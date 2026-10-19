"""REAPER-AI iterative LangGraph tool agent with persisted recovery points."""

import argparse
import json
import os
import sys
import time
from contextvars import ContextVar
from pathlib import Path
from typing import Any, TypedDict

import httpx
from langgraph.graph import END, START, StateGraph

from reaper_ai.agent import AgentState, dispatch_tool, new_agent_state
from reaper_ai.persistence import StorageError, Store

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
MODEL = os.environ.get("REAPER_MODEL", "qwen3.6:35b")
MAX_WALL_SECONDS = 600
_MAX_REPLY_CHARS = 80_000
_MODEL_PROTOCOL_OVERHEAD_BYTES = 512
_RUN = ContextVar("reaper_run_metrics", default=None)


def _configure_utf8_stdio() -> None:
    """Keep JSON events writable when Windows inherits a legacy code page."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (AttributeError, OSError):
            continue


_configure_utf8_stdio()


def _emit_event(kind: str, **payload: Any) -> None:
    """Write one machine-readable event without losing an agent run to encoding."""
    event = {"type": kind, "timestamp": time.time(), **payload}
    try:
        print("REAPER_EVENT: " + json.dumps(event, ensure_ascii=False), flush=True)
    except UnicodeEncodeError:
        print("REAPER_EVENT: " + json.dumps(event, ensure_ascii=True), flush=True)


class RunBudgetExceeded(RuntimeError):
    pass


def _budget() -> dict[str, Any] | None:
    metrics = _RUN.get()
    if metrics is None:
        return None
    if time.monotonic() >= metrics["deadline"]:
        raise RunBudgetExceeded("Wall-time budget exhausted")
    return metrics


def _model_context_budget() -> int:
    """Keep each planner prompt within the configured local-model context."""
    try:
        configured = int(os.environ.get("REAPER_MODEL_CONTEXT_CHARS", "36000"))
    except ValueError:
        configured = 36_000
    return max(8_000, min(configured, 96_000))


class AgentRunState(TypedDict):
    """LangGraph state for the bounded, tool-driven agent path."""

    workspace: str
    task: str
    agent: AgentState
    status: str
    error: str
    details: list[dict[str, Any]]
    last_result: dict[str, Any] | None


def ask_json(system: str, user: str) -> dict[str, Any]:
    metrics = _budget()
    timeout = 300.0
    if metrics is not None:
        metrics["model_calls"] += 1
        timeout = max(0.1, min(timeout, metrics["deadline"] - time.monotonic()))
    _emit_event("model_activity", status="started", model=MODEL)
    with httpx.Client(timeout=timeout) as client:
        response = client.post(OLLAMA_URL, json={
            "model": MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "format": "json", "stream": False, "think": False,
            "options": {"num_ctx": 16_384, "temperature": 0},
        })
        response.raise_for_status()
        payload = response.json()
    if metrics is not None:
        metrics["input_tokens"] += payload.get("prompt_eval_count") or 0
        metrics["output_tokens"] += payload.get("eval_count") or 0
    _emit_event("model_activity", status="completed", model=MODEL,
                input_tokens=payload.get("prompt_eval_count") or 0,
                output_tokens=payload.get("eval_count") or 0)
    raw = payload["message"]["content"]
    if len(raw) > _MAX_REPLY_CHARS:
        raise ValueError("Model output exceeds 80,000 characters")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("Model response must be a JSON object")
    return result


_AGENT_DECISION_SYSTEM = """You are Sentinel, an autonomous local work agent. Return JSON only; never expose chain-of-thought.

Return exactly one decision:
{"kind":"action","reason":"short next-step rationale","action":{"tool":"supported tool","args":{}},
 "plan":[{"step":"short current or next purpose"}],"open_verifications":["required proof"],
 "verification_for":["proof established by this action"]}
or
{"kind":"final","outcome":"completed or partial","reason":"short completion or blockage rationale","summary":"concise evidence-based result",
 "verification":"what observed tool results establish"}
`plan`, `open_verifications`, and `verification_for` are optional. `action.tool` must be supported and `action.args`
must be an object. Use `reason` only for a short decision rationale, not private reasoning.

Work method for every decision:
1. Determine the user goal, current subgoal, success criteria, constraints, and project conventions from task, plan,
   progress, verification, findings, and observations.
2. Prefer already known facts. `code_findings` contain source previously read; do not reread an unchanged range unless
   new evidence makes it necessary. Inspect only information needed for the next useful action. Stop analysis once it
   is sufficient to implement or verify the current subgoal.
3. Choose the action with the clearest contribution to the remaining work. For a development request, move from focused
   inspection to a concrete, minimal change, then to a proportionate verification; planning and reading are means, not
   an end. Keep the existing plan when it remains valid; update it only when evidence changes the work.
4. Treat each tool result as the factual outcome. A technically successful call is not by itself task progress. After an
   error or an ineffective action, use its result to change strategy and correct the cause rather than repeat it without
   a new reason. Do not claim actions, tests, or facts without supporting tool results.
5. Track explicit verification work in `open_verifications`. A write proves that a write succeeded, not that the new
   behavior is correct: after a change, name the required independent check as an open point. When selecting the tool
   action that passes such a check, copy the exact matching open-point string into `verification_for`; that field closes
   it after the successful result. Do not overwrite a still-needed open point, and do not finalize while one remains.
6. Return `kind: final` only with successful tool evidence. Use `outcome: "completed"` when observed results sufficiently
   satisfy the user goal. Use `outcome: "partial"` when observed evidence establishes a concrete blocker or incomplete
   result; state the remaining work truthfully and do not describe it as success.

Supported tools: list_files(path?, max_results?), search_text(query, path?, max_results?, case_sensitive?),
read_file(path, max_chars?), read_file_range(path, start_line, end_line, max_chars?), git_status, git_diff,
find_python, shell(command, timeout?), write_file(path, content, expected_hash?). Paths may be workspace-relative or
absolute. Git is optional. Shell commands and writes use the started user's normal operating-system permissions.
Before writing an existing text file, read it and pass that result's sha256 as expected_hash. Use only the supported
tools, respect tool errors and path rules, and never commit, push, or release automatically."""


def _short_text(value: Any, limit: int = 500) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= limit else text[:limit] + "..."


def _string_list(value: Any) -> list[str] | None:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    return None


def _progress_snapshot(agent: dict[str, Any]) -> dict[str, Any]:
    """Compact factual progress, separate from source findings and call history."""
    progress = agent.get("progress") if isinstance(agent.get("progress"), dict) else {}
    findings = progress.get("findings") if isinstance(progress.get("findings"), list) else []
    return {
        "last_change": progress.get("last_change"),
        "checks": list(progress.get("checks", []))[-12:],
        "open_verifications": list(progress.get("open_verifications", [])),
        "errors": list(progress.get("errors", []))[-12:],
        "last_reason": _short_text(progress.get("last_reason", "")),
        "finding_locations": [{key: item.get(key) for key in ("path", "start_line", "end_line", "sha256")}
                              for item in findings if isinstance(item, dict)],
    }


def _record_progress(agent: dict[str, Any], observation: dict[str, Any]) -> None:
    """Persist checks plus read-source evidence without suppressing any action."""
    progress = dict(agent.get("progress") or {})
    action = observation.get("action") if isinstance(observation.get("action"), dict) else {}
    result = observation.get("result") if isinstance(observation.get("result"), dict) else {}
    ok = bool(result.get("ok"))
    progress["checks"] = [*progress.get("checks", []), {
        "step": observation.get("step"), "tool": action.get("tool"), "ok": ok,
        "exit_code": result.get("exit_code"), "summary": _short_text(result.get("error") if not ok else "tool completed"),
    }][-12:]
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    if ok and action.get("tool") in {"read_file", "read_file_range"} and isinstance(data.get("content"), str) and data["content"]:
        finding = {key: data.get(key) for key in ("path", "start_line", "end_line", "sha256", "content")}
        if action.get("tool") == "read_file_range":
            finding["end_line"] = data.get("captured_end_line", data.get("last_line", finding["end_line"]))
        if isinstance(finding["path"], str):
            key = (finding["path"], finding["start_line"], finding["end_line"])
            findings = list(progress.get("findings", []))
            for index, previous in enumerate(findings):
                if isinstance(previous, dict) and (previous.get("path"), previous.get("start_line"),
                                                   previous.get("end_line")) == key:
                    findings[index] = finding
                    break
            else:
                findings.append(finding)
            progress["findings"] = findings
    changes = result.get("changes")
    if isinstance(changes, list):
        paths = [item.get("path") for item in changes if isinstance(item, dict) and isinstance(item.get("path"), str)]
        if paths:
            progress["last_change"] = {"step": observation.get("step"), "tool": action.get("tool"), "paths": paths}
    if not ok:
        progress["errors"] = [*progress.get("errors", []), {"step": observation.get("step"),
                              "tool": action.get("tool"), "error": _short_text(result.get("error", "Tool failed"))}][-12:]
    elif action.get("tool") not in {"read_file", "read_file_range"}:
        resolved = _string_list(action.get("verification_for"))
        if resolved:
            progress["open_verifications"] = [item for item in progress.get("open_verifications", []) if item not in resolved]
    agent["progress"] = progress


def _decision_error(agent: dict[str, Any], error: str) -> dict[str, Any]:
    observation = {"step": agent["current_step"] + 1, "action": {"tool": "model_decision", "args": {}},
                   "result": {"tool": "model_decision", "ok": False, "exit_code": None, "stdout": "", "stderr": "",
                              "data": {}, "changes": [], "error": error, "duration_ms": 0}}
    agent["observations"] = [*agent["observations"], observation]
    agent["current_step"] += 1
    agent["next_action"] = None
    _record_progress(agent, observation)
    _emit_event("agent_decision", decision_kind="invalid", tool="model_decision", reason=_short_text(error),
                progress=_progress_snapshot(agent), verification_status=agent["verification"].get("status"))
    return {"agent": agent, "status": "agent_decision_error", "error": "", "details": agent["observations"],
            "last_result": None}


def _agent_prompt(state: AgentRunState) -> str:
    """Prioritize retained source findings over disposable tool-call history."""
    agent = state["agent"]
    budget = _model_context_budget() - len(_AGENT_DECISION_SYSTEM.encode("utf-8")) - _MODEL_PROTOCOL_OVERHEAD_BYTES
    prompt = {"task": state["task"], "workspace": state["workspace"],
              "path_rule": "Paths may be workspace-relative or absolute.",
              "plan": agent["plan"], "current_step": agent["current_step"],
              "progress": _progress_snapshot(agent), "verification": agent["verification"],
              "code_findings": [], "previous_observations": []}
    used = len(json.dumps(prompt, ensure_ascii=False).encode("utf-8"))
    findings = [finding for finding in agent.get("progress", {}).get("findings", [])
                if isinstance(finding, dict) and isinstance(finding.get("content"), str) and finding["content"]]
    selected: list[dict[str, Any]] = []
    for finding in reversed(findings):
        path, digest = finding.get("path"), finding.get("sha256")
        start, end = finding.get("start_line"), finding.get("end_line")
        def overlaps(other: dict[str, Any]) -> bool:
            if (other.get("path"), other.get("sha256")) != (path, digest):
                return False
            other_start, other_end = other.get("start_line"), other.get("end_line")
            return start is None or end is None or other_start is None or other_end is None or start <= other_end and other_start <= end
        if not any(overlaps(other) for other in selected):
            selected.append(finding)
    findings = list(reversed(selected))
    retained: list[dict[str, Any]] = []
    if findings:
        headers = []
        for finding in findings:
            header = {key: finding.get(key) for key in ("path", "start_line", "end_line", "sha256")}
            if isinstance(header["start_line"], int) and isinstance(header["end_line"], int):
                content = finding["content"]
                observed_end = (header["start_line"] + content.count("\n") - 1
                                if content.endswith("\n") else header["start_line"] + content.count("\n"))
                header["end_line"] = min(header["end_line"], observed_end)
            headers.append(header)
        header_bytes = sum(len(json.dumps({**header, "content": ""}, ensure_ascii=False).encode("utf-8"))
                           for header in headers)
        available = max(0, budget - used - header_bytes)
        source_sizes = [len(finding["content"].encode("utf-8")) for finding in findings]
        shares = [min(size, available // len(findings)) for size in source_sizes]
        available -= sum(shares)
        while available:
            expandable = [index for index, size in enumerate(source_sizes) if shares[index] < size]
            if not expandable:
                break
            share = max(1, available // len(expandable))
            for index in expandable:
                added = min(source_sizes[index] - shares[index], share, available)
                shares[index] += added
                available -= added
        for finding, header, share in zip(findings, headers, shares):
            item = {**header, "content": finding["content"].encode("utf-8")[:share].decode("utf-8", "ignore")}
            retained.append(item)
        while retained and used + sum(len(json.dumps(item, ensure_ascii=False).encode("utf-8")) for item in retained) > budget:
            overflow = used + sum(len(json.dumps(value, ensure_ascii=False).encode("utf-8")) for value in retained) - budget
            expandable = [item for item in retained if item["content"]]
            if not expandable:
                retained.pop()
                continue
            reduction = max(1, (overflow + len(expandable) - 1) // len(expandable))
            for item in expandable:
                content = item["content"].encode("utf-8")
                item["content"] = content[:max(0, len(content) - reduction)].decode("utf-8", "ignore")
    prompt["code_findings"] = retained
    used += sum(len(json.dumps(item, ensure_ascii=False).encode("utf-8")) for item in retained)
    chosen: list[dict[str, Any]] = []
    for observation in reversed(agent["observations"]):
        action = observation.get("action") if isinstance(observation.get("action"), dict) else {}
        result = observation.get("result") if isinstance(observation.get("result"), dict) else {}
        if action.get("tool") in {"read_file", "read_file_range"}:
            continue
        item = {"step": observation.get("step"), "tool": action.get("tool"), "ok": bool(result.get("ok"))}
        if not result.get("ok"):
            item["error"] = _short_text(result.get("error", "Tool failed"))
        elif action.get("tool") == "write_file":
            item["changes"] = result.get("changes", [])
        else:
            item["data"] = result.get("data", {})
            if result.get("stdout"):
                item["stdout"] = _short_text(result["stdout"], max(1_024, budget // 12))
        encoded = json.dumps(item, ensure_ascii=False).encode("utf-8")
        if used + len(encoded) > budget:
            continue
        chosen.append(item)
        used += len(encoded)
    prompt["previous_observations"] = list(reversed(chosen))
    return json.dumps(prompt, ensure_ascii=False)


def _agent_action(decision: dict[str, Any], workspace: str) -> dict[str, Any] | None:
    action = decision.get("action")
    if not isinstance(action, dict) or not isinstance(action.get("tool"), str):
        return None
    args = action.get("args", {})
    if not isinstance(args, dict):
        return None
    if action["tool"] == "read_file_range":
        missing = [name for name in ("path", "start_line", "end_line") if name not in args]
        if missing:
            raise ValueError(f"read_file_range requires argument(s): {', '.join(missing)}")
        if not isinstance(args["path"], str) or not args["path"].strip():
            raise ValueError("read_file_range requires a non-empty string path")
        start_line, end_line = args["start_line"], args["end_line"]
        if (not isinstance(start_line, int) or isinstance(start_line, bool) or
                not isinstance(end_line, int) or isinstance(end_line, bool) or
                start_line < 1 or end_line < start_line):
            raise ValueError("read_file_range requires positive integer start_line/end_line with end_line >= start_line")
    context: dict[str, str] = {"workspace": workspace}
    requested_context = action.get("context")
    if isinstance(requested_context, dict) and isinstance(requested_context.get("cwd"), str):
        context["cwd"] = requested_context["cwd"]
    request: dict[str, Any] = {"tool": action["tool"], "args": args, "context": context}
    verification_for = _string_list(decision.get("verification_for"))
    if verification_for:
        request["verification_for"] = verification_for
    return request


def agent_prepare(state: AgentRunState) -> dict[str, Any]:
    _emit_event("phase_changed", phase="agent_prepare")
    return {"status": "agent_prepared", "error": "", "last_result": None}


def agent_decide(state: AgentRunState) -> dict[str, Any]:
    _emit_event("phase_changed", phase="agent_decide")
    agent = dict(state["agent"])
    agent["progress"] = dict(agent.get("progress") or {})
    agent["verification"] = dict(agent.get("verification") or {})
    try:
        decision = ask_json(_AGENT_DECISION_SYSTEM, _agent_prompt(state))
    except (RunBudgetExceeded, httpx.TimeoutException):
        raise
    except Exception as exc:
        return _decision_error(agent, f"Agent decision failed: {exc}")
    plan = decision.get("plan")
    if isinstance(plan, list) and plan and all(isinstance(item, dict) for item in plan):
        agent["plan"] = plan
    reason = _short_text(decision.get("reason", ""))
    agent["progress"]["last_reason"] = reason
    kind = decision.get("kind")
    if kind == "final":
        successful = any(observation.get("result", {}).get("ok") for observation in agent["observations"])
        if not successful:
            return _decision_error(agent, "Agent attempted final report without successful tool evidence")
        if agent["progress"].get("open_verifications"):
            return _decision_error(agent, "Agent attempted final report while explicit verification points remain open")
        outcome = decision.get("outcome", "completed")
        if outcome not in {"completed", "partial"}:
            return _decision_error(agent, "Agent final outcome must be completed or partial")
        verification = dict(agent["verification"])
        verification.update({"status": "ready", "summary": str(decision.get("summary", ""))[:4_000],
                             "model_assessment": str(decision.get("verification", ""))[:4_000],
                             "observed_tools": len(agent["observations"]), "outcome": outcome})
        agent["verification"] = verification
        agent["next_action"] = None
        _emit_event("agent_decision", decision_kind="final", tool="final", reason=reason,
                    progress=_progress_snapshot(agent), verification_status=verification["status"])
        return {"agent": agent, "status": "agent_finalizing", "error": ""}
    if kind != "action":
        return _decision_error(agent, "Agent decision must be an action or final report")
    open_points = _string_list(decision.get("open_verifications"))
    if open_points is not None:
        agent["progress"]["open_verifications"] = open_points
    try:
        action = _agent_action(decision, state["workspace"])
    except ValueError as exc:
        return _decision_error(agent, str(exc))
    if action is None:
        return _decision_error(agent, "Agent action requires a tool name and object arguments")
    agent["next_action"] = action
    _emit_event("agent_decision", decision_kind="action", tool=action["tool"], step=agent["current_step"] + 1,
                reason=reason, progress=_progress_snapshot(agent),
                verification_status=agent["verification"].get("status"))
    return {"agent": agent, "status": "agent_action_ready", "error": ""}


def agent_execute(state: AgentRunState) -> dict[str, Any]:
    _emit_event("phase_changed", phase="agent_execute")
    _budget()
    agent = dict(state["agent"])
    action = agent.get("next_action")
    if not isinstance(action, dict):
        return {"status": "agent_failed", "error": "Agent has no action to execute"}
    budgets = dict(agent["budgets"])
    _emit_event("tool_started", tool=action.get("tool"), action=action)
    result = dispatch_tool(action)
    budgets["tool_calls"] = budgets.get("tool_calls", 0) + 1
    agent["budgets"] = budgets
    _emit_event("tool_completed", tool=action.get("tool"), ok=result["ok"], exit_code=result["exit_code"],
                duration_ms=result["duration_ms"], error=result["error"])
    return {"agent": agent, "last_result": result, "status": "agent_executed", "error": ""}


def agent_observe(state: AgentRunState) -> dict[str, Any]:
    _emit_event("phase_changed", phase="agent_observe")
    agent = dict(state["agent"])
    action = agent.get("next_action")
    result = state.get("last_result")
    if not isinstance(action, dict) or not isinstance(result, dict):
        return {"status": "agent_failed", "error": "Agent observation lacks action or result"}
    observation = {"step": agent["current_step"] + 1, "action": action, "result": result}
    observations = [*agent["observations"], observation]
    agent["observations"] = observations
    agent["current_step"] += 1
    agent["next_action"] = None
    _record_progress(agent, observation)
    _emit_event("agent_observation", step=agent["current_step"], tool=action.get("tool"), ok=result.get("ok"))
    return {"agent": agent, "status": "agent_observed", "error": "", "details": observations,
            "last_result": None}


def agent_finalize(state: AgentRunState) -> dict[str, Any]:
    _emit_event("phase_changed", phase="agent_finalize")
    agent = dict(state["agent"])
    verification = dict(agent["verification"])
    verification["status"] = "observed"
    agent["verification"] = verification
    _emit_event("verification_completed", status="agent_completed", checks=len(agent["observations"]))
    return {"agent": agent, "status": "agent_completed", "error": "", "details": agent["observations"]}


def _agent_checkpoint_next_stage(stage: str, state: dict[str, Any]) -> str | None:
    status = state.get("status")
    if stage == "agent_prepare" and status == "agent_prepared":
        return "agent_decide"
    if stage == "agent_decide" and status == "agent_action_ready":
        return "agent_execute"
    if stage == "agent_decide" and status == "agent_decision_error":
        return "agent_decide"
    if stage == "agent_execute" and status == "agent_executed":
        return "agent_observe"
    if stage == "agent_observe" and status == "agent_observed":
        return "agent_decide"
    return None


def _agent_checkpointed(stage: str, function):
    """Persist safe agent decision boundaries in the existing SQLite store."""
    def wrapped(state: AgentRunState) -> dict[str, Any]:
        update = function(state)
        run_id = os.environ.get("REAPER_API_RUN_ID")
        next_stage = _agent_checkpoint_next_stage(stage, {**state, **update})
        if not run_id or next_stage is None:
            return update
        try:
            Store().save_checkpoint(run_id, stage=stage, next_stage=next_stage,
                                    state={**state, **update}, stage_result=update)
            _emit_event("checkpoint_saved", stage=stage, next_stage=next_stage)
        except StorageError as exc:
            return {"status": "agent_failed", "error": f"Checkpoint could not be saved: {exc}"}
        return update
    return wrapped


def _route_agent_decision(state: AgentRunState) -> str:
    return {"agent_action_ready": "execute", "agent_finalizing": "finalize",
            "agent_decision_error": "decide"}.get(state["status"], "end")


def _route_agent_execute(state: AgentRunState) -> str:
    return "observe" if state["status"] == "agent_executed" else "end"


def _route_agent_observe(state: AgentRunState) -> str:
    return "decide" if state["status"] == "agent_observed" else "end"


def build_agent_graph(start_stage: str = "agent_prepare"):
    nodes = {"agent_prepare": agent_prepare, "agent_decide": agent_decide,
             "agent_execute": agent_execute, "agent_observe": agent_observe,
             "agent_finalize": agent_finalize}
    if start_stage not in nodes:
        raise ValueError(f"Unsupported agent checkpoint stage: {start_stage}")
    graph = StateGraph(AgentRunState)
    for name, function in nodes.items():
        graph.add_node(name, _agent_checkpointed(name, function))
    graph.add_edge(START, start_stage)
    graph.add_edge("agent_prepare", "agent_decide")
    graph.add_conditional_edges("agent_decide", _route_agent_decision,
                                {"execute": "agent_execute", "finalize": "agent_finalize",
                                 "decide": "agent_decide", "end": END})
    graph.add_conditional_edges("agent_execute", _route_agent_execute,
                                {"observe": "agent_observe", "end": END})
    graph.add_conditional_edges("agent_observe", _route_agent_observe,
                                {"decide": "agent_decide", "end": END})
    graph.add_edge("agent_finalize", END)
    return graph.compile()


def _resume_state(workspace: str) -> tuple[dict[str, Any], str]:
    run_id = os.environ.get("REAPER_API_RUN_ID")
    if not run_id:
        raise ValueError("Recovery run has no API run ID")
    checkpoint = Store().get_checkpoint(run_id)
    if checkpoint is None:
        raise ValueError("No checkpoint is available for this run")
    if not checkpoint.get("valid"):
        raise ValueError("Checkpoint is not recoverable: " + str(checkpoint.get("invalid_reason") or "invalid"))
    state = checkpoint.get("state")
    next_stage = checkpoint.get("next_stage")
    if not isinstance(state, dict) or not isinstance(next_stage, str):
        raise ValueError("Checkpoint is incomplete")
    root = Path(workspace).resolve()
    if state.get("workspace") != str(root):
        raise ValueError("Checkpoint workspace does not match the requested repository")
    if not next_stage.startswith("agent_"):
        raise ValueError("Legacy static-pipeline checkpoints are not supported")
    _emit_event("recovery_started", run_id=run_id, stage=checkpoint["stage"], next_stage=next_stage)
    return state, next_stage


def _complete(result: dict[str, Any], run_id: str | None) -> dict[str, Any]:
    result["resumable"] = False
    if run_id:
        result["run_id"] = run_id
    _emit_event("run_completed", run_id=run_id, status=result.get("status", "failed"), result=result)
    return result


def run(task: str, workspace: str) -> dict[str, Any]:
    """Run the sole iterative-agent execution path."""
    root = Path(workspace).resolve()
    run_id = os.environ.get("REAPER_API_RUN_ID")
    _emit_event("run_started", run_id=run_id, workspace=str(root))
    started = time.monotonic()
    metrics = {"deadline": started + MAX_WALL_SECONDS,
               "model_calls": 0, "input_tokens": 0, "output_tokens": 0}
    token = _RUN.set(metrics)
    try:
        if os.environ.get("REAPER_RESUME") == "1":
            initial, start_stage = _resume_state(str(root))
        else:
            start_stage = "agent_prepare"
            initial = {"workspace": str(root), "task": task, "agent": new_agent_state(),
                       "status": "pending", "error": "", "details": [], "last_result": None}
        outcome = build_agent_graph(start_stage).invoke(
            initial, config={"recursion_limit": max(1_000, MAX_WALL_SECONDS * 20)}
        )
        internal = outcome["status"]
        final_outcome = outcome.get("agent", {}).get("verification", {}).get("outcome", "completed")
        status = final_outcome if internal == "agent_completed" else "failed"
        error = outcome.get("error", "")
        if "budget exhausted" in error.lower():
            status = "timed_out"
        result = {
            "status": status,
            "verification_level": "observed_tools" if internal == "agent_completed" else "none",
            "changed_files": [change["path"] for observation in outcome.get("details", [])
                              for change in observation.get("result", {}).get("changes", [])
                              if isinstance(change, dict) and isinstance(change.get("path"), str)],
            "checks": outcome.get("details", []), "reason": error, "internal_status": internal,
            "usage": {"model_calls": metrics["model_calls"], "input_tokens": metrics["input_tokens"],
                      "output_tokens": metrics["output_tokens"],
                      "elapsed_seconds": round(time.monotonic() - started, 1)},
            "limitations": ["Tool output is evidence; functional verification requires tools chosen by the agent",
                            "Cancellation requires the process supervisor"],
        }
        verification = outcome.get("agent", {}).get("verification", {})
        result["progress"] = _progress_snapshot(outcome.get("agent", {}))
        result["summary"] = verification.get("summary", "")
        result["tool_calls"] = outcome.get("agent", {}).get("budgets", {}).get("tool_calls", 0)
        return _complete(result, run_id)
    except (RunBudgetExceeded, httpx.TimeoutException) as exc:
        return _complete({"status": "timed_out", "reason": str(exc), "changed_files": [], "checks": [],
                          "usage": {"model_calls": metrics["model_calls"],
                          "input_tokens": metrics["input_tokens"], "output_tokens": metrics["output_tokens"],
                          "elapsed_seconds": round(time.monotonic() - started, 1)}}, run_id)
    except Exception as exc:
        return _complete({"status": "failed", "reason": str(exc), "changed_files": [], "checks": [],
                          "usage": {"model_calls": metrics["model_calls"],
                          "input_tokens": metrics["input_tokens"], "output_tokens": metrics["output_tokens"],
                          "elapsed_seconds": round(time.monotonic() - started, 1)}}, run_id)
    finally:
        _RUN.reset(token)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--task", required=True)
    args = parser.parse_args()
    result = run(args.task, args.workspace)
    print("RUN_RESULT:", json.dumps(result, ensure_ascii=False))
    if result["status"] != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()

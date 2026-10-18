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


_AGENT_DECISION_SYSTEM = """You are Sentinel's next-action planner. Return JSON only.
Choose exactly one of:
{"kind":"action","plan":[{"step":"short purpose"}],"action":{"tool":"supported tool","args":{...}}}
{"kind":"final","summary":"concise evidence-based answer","verification":"what the observed tool results establish"}

Supported tools: list_files(path?, max_results?), search_text(query, path?, max_results?, case_sensitive?),
read_file(path, max_chars?), read_file_range(path, start_line, end_line, max_chars?), git_status, git_diff,
find_python, shell(command, timeout?), write_file(path, content, expected_hash?). Use focused reads before edits.
Paths may be relative to the project workspace or absolute. Shell commands and writes run with the started user's
normal operating-system permissions; do not claim a fact without a tool result and do not claim an action succeeded
unless its result says so. Before writing an existing text file, read it and pass its returned sha256 as expected_hash.
Git is optional. Use errors returned by tools to correct your next action."""


def _agent_prompt(state: AgentRunState) -> str:
    """Give the model a compact, chronological observation window."""
    agent = state["agent"]
    budget = _model_context_budget() - len(_AGENT_DECISION_SYSTEM.encode("utf-8")) - _MODEL_PROTOCOL_OVERHEAD_BYTES
    prompt = {"task": state["task"], "workspace": state["workspace"],
              "path_rule": "Paths may be workspace-relative or absolute.",
              "plan": agent["plan"], "current_step": agent["current_step"], "previous_observations": []}
    used = len(json.dumps(prompt, ensure_ascii=False).encode("utf-8"))
    chosen: list[dict[str, Any]] = []
    for observation in reversed(agent["observations"]):
        encoded = json.dumps(observation, ensure_ascii=False).encode("utf-8")
        if used + len(encoded) > budget:
            continue
        chosen.append(observation)
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
    context: dict[str, str] = {"workspace": workspace}
    requested_context = action.get("context")
    if isinstance(requested_context, dict) and isinstance(requested_context.get("cwd"), str):
        context["cwd"] = requested_context["cwd"]
    return {"tool": action["tool"], "args": args, "context": context}


def agent_prepare(state: AgentRunState) -> dict[str, Any]:
    _emit_event("phase_changed", phase="agent_prepare")
    return {"status": "agent_prepared", "error": "", "last_result": None}


def agent_decide(state: AgentRunState) -> dict[str, Any]:
    _emit_event("phase_changed", phase="agent_decide")
    try:
        decision = ask_json(_AGENT_DECISION_SYSTEM, _agent_prompt(state))
    except (RunBudgetExceeded, httpx.TimeoutException):
        raise
    except Exception as exc:
        return {"status": "agent_failed", "error": f"Agent decision failed: {exc}"}
    agent = dict(state["agent"])
    plan = decision.get("plan")
    if isinstance(plan, list) and all(isinstance(item, dict) for item in plan):
        agent["plan"] = plan
    kind = decision.get("kind")
    if kind == "final":
        successful = any(observation.get("result", {}).get("ok") for observation in agent["observations"])
        if not successful:
            return {"agent": agent, "status": "agent_failed",
                    "error": "Agent attempted final report without successful tool evidence"}
        verification = dict(agent["verification"])
        verification.update({"status": "ready", "summary": str(decision.get("summary", ""))[:4_000],
                             "model_assessment": str(decision.get("verification", ""))[:4_000],
                             "observed_tools": len(agent["observations"])})
        agent["verification"] = verification
        agent["next_action"] = None
        return {"agent": agent, "status": "agent_finalizing", "error": ""}
    if kind != "action":
        return {"agent": agent, "status": "agent_failed",
                "error": "Agent decision must be an action or final report"}
    action = _agent_action(decision, state["workspace"])
    if action is None:
        return {"agent": agent, "status": "agent_failed",
                "error": "Agent action requires a tool name and object arguments"}
    agent["next_action"] = action
    _emit_event("agent_decision", tool=action["tool"], step=agent["current_step"] + 1)
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
    return {"agent_action_ready": "execute", "agent_finalizing": "finalize"}.get(state["status"], "end")


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
                                {"execute": "agent_execute", "finalize": "agent_finalize", "end": END})
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
        status = "completed" if internal == "agent_completed" else "failed"
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

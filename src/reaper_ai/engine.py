"""REAPER-AI iterative LangGraph tool agent with persisted recovery points."""

import argparse
import ast
import hashlib
import json
import os
import re
import time
import tomllib
from contextvars import ContextVar
import subprocess
from pathlib import Path
from typing import Any, TypedDict

import httpx
from langgraph.graph import END, START, StateGraph

from reaper_ai.agent import AgentState, dispatch_tool, new_agent_state
from reaper_ai.tools import _file
from reaper_ai.persistence import StorageError, Store

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
MODEL = os.environ.get("REAPER_MODEL", "qwen3.6:35b")
_READ_CHUNK_CHARS = 8_192
_BINARY_PROBE_BYTES = 8_192
MAX_WALL_SECONDS = 600
_MAX_REPLY_CHARS = 80_000
_MODEL_PROTOCOL_OVERHEAD_BYTES = 512
_RUN = ContextVar("reaper_run_metrics", default=None)

_PROPOSAL_SYSTEM = (
    "Implement only the needed changes in the provided source sections. "
    'Return JSON {"edits":[{"path":"relative.txt","start_line":1,"end_line":2,'
    '"start_offset":0,"end_offset":20,"source_digest":"sha256 from section",'
    '"expected":"exact existing text in that section",'
    '"replacement":"replacement text"}],"new_files":{}}. '
    "An edit range and source_digest must exactly match one provided section; expected must occur once within it. "
    "Use multiple non-overlapping edits for multiple changes. New files must be selected creation paths. "
    "Do not return full existing files, Markdown, or claims about tests."
)


# REAPER-AI A03 STRUCTURED EVENTS
# Dedicated machine-readable channel. Human-readable progress is not parsed.
def _emit_event(kind: str, **payload) -> None:
    event = {"type": kind, "timestamp": time.time(), **payload}
    print("REAPER_EVENT:", json.dumps(event, ensure_ascii=False), flush=True)


class RunBudgetExceeded(RuntimeError):
    pass


def _budget():
    metrics = _RUN.get()
    if metrics is None:
        return None
    if time.monotonic() >= metrics["deadline"]:
        raise RunBudgetExceeded("Wall-time budget exhausted")
    return metrics



class CodingState(TypedDict):
    workspace: str
    task: str
    # M6/P01 seed for the iterative agent.  The current stage graph does not
    # route through it yet, but checkpoint snapshots preserve it for P02.
    agent: AgentState
    inventory: list[dict]
    selected: list[str]
    creating: list[str]
    originals: dict[str, str]
    analysis: list[dict]
    edits: list[dict]
    changes: dict[str, str]
    status: str
    error: str
    details: list[dict]
    corrections: int
    feedback: str


class AgentRunState(TypedDict):
    """LangGraph state for the bounded, tool-driven agent path."""

    workspace: str
    task: str
    agent: AgentState
    status: str
    error: str
    details: list[dict[str, Any]]
    last_result: dict[str, Any] | None


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=20,
    )


def _is_python_path(path: str) -> bool:
    return Path(path).suffix.lower() == ".py"


def _validate_text_syntax(path: str, content: str) -> str:
    if _is_python_path(path):
        ast.parse(content, filename=path)
        return "ok"
    if Path(path).suffix.lower() == ".toml":
        tomllib.loads(content)
        return "ok"
    return "not_applicable"


def _file_kind(target: Path) -> str:
    """Classify without decoding a whole file during inventory."""
    with target.open("rb") as source:
        probe = source.read(_BINARY_PROBE_BYTES)
    if b"\0" in probe:
        return "binary"
    try:
        probe.decode("utf-8")
    except UnicodeDecodeError:
        return "binary"
    return "text"


def _read_text_sections(target: Path) -> str:
    """Load selected UTF-8 source on demand, in bounded read chunks."""
    sections: list[str] = []
    with target.open("r", encoding="utf-8") as source:
        while section := source.read(_READ_CHUNK_CHARS):
            sections.append(section)
    content = "".join(sections)
    if "\0" in content:
        raise ValueError(f"File contains NUL bytes and is not safe for UTF-8 text editing: {target.name}")
    return content


def _model_context_budget() -> int:
    """Configurable prompt limit, kept below the local model's context window."""
    try:
        configured = int(os.environ.get("REAPER_MODEL_CONTEXT_CHARS", "36000"))
    except ValueError:
        configured = 36_000
    return max(8_000, min(configured, 96_000))


def _line_offsets(content: str) -> list[int]:
    offsets = [0]
    for index, character in enumerate(content):
        if character == "\n":
            offsets.append(index + 1)
    return offsets


def _split_line_range(content: str, start_line: int, end_line: int, label: str) -> list[dict]:
    sections: list[dict] = []
    offsets = _line_offsets(content)
    line = start_line
    while line <= end_line:
        section_end = line
        start = offsets[line - 1]
        while section_end <= end_line:
            candidate_end = offsets[section_end] if section_end < len(offsets) else len(content)
            if section_end > line and candidate_end - start > _READ_CHUNK_CHARS:
                break
            section_end += 1
        actual_end = max(line, section_end - 1)
        end = offsets[actual_end] if actual_end < len(offsets) else len(content)
        # A generated/minified file can have one line larger than the normal
        # section size.  Keep it readable by cutting that one line by offsets.
        while start < end:
            section_end = min(start + _READ_CHUNK_CHARS, end)
            sections.append({"start_line": line, "end_line": actual_end,
                             "start_offset": start, "end_offset": section_end,
                             "label": label, "content": content[start:section_end]})
            start = section_end
        line = actual_end + 1
    return sections


def _source_sections(path: str, content: str) -> list[dict]:
    line_count = max(1, len(_line_offsets(content)))
    sections: list[dict] = []
    if not _is_python_path(path):
        return _split_line_range(content, 1, line_count, "file region")
    try:
        tree = ast.parse(content, filename=path)
    except SyntaxError:
        return _split_line_range(content, 1, line_count, "file region")
    cursor = 1
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        decorators = [decorator.lineno for decorator in node.decorator_list]
        start_line = min([node.lineno, *decorators])
        end_line = node.end_lineno or node.lineno
        if cursor < start_line:
            sections.extend(_split_line_range(content, cursor, start_line - 1, "file region"))
        label = ("class" if isinstance(node, ast.ClassDef) else "function") + " " + node.name
        sections.extend(_split_line_range(content, start_line, end_line, label))
        cursor = end_line + 1
    if cursor <= line_count:
        sections.extend(_split_line_range(content, cursor, line_count, "file region"))
    return sections


def _analysis_prompt(state: CodingState, sections: list[dict]) -> str:
    return (
        f"Task:\n{state['task']}\n\nCorrection guidance: {state.get('feedback', '')}\n"
        f"\nSelected existing files: {[path for path in state['selected'] if path not in state['creating']]}"
        f"\nNew files: {state['creating']}\n\nFocused source sections:\n"
        + json.dumps(sections, ensure_ascii=False)
    )


def _proposal_prompt_size(state: CodingState, sections: list[dict]) -> int:
    """Conservative UTF-8 size for both messages and their API framing."""
    return (len(_PROPOSAL_SYSTEM.encode("utf-8")) + len(_analysis_prompt(state, sections).encode("utf-8"))
            + _MODEL_PROTOCOL_OVERHEAD_BYTES)


def _analyze_selected_sources(state: CodingState) -> list[dict]:
    budget = _model_context_budget()
    terms = set(re.findall(r"[A-Za-z0-9_.-]+", state["task"].casefold()))
    candidates: list[dict] = []
    for path in state["selected"]:
        if path in state["creating"]:
            continue
        source_digest = code_digest(state["originals"][path])
        for section in _source_sections(path, state["originals"][path]):
            item = {"path": path, "source_digest": source_digest, **section}
            haystack = (path + " " + item["label"] + " " + item["content"]).casefold()
            item["_rank"] = (
                0 if Path(path).name.casefold() in terms or path.casefold() in terms else 1,
                0 if any(term in haystack for term in terms if len(term) > 2) else 1,
                0 if item["label"] != "file region" else 1,
                item["start_line"],
                item["start_offset"],
            )
            candidates.append(item)
    focused: list[dict] = []
    seen: set[tuple[str, int, int]] = set()
    prompt_size = _proposal_prompt_size(state, [])

    def compact(item: dict) -> dict:
        return {key: item[key] for key in
                ("path", "start_line", "end_line", "start_offset", "end_offset", "source_digest", "label", "content")}

    def add_if_fits(item: dict) -> bool:
        nonlocal prompt_size
        identity = (item["path"], item["start_offset"], item["end_offset"])
        if identity in seen:
            return False
        candidate = compact(item)
        encoded = json.dumps(candidate, ensure_ascii=False).encode("utf-8")
        added_size = len(encoded) + (2 if focused else 0)  # json.dumps inserts ", " between list items.
        if prompt_size + added_size > budget:
            return False
        seen.add(identity)
        focused.append(candidate)
        prompt_size += added_size
        return True

    # First give every selected file a focused foothold.  This prevents one
    # large, early path from consuming the entire context window.
    for path in state["selected"]:
        if path in state["creating"]:
            continue
        for item in sorted((item for item in candidates if item["path"] == path),
                           key=lambda candidate: candidate["_rank"]):
            if add_if_fits(item):
                break
    for item in sorted(candidates, key=lambda candidate: candidate["_rank"]):
        add_if_fits(item)
    if not focused and candidates:
        raise ValueError(f"No source section fits the {budget}-byte model context budget")
    return focused


def _selection_manifest(inventory: list[dict], task: str) -> tuple[list[dict], int]:
    """Keep full inventory in state while fitting a relevant manifest in model context."""
    task_terms = set(re.findall(r"[A-Za-z0-9_.-]+", task.casefold()))

    def rank(item: dict) -> tuple[int, int, str]:
        path = item["path"].casefold()
        name = Path(path).name
        exact = 0 if path in task_terms or name in task_terms else 1
        related = 0 if any(term in path for term in task_terms if len(term) > 2) else 1
        text = 0 if item["kind"] == "text" else 1
        return exact, related + text, path

    manifest: list[dict] = []
    used = 0
    budget = _model_context_budget()
    for item in sorted(inventory, key=rank):
        compact = {key: item[key] for key in ("path", "kind", "bytes")}
        size = len(json.dumps(compact, ensure_ascii=False)) + 1
        if used + size > budget:
            continue
        manifest.append(compact)
        used += size
    return manifest, len(inventory) - len(manifest)


def _selection_prompt(state: CodingState) -> str:
    manifest, omitted = _selection_manifest(state["inventory"], state["task"])
    note = ""
    if omitted:
        note = (
            f" The full inventory has {len(state['inventory'])} files; {omitted} less-relevant paths are omitted "
            "only from this model manifest. Explicitly named tracked paths remain selectable."
        )
    return f"Task:\n{state['task']}\n\nInventory manifest:{note}\n" + json.dumps(manifest, ensure_ascii=False)


def ask_json(system: str, user: str) -> dict:
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
    prefix = {"task": state["task"], "workspace": state["workspace"],
              "path_rule": "Paths may be workspace-relative or absolute.",
              "plan": agent["plan"], "current_step": agent["current_step"], "previous_observations": []}
    used = len(json.dumps(prefix, ensure_ascii=False).encode("utf-8"))
    chosen: list[dict] = []
    for observation in reversed(agent["observations"]):
        encoded = json.dumps(observation, ensure_ascii=False).encode("utf-8")
        if used + len(encoded) > budget:
            continue
        chosen.append(observation)
        used += len(encoded)
    prefix["previous_observations"] = list(reversed(chosen))
    return json.dumps(prefix, ensure_ascii=False)


def _agent_action(decision: dict, workspace: str) -> dict | None:
    action = decision.get("action")
    if not isinstance(action, dict) or not isinstance(action.get("tool"), str):
        return None
    args = action.get("args", {})
    if not isinstance(args, dict):
        return None
    # The workspace remains project context; absolute paths are intentionally
    # handled by the dispatcher under the launched user's OS permissions.
    context = {"workspace": workspace}
    requested_context = action.get("context")
    if isinstance(requested_context, dict) and isinstance(requested_context.get("cwd"), str):
        context["cwd"] = requested_context["cwd"]
    result = {"tool": action["tool"], "args": args, "context": context}
    return result


def agent_prepare(state: AgentRunState) -> dict:
    _emit_event("phase_changed", phase="agent_prepare")
    agent = dict(state["agent"])
    agent["project_context"] = {**agent["project_context"], "workspace": state["workspace"]}
    return {"agent": agent, "status": "agent_prepared", "error": "", "last_result": None}


def agent_decide(state: AgentRunState) -> dict:
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


def agent_execute(state: AgentRunState) -> dict:
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


def agent_observe(state: AgentRunState) -> dict:
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


def agent_finalize(state: AgentRunState) -> dict:
    _emit_event("phase_changed", phase="agent_finalize")
    agent = dict(state["agent"])
    verification = dict(agent["verification"])
    verification["status"] = "observed"
    agent["verification"] = verification
    _emit_event("verification_completed", status="agent_completed", checks=len(agent["observations"]))
    return {"agent": agent, "status": "agent_completed", "error": "", "details": agent["observations"]}


def _agent_checkpoint_next_stage(stage: str, state: dict) -> str | None:
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
    def wrapped(state: AgentRunState) -> dict:
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


def code_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def inventory(state: CodingState) -> dict:
    _emit_event("phase_changed", phase="inventory")
    print("[INVENTORY]")
    try:
        root = Path(state["workspace"]).resolve()
        candidates = []
        for target in sorted(root.rglob("*")):
            if not target.is_file():
                continue
            relative = target.relative_to(root).as_posix()
            candidates.append({"path": relative, "kind": _file_kind(target), "bytes": target.stat().st_size})
        if not candidates:
            raise ValueError("No regular project files")
        return {"inventory": candidates, "status": "inventoried", "error": ""}
    except (OSError, ValueError, UnicodeError, subprocess.TimeoutExpired, RunBudgetExceeded) as exc:
        return {"status": "failed", "error": str(exc)}


def select(state: CodingState) -> dict:
    _emit_event("phase_changed", phase="select")
    print("[SELECT]")
    try:
        result = ask_json(
            "Select existing UTF-8 text files to edit AND new UTF-8 text paths to create. "
            'Return JSON {"edit":["existing.txt"],"create":["new.txt"]}. '
            "Existing paths must come from inventory. "
            "Binary files are visible for analysis but must never be selected for text editing. "
            "Only request new files if needed. Never select a path both ways.",
            _selection_prompt(state),
        )
        paths, creating = result.get("edit"), result.get("create")
        if not isinstance(paths, list) or not isinstance(creating, list):
            raise ValueError("Invalid selection format")
        selected = paths + creating
        known = {item["path"]: item for item in state["inventory"]}
        if not selected:
            raise ValueError("File selection must contain at least one path")
        if any(not isinstance(x, str) for x in selected):
            raise ValueError("File selection paths must be strings")
        if len(set(selected)) != len(selected):
            raise ValueError("File selection contains duplicate paths")
        unknown = sorted(path for path in paths if path not in known)
        if unknown:
            raise ValueError("Selected existing paths are not in the inventory: " + ", ".join(unknown))
        binary = sorted(path for path in paths if known[path]["kind"] != "text")
        if binary:
            raise ValueError("Binary files cannot be edited as UTF-8 text: " + ", ".join(binary))
        root = Path(state["workspace"]).resolve()
        originals = {}
        for path in paths:
            target = _file(str(root), path)
            if not target.is_file():
                raise ValueError(f"File disappeared: {path}")
            originals[path] = _read_text_sections(target)
        for path in creating:
            target = _file(str(root), path)
            if target.exists() or path in known:
                raise ValueError(f"New path already exists: {path}")
        return {"selected": selected, "creating": creating, "originals": originals, "analysis": [], "edits": [],
                "status": "selected", "error": ""}
    except (OSError, ValueError, UnicodeError, httpx.HTTPError,
            subprocess.TimeoutExpired, json.JSONDecodeError, RunBudgetExceeded) as exc:
        return {"status": "failed", "error": str(exc)}


def analyze(state: CodingState) -> dict:
    _emit_event("phase_changed", phase="analyze")
    print("[ANALYZE]")
    try:
        return {"analysis": _analyze_selected_sources(state), "status": "analyzed", "error": ""}
    except (OSError, ValueError, UnicodeError, RunBudgetExceeded) as exc:
        return {"status": "failed", "error": str(exc)}


def _edit_position(source: str, edit: dict, available_ranges: set[tuple[str, int, int, int, int, str]]) -> int:
    required = {"path", "start_line", "end_line", "start_offset", "end_offset", "source_digest", "expected", "replacement"}
    if set(edit) != required:
        raise ValueError("Each edit must contain path, line/offset range, source digest, expected, and replacement")
    path, start_line, end_line, start_offset, end_offset, source_digest, expected, replacement = (
        edit["path"], edit["start_line"], edit["end_line"], edit["start_offset"], edit["end_offset"],
        edit["source_digest"], edit["expected"], edit["replacement"]
    )
    if (not isinstance(path, str) or not isinstance(start_line, int) or not isinstance(end_line, int)
            or not isinstance(start_offset, int) or not isinstance(end_offset, int)
            or not isinstance(source_digest, str)
            or not isinstance(expected, str) or not expected or not isinstance(replacement, str)):
        raise ValueError("Invalid edit value types or empty expected text")
    if (path, start_line, end_line, start_offset, end_offset, source_digest) not in available_ranges:
        raise ValueError(f"Edit range was not provided for {path}: lines {start_line}-{end_line}")
    if start_offset < 0 or end_offset < start_offset or end_offset > len(source):
        raise ValueError(f"Edit range is outside the current source for {path}: offsets {start_offset}-{end_offset}")
    section = source[start_offset:end_offset]
    positions: list[int] = []
    offset = 0
    while (position := section.find(expected, offset)) >= 0:
        positions.append(position)
        offset = position + 1
    if not positions:
        raise ValueError(f"Expected text is absent from the selected range for {path}: "
                         f"lines {start_line}-{end_line}, offsets {start_offset}-{end_offset}")
    if len(positions) > 1:
        raise ValueError(f"Expected text occurs {len(positions)} times in the selected range for {path}: "
                         f"lines {start_line}-{end_line}; choose a narrower range")
    if code_digest(source) != source_digest:
        raise ValueError(f"Source digest changed before write for {path}; no file was overwritten")
    return start_offset + positions[0]


def _changes_from_edits(state: CodingState, edits: list[dict]) -> dict[str, str]:
    available_ranges = {(item["path"], item["start_line"], item["end_line"],
                         item["start_offset"], item["end_offset"], item["source_digest"])
                        for item in state["analysis"]}
    grouped: dict[str, list[tuple[int, dict]]] = {}
    for edit in edits:
        path = edit.get("path") if isinstance(edit, dict) else None
        if path not in state["originals"]:
            raise ValueError(f"Edit targets an unselected or new file: {path}")
        grouped.setdefault(path, []).append((_edit_position(state["originals"][path], edit, available_ranges), edit))
    changes: dict[str, str] = {}
    for path, path_edits in grouped.items():
        intervals = sorted((edit["start_offset"], edit["end_offset"]) for _, edit in path_edits)
        if any(current[0] <= previous[1] for previous, current in zip(intervals, intervals[1:])):
            raise ValueError(f"Overlapping edit ranges for {path}")
        updated = state["originals"][path]
        for position, edit in sorted(path_edits, key=lambda item: item[0], reverse=True):
            expected = edit["expected"]
            updated = updated[:position] + edit["replacement"] + updated[position + len(expected):]
        changes[path] = updated
    return changes


def propose(state: CodingState) -> dict:
    _emit_event("phase_changed", phase="propose")
    print("[PROPOSE]")
    try:
        sections = state.get("analysis") or _analyze_selected_sources(state)
        if any("source_digest" not in section for section in sections):
            # A pre-BUG-008 checkpoint cannot prove positional source identity.
            sections = _analyze_selected_sources(state)
        result = ask_json(_PROPOSAL_SYSTEM, _analysis_prompt(state, sections))
        edits, new_files = result.get("edits"), result.get("new_files", {})
        if not isinstance(edits, list) or not isinstance(new_files, dict):
            raise ValueError("Proposal must contain an edits list and new_files object")
        if not edits and not new_files:
            raise ValueError("Proposal must contain at least one concrete operation")
        if set(new_files) != set(state["creating"]):
            raise ValueError("Proposed new-file set differs from selected creation paths")
        changes = _changes_from_edits({**state, "analysis": sections}, edits)
        changes.update(new_files)
        for name, content in changes.items():
            if not isinstance(content, str) or (name in new_files and not content.strip()):
                raise ValueError(f"Invalid proposed content: {name}")
            _validate_text_syntax(name, content)
        changes = {name: content for name, content in changes.items()
                   if name in state["creating"] or (
                       ast.dump(ast.parse(content), include_attributes=False)
                       != ast.dump(ast.parse(state["originals"][name]), include_attributes=False)
                       if _is_python_path(name) else content != state["originals"][name]
                   )}
        if not changes:
            return {"status": "no_semantic_change", "error": "No meaningful selected-file change; task completion not proven"}
        return {"analysis": sections, "edits": edits, "changes": changes, "status": "proposed", "error": ""}
    except (ValueError, SyntaxError, httpx.HTTPError, json.JSONDecodeError, RunBudgetExceeded) as exc:
        return {"status": "proposal_failed", "error": str(exc)}


def apply(state: CodingState) -> dict:
    _emit_event("phase_changed", phase="apply")
    print("[APPLY]")
    root = Path(state["workspace"]).resolve()
    written = []
    try:
        # Preflight every file before any write.
        for name in state["changes"]:
            target = _file(str(root), name)
            if name in state["creating"]:
                if target.exists():
                    raise ValueError(f"New file appeared during proposal: {name}")
            else:
                if not target.is_file():
                    raise ValueError(f"Source disappeared during proposal: {name}")
                current = _read_text_sections(target)
                file_edits = [edit for edit in state.get("edits", [])
                              if isinstance(edit, dict) and edit.get("path") == name]
                if file_edits and all("source_digest" in edit for edit in file_edits):
                    _changes_from_edits({**state, "originals": {**state["originals"], name: current}},
                                        file_edits)
                if code_digest(current) != code_digest(state["originals"][name]) or current != state["originals"][name]:
                    raise ValueError(f"Source digest changed before write for {name}; no file was overwritten")
        for name, content in state["changes"].items():
            target = _file(str(root), name)
            if name in state["creating"]:
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("x", encoding="utf-8", newline="") as f:
                    f.write(content)
            else:
                target.write_text(content, encoding="utf-8", newline="")
            written.append(name)
        _emit_event("files_changed", files=written)
        return {"status": "written", "error": ""}
    except (OSError, ValueError, subprocess.TimeoutExpired, RunBudgetExceeded) as exc:
        # Best-effort rollback; never clobber concurrent external changes.
        for name in reversed(written):
            target = _file(str(root), name)
            try:
                if _read_text_sections(target) == state["changes"][name]:
                    if name in state["creating"]:
                        target.unlink()
                    else:
                        target.write_text(state["originals"][name], encoding="utf-8", newline="")
            except OSError:
                pass
        return {"status": "failed", "error": f"Apply failed: {exc}"}


def _restore_written(state: CodingState) -> str:
    """Best-effort restoration only when file still equals our own proposal."""
    root = Path(state["workspace"]).resolve()
    errors = []
    for name, expected in reversed(list(state["changes"].items())):
        try:
            target = _file(str(root), name)
            if not target.is_file() or _read_text_sections(target) != expected:
                errors.append(f"{name}: concurrent change, preserved")
                continue
            if name in state["creating"]:
                target.unlink()
            else:
                target.write_text(state["originals"][name], encoding="utf-8", newline="")
        except OSError as exc:
            errors.append(f"{name}: {exc}")
    return "; ".join(errors)


def _check_local_imports(state: CodingState) -> None:
    """Check explicit imports FROM changed local modules; no user code execution."""
    root = Path(state["workspace"]).resolve()
    changes = state["changes"]
    for name, content in changes.items():
        if not _is_python_path(name):
            continue
        tree = ast.parse(content, filename=name)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.level or not node.module:
                continue
            module_path = node.module.replace(".", "/") + ".py"
            # Check only modules part of THIS change set, avoiding false
            # failure for dependencies outside the edited files.
            if module_path not in changes:
                continue
            module_tree = ast.parse(changes[module_path], filename=module_path)
            exports = {n.name for n in module_tree.body
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
            exports |= {name for n in module_tree.body if isinstance(n, (ast.Assign, ast.AnnAssign))
                        for name in ([t.id for t in n.targets if isinstance(t, ast.Name)] if isinstance(n, ast.Assign)
                                     else ([n.target.id] if isinstance(n.target, ast.Name) else []))}
            for alias in node.names:
                if alias.name != "*" and alias.name not in exports:
                    raise ValueError(f"{name}: {module_path} does not export {alias.name}")


def verify(state: CodingState) -> dict:
    _emit_event("phase_changed", phase="verify")
    print("[VERIFY]")
    root = Path(state["workspace"]).resolve()
    details = []
    try:
        for name, expected in state["changes"].items():
            actual = _read_text_sections(_file(str(root), name))
            if actual != expected:
                raise ValueError(f"Written content mismatch: {name}")
            syntax = _validate_text_syntax(name, actual)
            if name in state["creating"]:
                record = git(root, "status", "--porcelain", "--untracked-files=all", "--", name)
                if record.returncode or not record.stdout.startswith("?? "):
                    raise ValueError(f"New file not recognized by Git: {name}")
                proof = "untracked"
            else:
                diff = git(root, "--no-pager", "diff", "--no-ext-diff", "HEAD", "--", name)
                if diff.returncode or not diff.stdout.strip():
                    raise ValueError(f"Missing file-specific Git diff: {name}")
                proof = "diff"
            details.append({"path": name, "syntax": syntax, "git": proof})
            print(f"FILE: {name} | SYNTAX: {syntax.upper()} | GIT: {proof}")
        _check_local_imports(state)
        print("LOCAL IMPORTS: OK (changed files only)")
        _emit_event("verification_completed", status="structurally_verified", checks=details)
        return {"details": details, "status": "structurally_verified", "error": ""}
    except (OSError, ValueError, SyntaxError, subprocess.TimeoutExpired, RunBudgetExceeded) as exc:
        _emit_event("verification_completed", status="failed", reason=str(exc), checks=details)
        rollback_error = _restore_written(state)
        error = str(exc) + (f"; rollback incomplete: {rollback_error}" if rollback_error else "")
        return {"details": details, "status": "failed" if rollback_error else "verify_failed",
                "error": error}


def advance(state: CodingState) -> str:
    return "next" if state["status"] not in {"failed", "no_semantic_change", "proposal_failed"} else "end"


def retry_proposal(state: CodingState) -> str:
    return "repair" if state["status"] in {"proposal_failed", "verify_failed"} else "end"


def repair_proposal(state: CodingState) -> dict:
    _emit_event("phase_changed", phase="repair")
    print("[REPAIR PROPOSAL]")
    return {"corrections": state["corrections"] + 1,
            "feedback": state["error"], "status": "selected", "error": ""}


def _next_stage(stage: str, state: dict) -> str | None:
    status = state.get("status")
    if stage in {"inventory", "select", "analyze", "apply"}:
        return {"inventory": "select", "select": "analyze", "analyze": "propose", "apply": "verify"}.get(stage) \
            if status not in {"failed", "no_semantic_change", "proposal_failed"} else None
    if stage == "propose":
        if status == "proposed":
            return "apply"
        return "repair_proposal" if status == "proposal_failed" else None
    if stage == "verify":
        return "repair_proposal" if status == "verify_failed" else None
    return "propose" if stage == "repair_proposal" else None


def _checkpointed(stage: str, function):
    """Persist a complete state only after a node returned normally."""
    def wrapped(state: CodingState) -> dict:
        update = function(state)
        run_id = os.environ.get("REAPER_API_RUN_ID")
        next_stage = _next_stage(stage, {**state, **update})
        if not run_id or next_stage is None:
            return update
        try:
            Store().save_checkpoint(run_id, stage=stage, next_stage=next_stage,
                                    state={**state, **update}, stage_result=update)
            _emit_event("checkpoint_saved", stage=stage, next_stage=next_stage)
        except StorageError as exc:
            return {"status": "failed", "error": f"Checkpoint could not be saved: {exc}"}
        return update
    return wrapped


def build_graph(start_stage: str = "inventory"):
    if start_stage not in {"inventory", "select", "analyze", "propose", "repair_proposal", "apply", "verify"}:
        raise ValueError(f"Unsupported checkpoint stage: {start_stage}")
    graph = StateGraph(CodingState)
    for name, function in (("inventory", inventory), ("select", select),
                           ("analyze", analyze), ("propose", propose), ("repair_proposal", repair_proposal),
                           ("apply", apply), ("verify", verify)):
        graph.add_node(name, _checkpointed(name, function))
    graph.add_edge(START, start_stage)
    graph.add_conditional_edges("inventory", advance, {"next": "select", "end": END})
    graph.add_conditional_edges("select", advance, {"next": "analyze", "end": END})
    graph.add_conditional_edges("analyze", advance, {"next": "propose", "end": END})
    graph.add_conditional_edges("propose", lambda state: "next" if state["status"] == "proposed" else retry_proposal(state),
                                {"next": "apply", "repair": "repair_proposal", "end": END})
    graph.add_edge("repair_proposal", "propose")
    graph.add_conditional_edges("apply", advance, {"next": "verify", "end": END})
    graph.add_conditional_edges("verify", retry_proposal,
                                {"repair": "repair_proposal", "end": END})
    return graph.compile()


def _resume_state(workspace: str) -> tuple[dict, str]:
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
    if next_stage in {"analyze", "propose", "apply", "verify", "repair_proposal"}:
        originals = state.get("originals")
        changes = state.get("changes")
        creating = state.get("creating")
        if not isinstance(originals, dict) or not isinstance(changes, dict) or not isinstance(creating, list):
            raise ValueError("Checkpoint lacks the required stage state")
        # Before proposal/apply or after a rolled-back verification failure, inputs must be untouched.
        expect_originals = next_stage in {"propose", "apply", "repair_proposal"}
        for name, original in originals.items():
            target = _file(str(root), name)
            if not target.is_file() or (expect_originals and _read_text_sections(target) != original):
                raise ValueError(f"Checkpoint source no longer matches: {name}")
        for name in creating:
            target = _file(str(root), name)
            if expect_originals and target.exists():
                raise ValueError(f"Checkpoint creation path is no longer empty: {name}")
        if next_stage == "verify":
            for name, expected in changes.items():
                target = _file(str(root), name)
                if not target.is_file() or _read_text_sections(target) != expected:
                    raise ValueError(f"Checkpointed write no longer matches: {name}")
    _emit_event("recovery_started", run_id=run_id, stage=checkpoint["stage"], next_stage=next_stage)
    return state, next_stage


def run(task: str, workspace: str, *, agent_mode: bool | None = None) -> dict:
    """Run the sole iterative-agent execution path.

    ``agent_mode`` is accepted temporarily for call-site compatibility; it no
    longer selects a different orchestration path.
    """
    started = time.monotonic()
    metrics = {"deadline": started + MAX_WALL_SECONDS,
               "model_calls": 0, "input_tokens": 0, "output_tokens": 0}
    token = _RUN.set(metrics)
    try:
        if os.environ.get("REAPER_RESUME") == "1":
            initial, start_stage = _resume_state(workspace)
            if not start_stage.startswith("agent_"):
                raise ValueError("Legacy static-pipeline checkpoint cannot be resumed by the iterative agent")
        else:
            start_stage = "agent_prepare"
            agent = new_agent_state(task, workspace)
            agent["budgets"] = {**agent["budgets"], "max_wall_seconds": MAX_WALL_SECONDS}
            initial = {"workspace": str(Path(workspace).resolve()), "task": task, "agent": agent,
                       "status": "pending", "error": "", "details": [], "last_result": None}
        # LangGraph requires a recursion guard.  It is deliberately derived
        # from the wall-time safety budget rather than a per-run work limit.
        outcome = build_agent_graph(start_stage).invoke(
            initial, config={"recursion_limit": max(1_000, MAX_WALL_SECONDS * 20)}
        )
        internal = outcome["status"]
        if internal == "agent_completed":
            status = "completed"
        else:
            status = "failed"
        error = outcome.get("error", "")
        if "budget exhausted" in error.lower():
            status = "timed_out"
        result = {
            "status": status,
            "verification_level": "observed_tools" if internal == "agent_completed" else "none",
            "changed_files": [change["path"] for observation in outcome.get("details", [])
                              for change in observation.get("result", {}).get("changes", [])
                              if isinstance(change, dict) and isinstance(change.get("path"), str)],
            "checks": outcome.get("details", []),
            "reason": error,
            "internal_status": internal,
            "usage": {"model_calls": metrics["model_calls"],
                      "input_tokens": metrics["input_tokens"],
                      "output_tokens": metrics["output_tokens"],
                      "elapsed_seconds": round(time.monotonic() - started, 1)},
            "limitations": ["Tool output is evidence; functional verification requires tools chosen by the agent",
                            "Cancellation requires the process supervisor"],
        }
        verification = outcome.get("agent", {}).get("verification", {})
        result["summary"] = verification.get("summary", "")
        result["tool_calls"] = outcome.get("agent", {}).get("budgets", {}).get("tool_calls", 0)
        return result
    except (RunBudgetExceeded, httpx.TimeoutException) as exc:
        return {"status": "timed_out", "reason": str(exc), "changed_files": [],
                "checks": [], "usage": {"model_calls": metrics["model_calls"],
                "input_tokens": metrics["input_tokens"], "output_tokens": metrics["output_tokens"],
                "elapsed_seconds": round(time.monotonic() - started, 1)}}
    except Exception as exc:
        return {"status": "failed", "reason": str(exc), "changed_files": [],
                "checks": [], "usage": {"model_calls": metrics["model_calls"],
                "input_tokens": metrics["input_tokens"], "output_tokens": metrics["output_tokens"],
                "elapsed_seconds": round(time.monotonic() - started, 1)}}
    finally:
        _RUN.reset(token)



# RUN PROVENANCE START
# Audit-only state. Do not infer ownership or resume authority from this data.
def _git_snapshot(root: Path) -> tuple[str | None, list[str]]:
    try:
        head = git(root, "rev-parse", "HEAD")
        status = git(root, "status", "--porcelain", "--untracked-files=all")
        return (head.stdout.strip() if head.returncode == 0 else None,
                status.stdout.splitlines() if status.returncode == 0 else [])
    except (OSError, subprocess.TimeoutExpired):
        return None, []


_original_run = run


def _run_with_provenance(task: str, workspace: str, *, agent_mode: bool | None = None) -> dict:
    from uuid import uuid4
    root = Path(workspace).resolve()
    run_id = uuid4().hex[:12]
    head_before, status_before = _git_snapshot(root)
    print(f"RUN_ID: {run_id}", flush=True)
    _emit_event("run_started", run_id=run_id, workspace=str(root))
    result = _original_run(task, str(root), agent_mode=agent_mode)
    head_after, status_after = _git_snapshot(root)
    result["run_id"] = run_id
    result["git_baseline"] = head_before
    result["git_head_unchanged"] = head_before == head_after
    result["git_status_before"] = status_before
    result["git_status_after"] = status_after
    result["git_status_changed"] = status_before != status_after
    result["resumable"] = False  # Terminal results are never resumed automatically.
    _emit_event("run_completed", run_id=run_id, status=result.get("status", "failed"),
                result=result)
    return result


run = _run_with_provenance
# RUN PROVENANCE END


def main():
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

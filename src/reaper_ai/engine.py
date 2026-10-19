# Copyright (c) 2026 Robert Backhaus

"""REAPER-AI iterative LangGraph tool agent with persisted recovery points."""

import argparse
import ast
import hashlib
import json
import os
import re
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
_MAX_THINKING_CHARS = 12_000
_RUN = ContextVar("reaper_run_metrics", default=None)
_LAST_MODEL_RESPONSE = ContextVar("reaper_last_model_response", default=None)


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


def _ollama_options() -> dict[str, Any]:
    """Read the per-run model snapshot while retaining legacy defaults."""
    options: dict[str, Any] = {"temperature": 0.0, "top_p": 0.9, "top_k": 40, "num_ctx": 16_384}
    try:
        configured = json.loads(os.environ.get("REAPER_MODEL_OPTIONS", "{}"))
    except json.JSONDecodeError:
        configured = {}
    if isinstance(configured, dict):
        for key in options:
            if key in configured:
                options[key] = configured[key]
    return options


def _decision_system_prompt() -> str:
    """Keep Sentinel's technical contract intact while adding project guidance."""
    project_prompt = os.environ.get("REAPER_PROJECT_SYSTEM_PROMPT", "").strip()
    if not project_prompt:
        return _AGENT_DECISION_SYSTEM
    return _AGENT_DECISION_SYSTEM + "\n\nProject-specific instructions (supplemental; the technical contract above remains binding):\n" + project_prompt


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
            "options": _ollama_options(),
        })
        response.raise_for_status()
        payload = response.json()
    if metrics is not None:
        metrics["input_tokens"] += payload.get("prompt_eval_count") or 0
        metrics["output_tokens"] += payload.get("eval_count") or 0
    _emit_event("model_activity", status="completed", model=MODEL,
                input_tokens=payload.get("prompt_eval_count") or 0,
                output_tokens=payload.get("eval_count") or 0)
    message = payload["message"]
    raw = message["content"]
    thinking = message.get("thinking")
    if not isinstance(thinking, str):
        thinking = ""
    _LAST_MODEL_RESPONSE.set({
        "thinking_available": bool(thinking),
        "thinking": thinking[:_MAX_THINKING_CHARS],
        "thinking_truncated": len(thinking) > _MAX_THINKING_CHARS,
    })
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
 "verification_for":["proof established by this action"],"phase":"explore|plan|implement|verify",
 "current_task":"short concrete subgoal","decisions":["technical choice"],
 "information_gaps":["unresolved question"]}
or
{"kind":"final","outcome":"completed or partial","reason":"short completion or blockage rationale","summary":"concise evidence-based result",
 "verification":"what observed tool results establish"}
`plan`, `open_verifications`, and `verification_for` are optional. `action.tool` must be supported and `action.args`
must be an object. The work-memory fields are optional and incremental: provide only additions or changes; never repeat
the whole memory merely to restate it. `decisions` and `information_gaps` are model proposals, not proof of a file
change or a passed check. Use `reason` only for a short decision rationale, not private reasoning.

Work method for every decision:
1. Determine the user goal, current subgoal, success criteria, constraints, and project conventions from task, plan,
   progress, verification, confirmed facts, and observations.
2. Prefer already known facts. `working_memory.confirmed_facts` are tool-confirmed facts from reads, searches,
   index queries, changes, and checks; use them before repeating equivalent discovery. `information_gaps` states what
   tool evidence still does not establish. `code_findings` contain the corresponding source; do not reread an unchanged range unless
   new evidence makes it necessary. Inspect only information needed for the next useful action. Stop analysis once it
   is sufficient to implement or verify the current subgoal.
3. Choose the action with the clearest contribution to the remaining work. For a development request, move from focused
   inspection to a concrete, minimal change, then to a proportionate verification; planning and reading are means, not
   an end. Keep the existing plan when it remains valid; update it only when evidence changes the work.
4. Use the repository map for initial orientation. During `explore`, prefer index tools to broad scans: find files,
   symbols, or imports, then use `read_file_range` for the exact source needed. During `plan`, identify the affected
   functions and dependencies. During `implement`, make the concrete guarded change once sufficient evidence exists.
   During `verify`, run proportionate checks. A return to exploration needs a concrete missing fact. The engine records
   the reached phase from successful tool evidence; do not claim a later phase before the corresponding work is done.
5. Treat each tool result as the factual outcome. A technically successful call is not by itself task progress. After an
   error or an ineffective action, use its result to change strategy and correct the cause rather than repeat it without
   a new reason. Do not claim actions, tests, or facts without supporting tool results.
6. Track explicit verification work in `open_verifications`. A write proves that a write succeeded, not that the new
   behavior is correct: after a change, name the required independent check as an open point. When selecting the tool
   action that passes such a check, copy the exact matching open-point string into `verification_for`; that field closes
   it after the successful result. Do not overwrite a still-needed open point, and do not finalize while one remains.
7. Return `kind: final` only with successful tool evidence. Use `outcome: "completed"` when observed results sufficiently
   satisfy the user goal. Use `outcome: "partial"` when observed evidence establishes a concrete blocker or incomplete
   result; state the remaining work truthfully and do not describe it as success.

Supported tools: list_files(path?, max_results?), search_text(query, path? file-or-directory, max_results?, case_sensitive?),
read_file(path, max_chars?), read_file_range(path, start_line, end_line, max_chars?), git_status, git_diff,
find_python, shell(command, timeout?), write_file(path, content, expected_hash?),
apply_patch(path, expected_text, replacement, expected_hash), index_status,
index_files(query?, language?, limit?), index_symbols(name, path?, limit?), index_file_symbols(path),
index_imports(path?). Index tools are project-scoped metadata/navigation only; use `read_file_range` to retrieve source.
Paths may be workspace-relative or
absolute. Git is optional. Shell commands and writes use the started user's normal operating-system permissions.
On Windows, `shell` executes Windows PowerShell (`powershell -Command`), not CMD, Bash, or PowerShell 7. Use
PowerShell syntax such as `Test-Path`; do not use Bash/CMD operators such as `||` or `2>nul`.
Before modifying an existing text file, read it and pass that result's sha256 as expected_hash. Use `apply_patch` for
one exact, uniquely occurring source text replacement; use `write_file` for new files or whole-file replacement. Use
only the supported tools, respect tool errors and path rules, and never commit, push, or release automatically."""


def _short_text(value: Any, limit: int = 500) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= limit else text[:limit] + "..."


def _string_list(value: Any) -> list[str] | None:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    return None


_WORK_PHASES = {"explore", "plan", "implement", "verify"}


def _ensure_work_memory(agent: dict[str, Any]) -> None:
    """Supply defaults for checkpoints written before the run-memory fields existed."""
    if agent.get("phase") not in _WORK_PHASES:
        agent["phase"] = "explore"
    if not isinstance(agent.get("current_task"), str):
        agent["current_task"] = ""
    for key in ("facts", "decisions", "information_gaps"):
        if not isinstance(agent.get(key), list):
            agent[key] = []
    progress = agent.get("progress") if isinstance(agent.get("progress"), dict) else {}
    agent["progress"] = progress
    if not isinstance(progress.get("sources"), list):
        progress["sources"] = progress.pop("findings", []) if isinstance(progress.get("findings"), list) else []
    else:
        progress.pop("findings", None)
    # Checkpoints written before P1 stored observed and proposed notes together.
    # Keep their useful content once, then save the compact shape at the next node.
    legacy_findings = agent.pop("findings", [])
    if isinstance(legacy_findings, list):
        observed = [item for item in legacy_findings if isinstance(item, dict) and item.get("status") == "observed"]
        proposed = [item for item in legacy_findings if isinstance(item, dict) and item.get("status") != "observed"]
        agent["facts"] = _append_unique(agent["facts"], observed)
        agent["decisions"] = _append_unique(agent["decisions"], proposed)
    legacy_questions = agent.pop("open_questions", [])
    if isinstance(legacy_questions, list):
        questions = []
        for item in legacy_questions:
            if isinstance(item, dict):
                questions.append({**item, "kind": item.get("kind", "question"), "status": item.get("status", "proposed")})
        agent["information_gaps"] = _append_unique(agent["information_gaps"], questions)
    agent.pop("completed_tasks", None)
    if not isinstance(agent.get("repository_map"), dict):
        agent["repository_map"] = {}
    if not isinstance(agent.get("pending_decision_trace"), dict):
        agent["pending_decision_trace"] = None


def _repository_map(agent: dict[str, Any], workspace: str, task: str) -> dict[str, Any]:
    """Load one compact, persistent index overview; details remain tool-queryable."""
    existing = agent.get("repository_map")
    if isinstance(existing, dict) and existing.get("workspace") == workspace:
        return existing
    project_id = os.environ.get("REAPER_PROJECT_ID", "")
    unavailable = {"available": False, "workspace": workspace}
    if not project_id:
        return unavailable
    try:
        store = Store()
        project = store.get_project(project_id)
        if project is None or Path(project["workspace"]).resolve() != Path(workspace).resolve():
            return unavailable
        status = store.index_status(project_id)
        files = store.index_files(project_id)
        terms = {term.casefold() for term in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", task)
                 if term.casefold() not in {"with", "from", "that", "this", "file", "files", "code", "change", "create"}}
        ranked = sorted(files, key=lambda item: (
            -sum(term in item["path"].casefold() for term in terms), item["path"]
        ))
        selected = ranked[:12]
        symbols: list[dict[str, Any]] = []
        for term in list(terms)[:6]:
            symbols.extend(store.find_index_symbols(project_id, term, limit=4))
        seen_symbols: set[tuple[str, str, int]] = set()
        symbol_map = []
        for symbol in symbols:
            marker = (symbol["path"], symbol["qualname"], symbol["start_line"])
            if marker not in seen_symbols:
                symbol_map.append({key: symbol.get(key) for key in ("path", "name", "qualname", "kind", "start_line", "end_line")})
                seen_symbols.add(marker)
        imports = []
        for item in selected[:6]:
            imports.extend(store.index_imports(project_id, item["path"]))
        return {
            "available": True, "project_id": project_id, "workspace": workspace,
            "status": {key: status.get(key) for key in ("scanned_at", "git_branch", "git_head", "file_count", "python_file_count", "symbol_count")},
            "files": [{key: item.get(key) for key in ("path", "language", "line_count", "sha256")} for item in selected],
            "symbols": symbol_map[:24],
            "imports": [{key: item.get(key) for key in ("path", "module", "imported_name", "local_module", "line")} for item in imports[:24]],
        }
    except StorageError:
        return unavailable


def _repository_map_snapshot(repository_map: Any) -> dict[str, Any]:
    """Keep orientation data useful without crowding out the current edit."""
    if not isinstance(repository_map, dict):
        return {}
    result = {key: repository_map.get(key) for key in ("available", "project_id", "workspace", "status") if key in repository_map}
    for key, fields in (("files", ("path", "language", "line_count")),
                        ("symbols", ("path", "qualname", "kind", "start_line", "end_line")),
                        ("imports", ("path", "module", "imported_name", "local_module", "line"))):
        values = repository_map.get(key)
        if isinstance(values, list):
            result[key] = [{field: item.get(field) for field in fields if field in item}
                           for item in values[:8] if isinstance(item, dict)]
    return result


def _memory_text_items(value: Any, *, field: str, source_hashes: set[tuple[str, str]]) -> list[dict[str, Any]]:
    """Normalize model-supplied working notes without upgrading them to facts."""
    raw_items = value if isinstance(value, list) else [value] if isinstance(value, str) else []
    items: list[dict[str, Any]] = []
    for raw in raw_items:
        source: dict[str, Any] | None = None
        if isinstance(raw, dict):
            text = raw.get("summary", raw.get("text", raw.get(field, "")))
            candidate = raw.get("source")
            if isinstance(candidate, dict):
                path, digest = candidate.get("path"), candidate.get("sha256")
                if isinstance(path, str) and isinstance(digest, str) and (path, digest) in source_hashes:
                    source = {key: candidate[key] for key in ("path", "sha256", "start_line", "end_line") if key in candidate}
        else:
            text = raw
        if not isinstance(text, str) or not text.strip():
            continue
        item = {"status": "proposed", "kind": field, "summary": _short_text(text.strip(), 600)}
        if source is not None:
            item["source"] = source
        items.append(item)
    return items


def _append_unique(items: list[dict[str, Any]], additions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep accumulated notes stable across decisions while avoiding exact repeats."""
    seen = {(item.get("status"), item.get("summary"), str(item.get("source"))) for item in items if isinstance(item, dict)}
    result = [item for item in items if isinstance(item, dict)]
    for item in additions:
        marker = (item.get("status"), item.get("summary"), str(item.get("source")))
        if marker not in seen:
            result.append(item)
            seen.add(marker)
    return result


def _compact_memory_item(item: dict[str, Any]) -> dict[str, Any]:
    """Serialize evidence for a decision without replaying a tool's full payload."""
    compact = {key: item.get(key) for key in ("status", "kind", "tool", "query", "path", "source")
               if key in item}
    compact["summary"] = _short_text(item.get("summary", ""), 500)
    locations = item.get("locations")
    if isinstance(locations, list):
        compact["locations"] = [
            {key: _short_text(location.get(key), 240) for key in ("path", "line", "text") if key in location}
            for location in locations[:4] if isinstance(location, dict)
        ]
    evidence = item.get("evidence")
    if isinstance(evidence, list):
        compact["evidence"] = [
            {key: value for key, value in entry.items()
             if key in {"path", "name", "qualname", "kind", "start_line", "end_line", "module", "local_module"}}
            for entry in evidence[:4] if isinstance(entry, dict)
        ]
    elif isinstance(evidence, dict):
        compact["evidence"] = {key: evidence.get(key) for key in
                               ("file_count", "symbol_count", "git_branch", "git_head", "scanned_at") if key in evidence}
    return compact


def _work_memory_snapshot(agent: dict[str, Any]) -> dict[str, Any]:
    """Present current, compact evidence before historical notes and observations."""
    _ensure_work_memory(agent)
    facts = [item for item in agent["facts"] if isinstance(item, dict)]
    return {
        "current_task": _short_text(agent["current_task"], 600),
        "phase": agent["phase"],
        "confirmed_facts": [_compact_memory_item(item) for item in facts[-10:]],
        "decisions": [_compact_memory_item(item) for item in agent["decisions"][-8:]],
        "information_gaps": [_compact_memory_item(item) for item in agent["information_gaps"][-8:]],
    }


def _update_work_memory_from_decision(agent: dict[str, Any], decision: dict[str, Any]) -> None:
    """Apply only incremental model notes; factual state remains tool-derived."""
    _ensure_work_memory(agent)
    current_task = decision.get("current_task")
    if isinstance(current_task, str) and current_task.strip():
        agent["current_task"] = _short_text(current_task.strip(), 600)
    source_hashes = {
        (item.get("path"), item.get("sha256")) for item in agent.get("progress", {}).get("sources", [])
        if isinstance(item, dict) and isinstance(item.get("path"), str) and isinstance(item.get("sha256"), str)
    }
    for key in ("decisions", "findings", "completed_tasks"):
        agent["decisions"] = _append_unique(
            agent["decisions"], _memory_text_items(decision.get(key), field=key, source_hashes=source_hashes)
        )
    for key in ("information_gaps", "open_questions"):
        agent["information_gaps"] = _append_unique(
            agent["information_gaps"], _memory_text_items(decision.get(key), field=key, source_hashes=source_hashes)
        )


def _captured_end(finding: dict[str, Any]) -> int | None:
    """Return the last complete source line represented by a finding."""
    start, end, content = finding.get("start_line"), finding.get("end_line"), finding.get("content")
    if not isinstance(start, int) or not isinstance(end, int) or not isinstance(content, str):
        return None
    line_breaks = content.count("\n")
    # A truncated final fragment is useful context, but is not evidence that its
    # line was fully read. A complete source file may legitimately omit a final
    # newline, so retain that line unless the content was clipped.
    clipped = bool(finding.get("truncated") or finding.get("context_truncated"))
    observed_end = start + line_breaks - 1 if content.endswith("\n") else start + line_breaks
    if clipped and not content.endswith("\n"):
        observed_end -= 1
    return min(end, observed_end) if observed_end >= start else None


def _merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _source_coverage(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Describe completed, not merely requested, source ranges by file revision."""
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for finding in findings:
        path, digest, start = finding.get("path"), finding.get("sha256"), finding.get("start_line")
        captured_end = _captured_end(finding)
        if not isinstance(path, str) or not isinstance(digest, str) or not isinstance(start, int):
            continue
        entry = grouped.setdefault((path, digest), {"path": path, "sha256": digest, "ranges": [], "unread_from": []})
        if captured_end is not None:
            entry["ranges"].append((start, captured_end))
        if bool(finding.get("truncated") or finding.get("context_truncated")):
            entry["unread_from"].append((captured_end + 1) if captured_end is not None else start)
    coverage: list[dict[str, Any]] = []
    for entry in grouped.values():
        ranges = _merge_intervals(entry["ranges"])
        gaps = [[ranges[index][1] + 1, ranges[index + 1][0] - 1]
                for index in range(len(ranges) - 1) if ranges[index][1] + 1 < ranges[index + 1][0]]
        item: dict[str, Any] = {"path": entry["path"], "sha256": entry["sha256"],
                                "covered_ranges": [[start, end] for start, end in ranges]}
        if gaps:
            item["unread_ranges"] = gaps
        if entry["unread_from"]:
            # A later range can fill the tail that an earlier truncated read
            # did not capture. Advance each incomplete point through contiguous
            # completed coverage before telling the model what remains unknown.
            unread_points: list[int] = []
            for unread_from in entry["unread_from"]:
                for range_start, range_end in ranges:
                    if range_start <= unread_from <= range_end:
                        unread_from = range_end + 1
                    elif range_start > unread_from:
                        break
                unread_points.append(unread_from)
            item["unread_from"] = min(unread_points)
        coverage.append(item)
    return coverage


def _uncovered_ranges(start: int, end: int, covered: list[tuple[int, int]]) -> list[tuple[int, int]]:
    remaining: list[tuple[int, int]] = []
    cursor = start
    for covered_start, covered_end in _merge_intervals(covered):
        if covered_end < cursor:
            continue
        if covered_start > end:
            break
        if cursor < covered_start:
            remaining.append((cursor, min(end, covered_start - 1)))
        cursor = max(cursor, covered_end + 1)
    if cursor <= end:
        remaining.append((cursor, end))
    return remaining


def _non_overlapping_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Retain each complete source line once, preferring the newest read."""
    covered: dict[tuple[str, str], list[tuple[int, int]]] = {}
    retained: list[dict[str, Any]] = []
    for finding in reversed(findings):
        path, digest, start = finding.get("path"), finding.get("sha256"), finding.get("start_line")
        captured_end = _captured_end(finding)
        if not isinstance(path, str) or not isinstance(digest, str) or not isinstance(start, int) or captured_end is None:
            continue
        key = (path, digest)
        lines = finding["content"].splitlines(keepends=True)
        for range_start, range_end in _uncovered_ranges(start, captured_end, covered.get(key, [])):
            offset = range_start - start
            length = range_end - range_start + 1
            content = "".join(lines[offset:offset + length])
            if not content:
                continue
            item = {field: finding.get(field) for field in ("path", "sha256", "truncated", "original_length", "captured_length")}
            item.update({"start_line": range_start, "end_line": range_end, "content": content})
            retained.append(item)
        covered[key] = _merge_intervals([*covered.get(key, []), (start, captured_end)])
    return list(reversed(retained))


def _coherent_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Join adjacent retained ranges so a function reaches the model intact."""
    joined: list[dict[str, Any]] = []
    ordered = sorted(findings, key=lambda item: (str(item.get("path", "")), str(item.get("sha256", "")),
                                                 item.get("start_line", 0)))
    for finding in ordered:
        if (joined and finding.get("path") == joined[-1].get("path") and
                finding.get("sha256") == joined[-1].get("sha256") and
                isinstance(finding.get("start_line"), int) and
                finding["start_line"] == joined[-1].get("end_line", 0) + 1):
            joined[-1]["content"] += finding["content"]
            joined[-1]["end_line"] = finding["end_line"]
            joined[-1]["truncated"] = bool(joined[-1].get("truncated") or finding.get("truncated"))
            continue
        joined.append(dict(finding))
    return joined


def _implementation_intent(agent: dict[str, Any]) -> bool:
    """Recognize a concrete proposed change without treating it as completed work."""
    notes = [agent.get("current_task", "")]
    notes.extend(item.get("summary", "") for item in agent.get("decisions", []) if isinstance(item, dict))
    text = " ".join(note for note in notes if isinstance(note, str)).casefold()
    return bool(re.search(r"\b(add|implement|change|update|write|create|replace|fix|remove)\b", text))


def _reconcile_work_phase(agent: dict[str, Any]) -> None:
    """Promote a concrete, evidence-backed plan without claiming it is complete."""
    if agent.get("phase") == "plan" and _implementation_intent(agent):
        agent["phase"] = "implement"


def _reconcile_information_gaps(agent: dict[str, Any]) -> None:
    """Drop no-match searches once later source evidence has superseded them."""
    source_paths = {
        item.get("path") for item in agent.get("progress", {}).get("sources", [])
        if isinstance(item, dict) and isinstance(item.get("path"), str) and item.get("content")
    }
    agent["information_gaps"] = [gap for gap in agent.get("information_gaps", []) if not (
        isinstance(gap, dict) and gap.get("kind") == "search_gap" and
        (not isinstance(gap.get("path"), str) or gap.get("path") in source_paths)
    )]


def _context_range_scores(agent: dict[str, Any], findings: list[dict[str, Any]]) -> list[int]:
    """Rank individual source ranges for the next action, not their whole file."""
    notes = [agent.get("current_task", "")]
    notes.extend(item.get("summary", "") for item in agent.get("decisions", []) if isinstance(item, dict))
    terms = {term.casefold() for note in notes if isinstance(note, str)
             for term in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", note)}
    recent_reads = [
        {key: item.get(key) for key in ("path", "sha256", "start_line", "end_line")}
        for item in agent.get("progress", {}).get("sources", []) if isinstance(item, dict)
    ]
    search_locations = [location for item in agent.get("facts", [])
                        if isinstance(item, dict) and item.get("kind") == "search_result"
                        for location in item.get("locations", []) if isinstance(location, dict)]
    scores: list[int] = []
    for index, finding in enumerate(findings):
        path, start, end = finding.get("path"), finding.get("start_line"), finding.get("end_line")
        if not isinstance(path, str) or not isinstance(start, int) or not isinstance(end, int):
            scores.append(-1)
            continue
        path_terms = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", path.casefold()))
        content = finding.get("content", "").casefold()
        matches = sum(term in path_terms or re.search(rf"\b{re.escape(term)}\b", content) is not None
                      for term in terms)
        score = matches * 100
        # A dispatcher-truncated full read is weaker edit context than a later,
        # complete targeted range from the same file.
        if finding.get("truncated"):
            score -= 5_000
        # A latest, focused read is direct evidence about the active edit.  It
        # wins ties over an older range from the same file without hiding a
        # separately relevant Store/API convention.
        for recency, source in enumerate(reversed(recent_reads), start=1):
            if source.get("path") != path:
                continue
            read_start, read_end = source.get("start_line"), source.get("end_line")
            if isinstance(read_start, int) and isinstance(read_end, int) and not (end < read_start or start > read_end):
                score += 1_000 + max(0, 100 - recency)
                break
        for location in search_locations:
            line = location.get("line")
            if location.get("path") == path and isinstance(line, int) and start <= line <= end:
                score += 500
        scores.append(score + index)
    return scores


def _select_context_findings(agent: dict[str, Any], findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prefer a few task-relevant, coherent ranges over every historical fragment."""
    scores = _context_range_scores(agent, findings)
    selected_indexes: set[int] = set()
    for index, candidate in sorted(enumerate(findings), key=lambda entry: scores[entry[0]], reverse=True):
        if len(selected_indexes) == 3:
            break
        path, digest = candidate.get("path"), candidate.get("sha256")
        start, end = candidate.get("start_line"), candidate.get("end_line")
        if not all((isinstance(path, str), isinstance(digest, str), isinstance(start, int), isinstance(end, int))):
            continue
        duplicate = False
        for selected_index in selected_indexes:
            selected = findings[selected_index]
            selected_start, selected_end = selected.get("start_line"), selected.get("end_line")
            if (selected.get("path") != path or selected.get("sha256") != digest or
                    not isinstance(selected_start, int) or not isinstance(selected_end, int)):
                continue
            overlap = max(0, min(end, selected_end) - max(start, selected_start) + 1)
            if not overlap:
                continue
            # A clipped broad capture adds no usable context beside the focused
            # range it overlaps. Near-identical reads are likewise redundant.
            if candidate.get("truncated") or overlap * 2 >= end - start + 1:
                duplicate = True
                break
        if not duplicate:
            selected_indexes.add(index)
    # Deduplicate only the ranges that will be shown. Doing so before ranking
    # can fuse a full-file capture with a later targeted range, after which
    # truncation keeps only the unrelated beginning of the combined excerpt.
    selected = [finding for index, finding in enumerate(findings) if index in selected_indexes]
    coherent = _coherent_findings(_non_overlapping_findings(selected))
    coherent_scores = _context_range_scores(agent, coherent)
    return [entry[1] for entry in sorted(enumerate(coherent), key=lambda entry: coherent_scores[entry[0]], reverse=True)]


def _progress_snapshot(agent: dict[str, Any]) -> dict[str, Any]:
    """Compact changes and verification state, separate from sources and history."""
    progress = agent.get("progress") if isinstance(agent.get("progress"), dict) else {}
    return {
        "last_change": progress.get("last_change"),
        "checks": list(progress.get("checks", []))[-12:],
        "open_verifications": list(progress.get("open_verifications", [])),
        "errors": list(progress.get("errors", []))[-12:],
        "last_reason": _short_text(progress.get("last_reason", "")),
    }


def _source_fact_finding(finding: dict[str, Any]) -> dict[str, Any] | None:
    """Summarize source facts confirmed by a successful read, never model prose."""
    path, digest, content = finding.get("path"), finding.get("sha256"), finding.get("content")
    start_line, end_line = finding.get("start_line"), finding.get("end_line")
    if not all((isinstance(path, str), isinstance(digest, str), isinstance(content, str),
                isinstance(start_line, int), isinstance(end_line, int))):
        return None
    facts: list[str] = []
    try:
        tree = ast.parse(content)
        for node in ast.walk(tree):
            line = start_line + node.lineno - 1 if hasattr(node, "lineno") else None
            if isinstance(node, ast.ClassDef):
                facts.append(f"class {node.name} at line {line}")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
                try:
                    signature = ast.unparse(node.args)
                except (TypeError, ValueError):
                    signature = "..."
                facts.append(f"{prefix} {node.name}({signature}) at line {line}")
            elif isinstance(node, ast.Import):
                facts.append("imports " + ", ".join(alias.name for alias in node.names))
            elif isinstance(node, ast.ImportFrom):
                module = "." * node.level + (node.module or "")
                facts.append(f"imports from {module}: " + ", ".join(alias.name for alias in node.names))
    except SyntaxError:
        # Ranges inside an indented scope or a clipped final definition are not
        # independently parseable. Preserve only declaration/import lines that
        # occur verbatim in the observed text; this is not a substitute AST.
        for offset, line in enumerate(content.splitlines(), start=0):
            match = re.match(r"^\s*((?:async\s+def|def|class)\s+[A-Za-z_]\w*[^:]*:|"
                             r"from\s+[^\s]+\s+import\s+.+|import\s+.+)$", line)
            if match:
                facts.append(f"{match.group(1).strip()} at line {start_line + offset}")
    if not facts:
        return None
    unique = list(dict.fromkeys(facts))[:8]
    return {
        "status": "observed",
        "kind": "source_fact",
        "summary": _short_text(f"Observed in {path}: " + "; ".join(unique), 1_200),
        "source": {"path": path, "sha256": digest, "start_line": start_line, "end_line": end_line},
    }


def _observed_tool_knowledge(action: dict[str, Any], result: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Turn every information-bearing tool result into compact, sourced run knowledge.

    This is deliberately one update point for file, search, index, change, and
    verification evidence.  It records what a tool established, never a model
    interpretation, and leaves full result payloads in the checkpointed
    observation history.
    """
    tool = action.get("tool") if isinstance(action.get("tool"), str) else "tool"
    args = action.get("args") if isinstance(action.get("args"), dict) else {}
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    if not result.get("ok"):
        error = _short_text(result.get("error", "Tool failed"), 600)
        return ([{"status": "observed", "kind": "tool_error",
                  "summary": f"{tool} failed: {error}", "tool": tool, "args": args}],
                [{"status": "observed", "kind": "tool_error",
                  "summary": f"Resolve the {tool} failure before relying on its result: {error}", "tool": tool}])

    source = {key: data.get(key) for key in ("path", "sha256", "start_line", "end_line")
              if isinstance(data.get(key), (str, int))}
    facts: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    if tool in {"read_file", "read_file_range"} and isinstance(data.get("content"), str):
        finding = {key: data.get(key) for key in ("path", "start_line", "end_line", "sha256", "content",
                                                    "truncated", "original_length", "captured_length")}
        if tool == "read_file_range":
            finding["end_line"] = data.get("captured_end_line", data.get("last_line", finding["end_line"]))
        source_fact = _source_fact_finding(finding)
        if source_fact is not None:
            facts.append(source_fact)
        if data.get("truncated"):
            gaps.append({"status": "observed", "kind": "source_gap",
                         "summary": f"Only part of {source.get('path', args.get('path', 'file'))} is captured; read a targeted missing range if needed.",
                         "source": source})
    elif tool == "search_text":
        matches = data.get("matches") if isinstance(data.get("matches"), list) else []
        locations = [{key: item.get(key) for key in ("path", "line", "text")}
                     for item in matches[:12] if isinstance(item, dict) and isinstance(item.get("path"), str)
                     and isinstance(item.get("line"), int)]
        query = args.get("query", "")
        facts.append({"status": "observed", "kind": "search_result",
                      "summary": _short_text(f"Search {query!r} in {args.get('path', '.')}: {len(matches)} match(es)", 600),
                      "tool": tool, "query": query, "path": args.get("path", "."), "locations": locations})
        if locations:
            gaps.append({"status": "observed", "kind": "source_gap",
                         "summary": f"Search found source locations for {query!r}; read a matching range for implementation details if they are still needed.",
                         "tool": tool, "query": query, "locations": locations})
        else:
            gaps.append({"status": "observed", "kind": "search_gap",
                         "summary": f"Search {query!r} found no matches; refine the query or use indexed structure.",
                         "tool": tool, "query": query, "path": args.get("path", ".")})
    elif tool.startswith("index_"):
        payload_key = {"index_files": "files", "index_symbols": "symbols", "index_file_symbols": "symbols",
                       "index_imports": "imports", "index_status": "index"}.get(tool, "")
        payload = data.get(payload_key)
        if isinstance(payload, list):
            entries = [{key: item.get(key) for key in ("path", "name", "qualname", "kind", "start_line", "end_line", "module", "local_module")}
                       for item in payload[:12] if isinstance(item, dict)]
            summary = f"{tool} returned {len(payload)} indexed entr{'y' if len(payload) == 1 else 'ies'}"
        elif isinstance(payload, dict):
            entries = {key: payload.get(key) for key in ("file_count", "symbol_count", "git_branch", "git_head", "scanned_at")}
            summary = f"{tool} returned current repository-index status"
        else:
            entries, summary = [], f"{tool} completed"
        facts.append({"status": "observed", "kind": "repository_index", "summary": _short_text(summary, 600),
                      "tool": tool, "args": args, "evidence": entries})
    elif tool == "list_files":
        files = data.get("files") if isinstance(data.get("files"), list) else []
        facts.append({"status": "observed", "kind": "file_listing",
                      "summary": f"Listed {len(files)} file(s) below {args.get('path', '.')}", "tool": tool,
                      "paths": [item.get("path") for item in files[:20] if isinstance(item, dict)]})
    elif tool in {"write_file", "apply_patch"}:
        # Actual changes already have their own hash-bound progress evidence.
        # Do not duplicate that record as a generic fact.
        pass
    else:
        output = result.get("stdout") if isinstance(result.get("stdout"), str) else ""
        evidence = {key: _short_text(value, 1_200) for key, value in data.items()
                    if key in {"output", "diff", "error"} and isinstance(value, str) and value.strip()}
        facts.append({"status": "observed", "kind": "tool_result",
                      "summary": _short_text(f"{tool} succeeded" + (f": {output.strip()}" if output.strip() else ""), 900),
                      "tool": tool, "args": args, "evidence": evidence})
    return facts, gaps


def _record_observed_knowledge(agent: dict[str, Any], facts: list[dict[str, Any]], gaps: list[dict[str, Any]]) -> None:
    """Merge common tool knowledge and explicitly retain an ineffective repeat."""
    before = len([item for item in agent["facts"] if isinstance(item, dict)])
    agent["facts"] = _append_unique(agent["facts"], facts)
    if facts and len(agent["facts"]) == before:
        tool = facts[0].get("tool", "tool")
        agent["facts"] = _append_unique(agent["facts"], [{
            "status": "observed", "kind": "repeat",
            "summary": f"The latest successful {tool} action added no new confirmed knowledge; use the retained facts or address an explicit information gap.",
            "tool": tool,
        }])
    agent["information_gaps"] = _append_unique(agent["information_gaps"], gaps)[-32:]


def _advance_phase_from_evidence(agent: dict[str, Any], action: dict[str, Any], result: dict[str, Any]) -> None:
    """Make working phases evidence-based while retaining dynamic backwards research."""
    if not result.get("ok"):
        return
    tool = action.get("tool")
    if tool in {"list_files", "index_status", "index_files", "index_symbols", "index_file_symbols", "index_imports", "find_python"}:
        agent["phase"] = "explore"
    elif tool in {"search_text", "read_file", "read_file_range"}:
        # A focused read can fill a concrete implementation detail.  Do not
        # discard a pending implementation decision merely because its source
        # was reread; a later decision can still return to research explicitly.
        if agent.get("phase") not in {"implement", "verify"}:
            agent["phase"] = "implement" if _implementation_intent(agent) else "plan"
    elif tool in {"write_file", "apply_patch"}:
        agent["phase"] = "implement"
    elif tool in {"shell", "git_status", "git_diff"} and (
        action.get("verification_for") or agent.get("phase") in {"implement", "verify"}
    ):
        agent["phase"] = "verify"


def _knowledge_mentions_path(item: dict[str, Any], path: str) -> bool:
    """Identify a path in any compact evidence shape before invalidating changed facts."""
    if item.get("path") == path:
        return True
    source = item.get("source")
    if isinstance(source, dict) and source.get("path") == path:
        return True
    for key in ("locations", "paths"):
        values = item.get(key)
        if isinstance(values, list):
            for value in values:
                if value == path or (isinstance(value, dict) and value.get("path") == path):
                    return True
    return False


def _resolve_source_gaps(agent: dict[str, Any], action: dict[str, Any], result: dict[str, Any]) -> None:
    """Clear source gaps once their cited range is read; expire superseded searches."""
    if not result.get("ok"):
        return
    if action.get("tool") == "search_text":
        args = action.get("args") if isinstance(action.get("args"), dict) else {}
        path = args.get("path", ".")
        # A later focused search supersedes an earlier no-match attempt in the
        # same location. The zero-match result remains an observed fact, but it
        # is no longer an actionable information gap.
        agent["information_gaps"] = [gap for gap in agent.get("information_gaps", []) if not (
            isinstance(gap, dict) and gap.get("kind") == "search_gap" and gap.get("path", ".") == path
        )]
        return
    if action.get("tool") not in {"read_file", "read_file_range"}:
        return
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    path, start, end = data.get("path"), data.get("start_line"), data.get("captured_end_line", data.get("end_line"))
    if not isinstance(path, str) or not isinstance(start, int) or not isinstance(end, int):
        return
    retained = []
    for gap in agent.get("information_gaps", []):
        if not isinstance(gap, dict) or gap.get("kind") != "source_gap":
            retained.append(gap)
            continue
        locations = gap.get("locations")
        source = gap.get("source") if isinstance(gap.get("source"), dict) else {}
        location_matches = isinstance(locations, list) and any(
            isinstance(location, dict) and location.get("path") == path and start <= location.get("line", -1) <= end
            for location in locations
        )
        cited = (source.get("path") == path) or location_matches
        if not cited:
            retained.append(gap)
    agent["information_gaps"] = retained


def _record_progress(agent: dict[str, Any], observation: dict[str, Any]) -> None:
    """Persist checks plus read-source evidence without suppressing any action."""
    _ensure_work_memory(agent)
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
        finding = {key: data.get(key) for key in ("path", "start_line", "end_line", "sha256", "content",
                                                    "truncated", "original_length", "captured_length")}
        if action.get("tool") == "read_file_range":
            finding["end_line"] = data.get("captured_end_line", data.get("last_line", finding["end_line"]))
        if isinstance(finding["path"], str):
            key = (finding["path"], finding["start_line"], finding["end_line"])
            findings = list(progress.get("sources", []))
            for index, previous in enumerate(findings):
                if isinstance(previous, dict) and (previous.get("path"), previous.get("start_line"),
                                                   previous.get("end_line")) == key:
                    findings[index] = finding
                    break
            else:
                findings.append(finding)
            progress["sources"] = findings
    changes = result.get("changes")
    if isinstance(changes, list):
        paths = [item.get("path") for item in changes if isinstance(item, dict) and isinstance(item.get("path"), str)]
        if paths:
            progress["last_change"] = {"step": observation.get("step"), "tool": action.get("tool"), "paths": paths}
            # A write refreshes the persistent index in the dispatcher. Rebuild the compact map only for the next
            # decision so it cannot advertise the old file hash.
            agent["repository_map"] = {}
            for change in changes:
                if not isinstance(change, dict) or not isinstance(change.get("path"), str):
                    continue
                path, after_hash = change["path"], change.get("after_hash")
                # Old source-linked notes are not facts about a changed file.
                progress["sources"] = [item for item in progress.get("sources", []) if not (
                    isinstance(item, dict) and item.get("path") == path and item.get("sha256") != after_hash
                )]
                agent["facts"] = [item for item in agent["facts"] if not (
                    isinstance(item, dict) and _knowledge_mentions_path(item, path) and
                    (not isinstance(item.get("source"), dict) or item["source"].get("sha256") != after_hash)
                )]
                agent["information_gaps"] = [item for item in agent.get("information_gaps", []) if not (
                    isinstance(item, dict) and _knowledge_mentions_path(item, path)
                )]
    _resolve_source_gaps(agent, action, result)
    facts, gaps = _observed_tool_knowledge(action, result)
    _record_observed_knowledge(agent, facts, gaps)
    _reconcile_information_gaps(agent)
    _advance_phase_from_evidence(agent, action, result)
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
    """Prioritize durable run work notes, then factual progress and source excerpts."""
    agent = state["agent"]
    budget = _model_context_budget() - len(_decision_system_prompt().encode("utf-8")) - _MODEL_PROTOCOL_OVERHEAD_BYTES
    progress = _progress_snapshot(agent)
    plan = [item for item in agent.get("plan", []) if isinstance(item, dict)][-8:]
    prompt = {"task": state["task"], "current_task": agent.get("current_task", ""), "phase": agent.get("phase", "explore"),
              "workspace": state["workspace"],
              "path_rule": "Paths may be workspace-relative or absolute.",
              "shell_environment": "Windows PowerShell (powershell -Command); use PowerShell syntax, never Bash/CMD `||` or `2>nul`.",
              "code_findings": [], "visible_source_coverage": [],
              "working_memory": _work_memory_snapshot(agent), "progress": progress, "verification": agent["verification"],
              "source_coverage": [], "repository_map": _repository_map_snapshot(agent.get("repository_map")),
              "plan": plan, "current_step": agent["current_step"], "previous_observations": []}
    used = len(json.dumps(prompt, ensure_ascii=False).encode("utf-8"))
    findings = [finding for finding in agent.get("progress", {}).get("sources", [])
                if isinstance(finding, dict) and isinstance(finding.get("content"), str) and finding["content"]]
    prompt["source_coverage"] = _source_coverage(findings)
    used = len(json.dumps(prompt, ensure_ascii=False).encode("utf-8"))
    findings = _select_context_findings(agent, findings)
    retained: list[dict[str, Any]] = []
    source_sizes: list[int] = []
    if findings:
        headers = []
        for finding in findings:
            header = {key: finding.get(key) for key in ("path", "start_line", "end_line", "sha256",
                                                          "truncated", "original_length", "captured_length")}
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
        # Findings are ordered by relevance. Preserve a coherent, current edit
        # range before spending remaining context on older supporting source.
        shares = []
        for size in source_sizes:
            share = min(size, available)
            shares.append(share)
            available -= share
        for finding, header, share in zip(findings, headers, shares):
            item = {**header, "content": finding["content"].encode("utf-8")[:share].decode("utf-8", "ignore")}
            retained.append(item)
        while retained and used + sum(len(json.dumps(item, ensure_ascii=False).encode("utf-8")) for item in retained) > budget:
            overflow = used + sum(len(json.dumps(value, ensure_ascii=False).encode("utf-8")) for value in retained) - budget
            expandable = [item for item in reversed(retained) if item["content"]]
            if not expandable:
                retained.pop()
                continue
            item = expandable[0]
            content = item["content"].encode("utf-8")
            item["content"] = content[:max(0, len(content) - max(1, overflow))].decode("utf-8", "ignore")
        for item, source_size in zip(retained, source_sizes):
            context_size = len(item["content"].encode("utf-8"))
            item["context_truncated"] = context_size < source_size
            start_line, end_line = item.get("start_line"), item.get("end_line")
            if isinstance(start_line, int) and isinstance(end_line, int):
                content = item["content"]
                observed_end = (start_line + content.count("\n") - 1
                                if content.endswith("\n") else start_line + content.count("\n"))
                item["end_line"] = min(end_line, observed_end)
    prompt["code_findings"] = retained
    prompt["visible_source_coverage"] = _source_coverage(retained)
    while retained and len(json.dumps(prompt, ensure_ascii=False).encode("utf-8")) > budget:
        overflow = len(json.dumps(prompt, ensure_ascii=False).encode("utf-8")) - budget
        expandable = [item for item in reversed(retained) if item["content"]]
        if not expandable:
            retained.pop()
        else:
            item = expandable[0]
            content = item["content"].encode("utf-8")
            item["content"] = content[:max(0, len(content) - max(1, overflow))].decode("utf-8", "ignore")
    # The final JSON-size trim can remove complete lines. Recompute metadata
    # afterwards so the trace and visible coverage describe the exact content
    # sent to Ollama, rather than the pre-trim excerpt.
    for item, source_size in zip(retained, source_sizes):
        content = item["content"]
        item["context_truncated"] = len(content.encode("utf-8")) < source_size
        start_line, end_line = item.get("start_line"), item.get("end_line")
        if isinstance(start_line, int) and isinstance(end_line, int):
            observed_end = (start_line + content.count("\n") - 1
                            if content.endswith("\n") else start_line + content.count("\n"))
            item["end_line"] = min(end_line, observed_end)
    prompt["visible_source_coverage"] = _source_coverage(retained)
    used += sum(len(json.dumps(item, ensure_ascii=False).encode("utf-8")) for item in retained)
    used = len(json.dumps(prompt, ensure_ascii=False).encode("utf-8"))
    chosen: list[dict[str, Any]] = []
    for observation in reversed(agent["observations"]):
        action = observation.get("action") if isinstance(observation.get("action"), dict) else {}
        result = observation.get("result") if isinstance(observation.get("result"), dict) else {}
        if action.get("tool") in {"read_file", "read_file_range"}:
            continue
        item = {"step": observation.get("step"), "tool": action.get("tool"), "ok": bool(result.get("ok"))}
        if not result.get("ok"):
            item["error"] = _short_text(result.get("error", "Tool failed"))
        elif action.get("tool") in {"write_file", "apply_patch"}:
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


def _fact_markers(agent: dict[str, Any]) -> set[str]:
    """Identify the confirmed facts available before or after one tool action."""
    return {
        json.dumps(_compact_memory_item(item), ensure_ascii=False, sort_keys=True)
        for item in agent.get("facts", [])
        if isinstance(item, dict) and item.get("status") == "observed" and item.get("kind") != "repeat"
    }


def _action_signature(action: dict[str, Any] | None) -> str | None:
    if not isinstance(action, dict):
        return None
    tool = action.get("tool")
    args = action.get("args")
    if not isinstance(tool, str) or not isinstance(args, dict):
        return None
    return json.dumps({"tool": tool, "args": args}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _trace_context(prompt: str, system: str) -> dict[str, Any]:
    """Record the decision-relevant portions of the exact serialized prompt."""
    payload = json.loads(prompt)
    memory = payload.get("working_memory") if isinstance(payload.get("working_memory"), dict) else {}
    excerpts = []
    for item in payload.get("code_findings", []):
        if not isinstance(item, dict):
            continue
        content = item.get("content") if isinstance(item.get("content"), str) else ""
        excerpts.append({
            **{key: item.get(key) for key in ("path", "start_line", "end_line", "sha256", "context_truncated") if key in item},
            "content_chars": len(content),
            "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        })
    return {
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "prompt_bytes": len(prompt.encode("utf-8")),
        "system_sha256": hashlib.sha256(system.encode("utf-8")).hexdigest(),
        "system_bytes": len(system.encode("utf-8")),
        "task": payload.get("task"),
        "current_task": payload.get("current_task"),
        "phase": payload.get("phase"),
        "confirmed_facts": memory.get("confirmed_facts", []),
        "model_decisions": memory.get("decisions", []),
        "information_gaps": memory.get("information_gaps", []),
        "visible_source_coverage": payload.get("visible_source_coverage", []),
        "source_excerpts": excerpts,
        "open_verifications": payload.get("progress", {}).get("open_verifications", []),
    }


def _trace_result(result: dict[str, Any]) -> dict[str, Any]:
    """Keep the tool outcome diagnostic without duplicating full captured output."""
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    return {
        "ok": bool(result.get("ok")),
        "exit_code": result.get("exit_code"),
        "duration_ms": result.get("duration_ms"),
        "error": _short_text(result.get("error", ""), 800),
        "changes": result.get("changes", []),
        "data_keys": sorted(data)[:24],
    }


def _emit_model_thinking(*, trace_id: str, step: int) -> None:
    """Expose only the separate Ollama thinking field when the server supplied it."""
    response = _LAST_MODEL_RESPONSE.get()
    if not isinstance(response, dict) or not response.get("thinking_available"):
        _emit_event("model_thinking", trace_id=trace_id, step=step, model=MODEL,
                    available=False, reason="Ollama response did not contain message.thinking")
        return
    _emit_event("model_thinking", trace_id=trace_id, step=step, model=MODEL, available=True,
                thinking=response.get("thinking", ""), truncated=bool(response.get("thinking_truncated")))


def _agent_action(decision: dict[str, Any], workspace: str, project_id: str | None = None) -> dict[str, Any] | None:
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
    if project_id:
        context["project_id"] = project_id
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
    agent = dict(state["agent"])
    _ensure_work_memory(agent)
    agent["repository_map"] = _repository_map(agent, state["workspace"], state["task"])
    return {"agent": agent, "status": "agent_prepared", "error": "", "last_result": None}


def agent_decide(state: AgentRunState) -> dict[str, Any]:
    _emit_event("phase_changed", phase="agent_decide")
    agent = dict(state["agent"])
    agent["progress"] = dict(agent.get("progress") or {})
    agent["verification"] = dict(agent.get("verification") or {})
    _ensure_work_memory(agent)
    _reconcile_information_gaps(agent)
    _reconcile_work_phase(agent)
    if not agent["repository_map"]:
        agent["repository_map"] = _repository_map(agent, state["workspace"], state["task"])
    system = _decision_system_prompt()
    prompt = _agent_prompt(state)
    step = agent["current_step"] + 1
    run_id = os.environ.get("REAPER_API_RUN_ID")
    trace_id = f"{run_id or 'local'}:{step}"
    previous = agent["observations"][-1] if agent["observations"] else {}
    previous_action = previous.get("action") if isinstance(previous, dict) else None
    trace = {
        "trace_id": trace_id, "run_id": run_id, "step": step, "model": MODEL,
        "context": _trace_context(prompt, system), "phase_before": agent.get("phase"),
        "previous_action": {"step": previous.get("step"), "action": previous_action}
        if isinstance(previous_action, dict) else None,
        "_fact_markers": sorted(_fact_markers(agent)),
        "_previous_action_signature": _action_signature(previous_action),
    }
    _LAST_MODEL_RESPONSE.set(None)
    try:
        decision = ask_json(system, prompt)
    except (RunBudgetExceeded, httpx.TimeoutException):
        raise
    except Exception as exc:
        error = f"Agent decision failed: {exc}"
        _emit_model_thinking(trace_id=trace_id, step=step)
        _emit_event("decision_trace", trace_state="decision", trace_id=trace_id, run_id=run_id, step=step,
                    model=MODEL, context=trace["context"], phase_before=trace["phase_before"],
                    decision_kind="invalid", reason=error, action=None)
        return _decision_error(agent, f"Agent decision failed: {exc}")
    plan = decision.get("plan")
    if isinstance(plan, list) and plan and all(isinstance(item, dict) for item in plan):
        existing_steps = {str(item.get("step")) for item in agent["plan"] if isinstance(item, dict)}
        agent["plan"] = [*agent["plan"], *[item for item in plan if str(item.get("step")) not in existing_steps]]
    _update_work_memory_from_decision(agent, decision)
    reason = _short_text(decision.get("reason", ""))
    agent["progress"]["last_reason"] = reason
    kind = decision.get("kind")
    _emit_model_thinking(trace_id=trace_id, step=step)
    if kind == "final":
        _emit_event("decision_trace", trace_state="decision", trace_id=trace_id, run_id=run_id, step=step,
                    model=MODEL, context=trace["context"], phase_before=trace["phase_before"],
                    decision_kind="final", reason=reason, action=None)
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
        _emit_event("decision_trace", trace_state="decision", trace_id=trace_id, run_id=run_id, step=step,
                    model=MODEL, context=trace["context"], phase_before=trace["phase_before"],
                    decision_kind="invalid", reason=reason or "Agent decision must be an action or final report", action=None)
        return _decision_error(agent, "Agent decision must be an action or final report")
    open_points = _string_list(decision.get("open_verifications"))
    if open_points is not None:
        agent["progress"]["open_verifications"] = open_points
    try:
        repository_map = agent.get("repository_map") if isinstance(agent.get("repository_map"), dict) else {}
        action = _agent_action(decision, state["workspace"], repository_map.get("project_id"))
    except ValueError as exc:
        _emit_event("decision_trace", trace_state="decision", trace_id=trace_id, run_id=run_id, step=step,
                    model=MODEL, context=trace["context"], phase_before=trace["phase_before"],
                    decision_kind="invalid", reason=str(exc), action=None)
        return _decision_error(agent, str(exc))
    if action is None:
        _emit_event("decision_trace", trace_state="decision", trace_id=trace_id, run_id=run_id, step=step,
                    model=MODEL, context=trace["context"], phase_before=trace["phase_before"],
                    decision_kind="invalid", reason="Agent action requires a tool name and object arguments", action=None)
        return _decision_error(agent, "Agent action requires a tool name and object arguments")
    trace["action"] = {"tool": action["tool"], "args": action["args"]}
    trace["reason"] = reason
    agent["pending_decision_trace"] = trace
    _emit_event("decision_trace", trace_state="decision", trace_id=trace_id, run_id=run_id, step=step,
                model=MODEL, context=trace["context"], phase_before=trace["phase_before"],
                decision_kind="action", reason=reason, action=trace["action"])
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
    _record_progress(agent, observation)
    # A successful source read is retained once in progress.sources.  Historical
    # observations keep its metadata for recovery and diagnostics, not a second
    # complete copy of the same text.
    if action.get("tool") in {"read_file", "read_file_range"}:
        historical_result = dict(result)
        historical_data = dict(result.get("data") or {})
        historical_data.pop("content", None)
        historical_result["data"] = historical_data
        observation = {**observation, "result": historical_result}
    observations = [*agent["observations"], observation]
    agent["observations"] = observations
    agent["current_step"] += 1
    agent["next_action"] = None
    trace = agent.get("pending_decision_trace")
    if isinstance(trace, dict):
        before = set(trace.get("_fact_markers", [])) if isinstance(trace.get("_fact_markers"), list) else set()
        after = _fact_markers(agent)
        new_facts = [json.loads(marker) for marker in sorted(after - before)]
        signature = _action_signature(action)
        previous_signature = trace.get("_previous_action_signature")
        _emit_event(
            "decision_trace", trace_state="result", trace_id=trace.get("trace_id"),
            run_id=trace.get("run_id"), step=observation["step"], model=trace.get("model", MODEL),
            tool_result=_trace_result(result), information_gain={
                "new_confirmed_facts": new_facts,
                "new_fact_count": len(new_facts),
                "repeated_action": bool(signature and signature == previous_signature),
            }, phase_before=trace.get("phase_before"), phase_after=agent.get("phase"),
            strategy_changed=bool(signature and previous_signature and signature != previous_signature),
        )
        agent["pending_decision_trace"] = None
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

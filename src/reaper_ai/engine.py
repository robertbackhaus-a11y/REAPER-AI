"""REAPER-AI: bounded, multi-file coding engine with structural verification.

One repository inventory, one file-selection call, one change-proposal call,
one preflight, one apply, one verification. No autonomous tool loop.
"""

import argparse
import ast
import hashlib
import json
import os
import time
import tomllib
from contextvars import ContextVar
import subprocess
from pathlib import Path
from typing import TypedDict

import httpx
from langgraph.graph import END, START, StateGraph

from reaper_ai.tools import _file
from reaper_ai.persistence import StorageError, Store

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
MODEL = os.environ.get("REAPER_MODEL", "qwen3.6:35b")
MAX_FILES = 3
MAX_SOURCE_CHARS = 20_000
MAX_MODEL_CALLS = 4  # selection + proposal + at most one repair
MAX_WALL_SECONDS = 600
_MAX_REPLY_CHARS = 80_000
_EDITABLE_EXISTING_SUFFIXES = {".py", ".toml", ".md"}
_EDITABLE_EXISTING_NAMES = {"VERSION"}
_RUN = ContextVar("reaper_run_metrics", default=None)


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
    if metrics["model_calls"] >= MAX_MODEL_CALLS:
        raise RunBudgetExceeded("Model-call budget exhausted")
    return metrics



class CodingState(TypedDict):
    workspace: str
    task: str
    inventory: list[dict]
    selected: list[str]
    creating: list[str]
    originals: dict[str, str]
    changes: dict[str, str]
    status: str
    error: str
    details: list[dict]
    corrections: int
    feedback: str


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=20,
    )


def _is_python_path(path: str) -> bool:
    return Path(path).suffix.lower() == ".py"


def _is_editable_existing_path(path: str) -> bool:
    item = Path(path)
    return item.name in _EDITABLE_EXISTING_NAMES or item.suffix.lower() in _EDITABLE_EXISTING_SUFFIXES


def _validate_text_syntax(path: str, content: str) -> str:
    if _is_python_path(path):
        ast.parse(content, filename=path)
        return "ok"
    if Path(path).suffix.lower() == ".toml":
        tomllib.loads(content)
        return "ok"
    return "not_applicable"


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


def code_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def inventory(state: CodingState) -> dict:
    _emit_event("phase_changed", phase="inventory")
    print("[INVENTORY]")
    try:
        root = Path(state["workspace"]).resolve()
        top = git(root, "rev-parse", "--show-toplevel")
        if top.returncode or Path(top.stdout.strip()).resolve() != root:
            raise ValueError("Workspace must be the root of a Git repository")
        files = git(root, "ls-files", "--cached", "--", "*.py", "*.toml", "*.md", "VERSION")
        if files.returncode:
            raise ValueError(files.stderr.strip())
        candidates = []
        for name in files.stdout.splitlines():
            target = _file(str(root), name)
            if not target.is_file():
                continue
            source = target.read_text(encoding="utf-8")
            if len(source) > MAX_SOURCE_CHARS:
                continue
            relative = target.relative_to(root).as_posix()
            if not _is_editable_existing_path(relative):
                continue
            if _is_python_path(relative):
                try:
                    tree = ast.parse(source)
                except SyntaxError:
                    continue
                symbols = [node.name for node in tree.body if isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
                description = (ast.get_docstring(tree) or "")[:160]
            else:
                symbols = []
                description = "Tracked configuration or documentation file"
            candidates.append({"path": relative, "symbols": symbols[:30], "description": description})
        if not candidates:
            raise ValueError("No eligible tracked editable files")
        if len(candidates) > 100:
            raise ValueError("Repository inventory too large (>100 editable files)")
        return {"inventory": candidates, "status": "inventoried", "error": ""}
    except (OSError, ValueError, UnicodeError, subprocess.TimeoutExpired, RunBudgetExceeded) as exc:
        return {"status": "failed", "error": str(exc)}


def select(state: CodingState) -> dict:
    _emit_event("phase_changed", phase="select")
    print("[SELECT]")
    try:
        result = ask_json(
            "Select existing tracked editable files to edit AND new Python paths to create. "
            'Return JSON {"edit":["existing.py"],"create":["new.py"]}. '
            "At most 3 paths total; existing paths must come from inventory. "
            "Only request new files if needed. Never select a path both ways.",
            f"Task:\n{state['task']}\n\nInventory:\n"
            + json.dumps(state["inventory"], ensure_ascii=False),
        )
        paths, creating = result.get("edit"), result.get("create")
        if not isinstance(paths, list) or not isinstance(creating, list):
            raise ValueError("Invalid selection format")
        selected = paths + creating
        known = {x["path"] for x in state["inventory"]}
        if not 1 <= len(selected) <= MAX_FILES:
            raise ValueError(f"File selection must contain 1 to {MAX_FILES} paths (got {len(selected)})")
        if any(not isinstance(x, str) for x in selected):
            raise ValueError("File selection paths must be strings")
        if len(set(selected)) != len(selected):
            raise ValueError("File selection contains duplicate paths")
        unknown = sorted(path for path in paths if path not in known)
        if unknown:
            raise ValueError("Selected existing paths are not in the inventory: " + ", ".join(unknown))
        root = Path(state["workspace"]).resolve()
        originals = {}
        for path in paths:
            target = _file(str(root), path)
            if not target.is_file():
                raise ValueError(f"File disappeared: {path}")
            clean = git(root, "diff", "HEAD", "--quiet", "--", path)
            if clean.returncode != 0:
                raise ValueError(f"File already has Git changes: {path}")
            originals[path] = target.read_text(encoding="utf-8")
        for path in creating:
            target = _file(str(root), path)
            if target.suffix.lower() != ".py" or target.exists() or path in known:
                raise ValueError(f"Invalid new Python file: {path}")
            ignored = git(root, "check-ignore", "-q", "--", path)
            if ignored.returncode == 0:
                raise ValueError(f"New file is gitignored: {path}")
            if ignored.returncode != 1:
                raise ValueError(f"Git ignore check failed: {path}")
        return {"selected": selected, "creating": creating, "originals": originals,
                "status": "selected", "error": ""}
    except (OSError, ValueError, UnicodeError, httpx.HTTPError,
            subprocess.TimeoutExpired, json.JSONDecodeError, RunBudgetExceeded) as exc:
        return {"status": "failed", "error": str(exc)}


def propose(state: CodingState) -> dict:
    _emit_event("phase_changed", phase="propose")
    print("[PROPOSE]")
    try:
        result = ask_json(
            "Implement the task in the selected existing editable files AND new Python files. "
            'Return JSON {"changes":{"relative.py":"complete updated source"}}. '
            "Include every selected file, even if it requires no change. "
            "Retain unrelated behavior in existing files; provide full source for new files. "
            "Do not claim tests ran. No Markdown.",
            f"Task:\n{state['task']}\n\nCorrection guidance: {state.get('feedback', '')}\n\nEdit existing: {state['selected']}\n"
            f"Create new: {state['creating']}\nCurrent existing sources:\n"
            + json.dumps(state["originals"], ensure_ascii=False),
        )
        changes = result.get("changes")
        if not isinstance(changes, dict) or set(changes) != set(state["selected"]):
            raise ValueError("Proposed file set differs from selected files")
        meaningful = False
        for name, content in changes.items():
            if not isinstance(content, str) or not content.strip():
                raise ValueError(f"Invalid proposed content: {name}")
            if len(content) > MAX_SOURCE_CHARS * 2:
                raise ValueError(f"Proposed content too large: {name}")
            _validate_text_syntax(name, content)
            if name in state["creating"]:
                meaningful = True
            elif _is_python_path(name):
                new_tree = ast.parse(content, filename=name)
                old_tree = ast.parse(state["originals"][name], filename=name)
                if ast.dump(new_tree, include_attributes=False) != ast.dump(old_tree, include_attributes=False):
                    meaningful = True
            elif content != state["originals"][name]:
                meaningful = True
        if not meaningful:
            return {"status": "no_semantic_change", "error": "No meaningful selected-file change; task completion not proven"}
        # Reject cosmetic-only Python modifications and unchanged text files.
        changes = {name: content for name, content in changes.items()
                   if name in state["creating"] or (
                       ast.dump(ast.parse(content), include_attributes=False)
                       != ast.dump(ast.parse(state["originals"][name]), include_attributes=False)
                       if _is_python_path(name) else content != state["originals"][name]
                   )}
        return {"changes": changes, "status": "proposed", "error": ""}
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
                if not target.is_file() or target.read_text(encoding="utf-8") != state["originals"][name]:
                    raise ValueError(f"Source changed during proposal: {name}")
                if git(root, "diff", "HEAD", "--quiet", "--", name).returncode != 0:
                    raise ValueError(f"Git state changed: {name}")
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
                if target.read_text(encoding="utf-8") == state["changes"][name]:
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
            if not target.is_file() or target.read_text(encoding="utf-8") != expected:
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
            actual = _file(str(root), name).read_text(encoding="utf-8")
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
    return "repair" if state["status"] in {"proposal_failed", "verify_failed"} and state["corrections"] < 1 else "end"


def repair_proposal(state: CodingState) -> dict:
    _emit_event("phase_changed", phase="repair")
    print("[REPAIR PROPOSAL]")
    return {"corrections": state["corrections"] + 1,
            "feedback": state["error"], "status": "selected", "error": ""}


def _next_stage(stage: str, state: dict) -> str | None:
    status = state.get("status")
    if stage in {"inventory", "select", "apply"}:
        return {"inventory": "select", "select": "propose", "apply": "verify"}.get(stage) \
            if status not in {"failed", "no_semantic_change", "proposal_failed"} else None
    if stage == "propose":
        if status == "proposed":
            return "apply"
        return "repair_proposal" if status == "proposal_failed" and state.get("corrections", 0) < 1 else None
    if stage == "verify":
        return "repair_proposal" if status == "verify_failed" and state.get("corrections", 0) < 1 else None
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
    if start_stage not in {"inventory", "select", "propose", "repair_proposal", "apply", "verify"}:
        raise ValueError(f"Unsupported checkpoint stage: {start_stage}")
    graph = StateGraph(CodingState)
    for name, function in (("inventory", inventory), ("select", select),
                           ("propose", propose), ("repair_proposal", repair_proposal),
                           ("apply", apply), ("verify", verify)):
        graph.add_node(name, _checkpointed(name, function))
    graph.add_edge(START, start_stage)
    graph.add_conditional_edges("inventory", advance, {"next": "select", "end": END})
    graph.add_conditional_edges("select", advance, {"next": "propose", "end": END})
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
    if next_stage in {"propose", "apply", "verify", "repair_proposal"}:
        originals = state.get("originals")
        changes = state.get("changes")
        creating = state.get("creating")
        if not isinstance(originals, dict) or not isinstance(changes, dict) or not isinstance(creating, list):
            raise ValueError("Checkpoint lacks the required stage state")
        # Before proposal/apply or after a rolled-back verification failure, inputs must be untouched.
        expect_originals = next_stage in {"propose", "apply", "repair_proposal"}
        for name, original in originals.items():
            target = _file(str(root), name)
            if not target.is_file() or (expect_originals and target.read_text(encoding="utf-8") != original):
                raise ValueError(f"Checkpoint source no longer matches: {name}")
        for name in creating:
            target = _file(str(root), name)
            if expect_originals and target.exists():
                raise ValueError(f"Checkpoint creation path is no longer empty: {name}")
        if next_stage == "verify":
            for name, expected in changes.items():
                target = _file(str(root), name)
                if not target.is_file() or target.read_text(encoding="utf-8") != expected:
                    raise ValueError(f"Checkpointed write no longer matches: {name}")
    _emit_event("recovery_started", run_id=run_id, stage=checkpoint["stage"], next_stage=next_stage)
    return state, next_stage


def run(task: str, workspace: str) -> dict:
    """Return a truthful run result. No claim of functional correctness."""
    started = time.monotonic()
    metrics = {"deadline": started + MAX_WALL_SECONDS,
               "model_calls": 0, "input_tokens": 0, "output_tokens": 0}
    token = _RUN.set(metrics)
    try:
        if os.environ.get("REAPER_RESUME") == "1":
            initial, start_stage = _resume_state(workspace)
        else:
            start_stage = "inventory"
            initial = {"workspace": str(Path(workspace).resolve()), "task": task,
                       "inventory": [], "selected": [], "creating": [], "originals": {},
                       "changes": {}, "status": "pending", "error": "", "details": [],
                       "corrections": 0, "feedback": ""}
        outcome = build_graph(start_stage).invoke(initial, config={"recursion_limit": 15})
        internal = outcome["status"]
        if internal == "structurally_verified":
            status = "completed"
        elif internal in {"no_semantic_change", "verify_failed"}:
            status = "failed"
        else:
            status = "failed"
        error = outcome.get("error", "")
        if "budget exhausted" in error.lower():
            status = "timed_out"
        return {
            "status": status,
            "verification_level": "structural" if status == "completed" else "none",
            "changed_files": [r["path"] for r in outcome.get("details", [])] if status == "completed" else [],
            "checks": outcome.get("details", []),
            "corrections": outcome.get("corrections", 0),
            "reason": error,
            "internal_status": internal,
            "usage": {"model_calls": metrics["model_calls"],
                      "input_tokens": metrics["input_tokens"],
                      "output_tokens": metrics["output_tokens"],
                      "elapsed_seconds": round(time.monotonic() - started, 1)},
            "limitations": ["No functional runtime tests executed", "Cancellation requires process supervisor"],
        }
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


def _run_with_provenance(task: str, workspace: str) -> dict:
    from uuid import uuid4
    root = Path(workspace).resolve()
    run_id = uuid4().hex[:12]
    head_before, status_before = _git_snapshot(root)
    print(f"RUN_ID: {run_id}", flush=True)
    _emit_event("run_started", run_id=run_id, workspace=str(root))
    result = _original_run(task, str(root))
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

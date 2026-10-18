"""REAPER-AI: bounded, multi-file coding engine with structural verification.

One repository inventory, one file-selection call, one change-proposal call,
one preflight, one apply, one verification. No autonomous tool loop.
"""

import argparse
import ast
import hashlib
import json
import time
from contextvars import ContextVar
import subprocess
from pathlib import Path
from typing import TypedDict

import httpx
from langgraph.graph import END, START, StateGraph

from reaper_ai.tools import _file

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
MODEL = "qwen3.6:35b"
MAX_FILES = 3
MAX_SOURCE_CHARS = 20_000
MAX_MODEL_CALLS = 4  # selection + proposal + at most one repair
MAX_WALL_SECONDS = 600
_MAX_REPLY_CHARS = 80_000
_RUN = ContextVar("reaper_run_metrics", default=None)


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


def ask_json(system: str, user: str) -> dict:
    metrics = _budget()
    timeout = 300.0
    if metrics is not None:
        metrics["model_calls"] += 1
        timeout = max(0.1, min(timeout, metrics["deadline"] - time.monotonic()))
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
    print("[INVENTORY]")
    try:
        root = Path(state["workspace"]).resolve()
        top = git(root, "rev-parse", "--show-toplevel")
        if top.returncode or Path(top.stdout.strip()).resolve() != root:
            raise ValueError("Workspace must be the root of a Git repository")
        files = git(root, "ls-files", "--cached", "--", "*.py")
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
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            symbols = [node.name for node in tree.body if isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
            candidates.append({"path": target.relative_to(root).as_posix(),
                               "symbols": symbols[:30],
                               "description": (ast.get_docstring(tree) or "")[:160]})
        if not candidates:
            raise ValueError("No eligible tracked Python files")
        if len(candidates) > 100:
            raise ValueError("Repository inventory too large (>100 Python files)")
        return {"inventory": candidates, "status": "inventoried", "error": ""}
    except (OSError, ValueError, UnicodeError, subprocess.TimeoutExpired, RunBudgetExceeded) as exc:
        return {"status": "failed", "error": str(exc)}


def select(state: CodingState) -> dict:
    print("[SELECT]")
    try:
        result = ask_json(
            "Select existing Python files to edit AND new Python paths to create. "
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
        if (not 1 <= len(selected) <= MAX_FILES
                or any(not isinstance(x, str) for x in selected)
                or len(set(selected)) != len(selected)
                or any(x not in known for x in paths)):
            raise ValueError("Invalid file selection")
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
    print("[PROPOSE]")
    try:
        result = ask_json(
            "Implement the task in the selected existing AND new Python files. "
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
            new_tree = ast.parse(content, filename=name)
            old_tree = ast.parse(state["originals"][name], filename=name) if name in state["originals"] else None
            if old_tree is None or ast.dump(new_tree, include_attributes=False) != ast.dump(old_tree, include_attributes=False):
                meaningful = True
        if not meaningful:
            return {"status": "no_semantic_change", "error": "No Python AST change; task completion not proven"}
        # Reject cosmetic-only modifications to otherwise unchanged files.
        changes = {name: content for name, content in changes.items()
                   if name in state["creating"] or ast.dump(ast.parse(content), include_attributes=False)
                   != ast.dump(ast.parse(state["originals"][name]), include_attributes=False)}
        return {"changes": changes, "status": "proposed", "error": ""}
    except (ValueError, SyntaxError, httpx.HTTPError, json.JSONDecodeError, RunBudgetExceeded) as exc:
        return {"status": "proposal_failed", "error": str(exc)}


def apply(state: CodingState) -> dict:
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
    print("[VERIFY]")
    root = Path(state["workspace"]).resolve()
    details = []
    try:
        for name, expected in state["changes"].items():
            actual = _file(str(root), name).read_text(encoding="utf-8")
            if actual != expected:
                raise ValueError(f"Written content mismatch: {name}")
            ast.parse(actual, filename=name)
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
            details.append({"path": name, "syntax": "ok", "git": proof})
            print(f"FILE: {name} | SYNTAX: OK | GIT: {proof}")
        _check_local_imports(state)
        print("LOCAL IMPORTS: OK (changed files only)")
        return {"details": details, "status": "structurally_verified", "error": ""}
    except (OSError, ValueError, SyntaxError, subprocess.TimeoutExpired, RunBudgetExceeded) as exc:
        rollback_error = _restore_written(state)
        error = str(exc) + (f"; rollback incomplete: {rollback_error}" if rollback_error else "")
        return {"details": details, "status": "failed" if rollback_error else "verify_failed",
                "error": error}


def advance(state: CodingState) -> str:
    return "next" if state["status"] not in {"failed", "no_semantic_change", "proposal_failed"} else "end"


def retry_proposal(state: CodingState) -> str:
    return "repair" if state["status"] in {"proposal_failed", "verify_failed"} and state["corrections"] < 1 else "end"


def repair_proposal(state: CodingState) -> dict:
    print("[REPAIR PROPOSAL]")
    return {"corrections": state["corrections"] + 1,
            "feedback": state["error"], "status": "selected", "error": ""}



def build_graph():
    graph = StateGraph(CodingState)
    for name, function in (("inventory", inventory), ("select", select),
                           ("propose", propose), ("repair_proposal", repair_proposal),
                           ("apply", apply), ("verify", verify)):
        graph.add_node(name, function)
    graph.add_edge(START, "inventory")
    graph.add_conditional_edges("inventory", advance, {"next": "select", "end": END})
    graph.add_conditional_edges("select", advance, {"next": "propose", "end": END})
    graph.add_conditional_edges("propose", lambda state: "next" if state["status"] == "proposed" else retry_proposal(state),
                                {"next": "apply", "repair": "repair_proposal", "end": END})
    graph.add_edge("repair_proposal", "propose")
    graph.add_conditional_edges("apply", advance, {"next": "verify", "end": END})
    graph.add_conditional_edges("verify", retry_proposal,
                                {"repair": "repair_proposal", "end": END})
    return graph.compile()


def run(task: str, workspace: str) -> dict:
    """Return a truthful run result. No claim of functional correctness."""
    started = time.monotonic()
    metrics = {"deadline": started + MAX_WALL_SECONDS,
               "model_calls": 0, "input_tokens": 0, "output_tokens": 0}
    token = _RUN.set(metrics)
    try:
        outcome = build_graph().invoke({
            "workspace": str(Path(workspace).resolve()), "task": task,
            "inventory": [], "selected": [], "creating": [], "originals": {},
            "changes": {}, "status": "pending", "error": "", "details": [],
            "corrections": 0, "feedback": "",
        }, config={"recursion_limit": 15})
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
    result = _original_run(task, str(root))
    head_after, status_after = _git_snapshot(root)
    result["run_id"] = run_id
    result["git_baseline"] = head_before
    result["git_head_unchanged"] = head_before == head_after
    result["git_status_before"] = status_before
    result["git_status_after"] = status_after
    result["git_status_changed"] = status_before != status_after
    result["resumable"] = False  # No persistent per-run receipts/checkpoints yet.
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

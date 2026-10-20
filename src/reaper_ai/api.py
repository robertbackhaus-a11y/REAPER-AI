# Copyright (c) 2026 Robert Backhaus

"""Local, single-user run API with SQLite conversation and run history."""

import json
import asyncio
import os
import subprocess
import sys
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from reaper_ai.persistence import StorageError, Store
from reaper_ai.repository_index import RepositoryIndexError, refresh_project_index

app = FastAPI(title="REAPER-AI", version="0.7.0")
_lock = threading.Lock()
_runs: dict[str, dict] = {}
_active: str | None = None
_procs: dict[str, subprocess.Popen] = {}
_MAX_LOG = 128_000
_store = Store()
try:
    _interrupted_on_startup = _store.interrupt_active_runs()
    _startup_storage_error: str | None = None
except StorageError as _startup_error:
    _interrupted_on_startup = []
    _startup_storage_error = str(_startup_error)


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


class StartRequest(BaseModel):
    workspace: str | None = Field(default=None, min_length=1)
    task: str = Field(min_length=1, max_length=20_000)
    timeout: int = Field(default=660, ge=1, le=3600)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    conversation_id: str | None = Field(default=None, min_length=1, max_length=64)
    project_id: str | None = Field(default=None, min_length=1, max_length=64)


def _public(entry: dict) -> dict:
    return {k: v for k, v in entry.items() if k not in {"log_path"}}


def _storage_failure(exc: StorageError) -> HTTPException:
    return HTTPException(status_code=503, detail=f"Local persistence unavailable: {exc}")


def _refresh_index(project: dict) -> dict:
    try:
        return refresh_project_index(_store, project)
    except (RepositoryIndexError, StorageError) as exc:
        raise HTTPException(status_code=503, detail=f"Repository index unavailable: {exc}") from exc


def _entry(run_id: str) -> dict:
    """Find a live entry first, then the persisted history after a restart."""
    with _lock:
        entry = _runs.get(run_id)
        if entry is not None:
            return entry
    try:
        stored = _store.get_run(run_id)
    except StorageError as exc:
        raise _storage_failure(exc) from exc
    if stored is None:
        raise HTTPException(status_code=404, detail="Unknown run")
    return stored


def _active_conversation_ids() -> set[str]:
    """Return conversations that still own a live supervisor process."""
    with _lock:
        return {
            entry["conversation_id"] for entry in _runs.values()
            if entry.get("status") in {"running", "cancelling"} and isinstance(entry.get("conversation_id"), str)
        }


def _terminal_summary(run_id: str, status: str, result: dict | None) -> str:
    """A compact, observed-only chat record; raw model prose is not saved here."""
    changed = result.get("changed_files", []) if isinstance(result, dict) else []
    checks = result.get("checks", []) if isinstance(result, dict) else []
    reason = result.get("reason") if isinstance(result, dict) else None
    parts = [f"Run {run_id} finished with status: {status}."]
    if changed:
        parts.append("Recorded changed files: " + ", ".join(str(path) for path in changed[:20]) + ".")
    if checks:
        parts.append(f"Recorded checks: {len(checks)}.")
    if reason:
        parts.append("Reason: " + str(reason)[:500])
    return " ".join(parts)


def _persist_events(run_id: str, log_path: str) -> None:
    """Persist structured engine events once, after the supervisor has finished."""
    try:
        lines = Path(log_path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    sequence = 0
    for line in lines:
        if not line.startswith("REAPER_EVENT:"):
            continue
        try:
            event = json.loads(line[len("REAPER_EVENT:"):].strip())
        except (TypeError, ValueError):
            continue
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            continue
        sequence += 1
        _store.record_event(run_id, sequence, event)


def _watch(run_id: str, proc: subprocess.Popen) -> None:
    global _active
    code = proc.wait()
    with _lock:
        entry = _runs[run_id]
        try:
            log = Path(entry["log_path"]).read_text(encoding="utf-8", errors="replace")
            results = [line.split("RUN_RESULT:", 1)[1].strip() for line in log.splitlines() if line.startswith("RUN_RESULT:")]
            result = json.loads(results[-1]) if results else None
        except (OSError, ValueError, json.JSONDecodeError):
            result = None
        entry["result"] = result
        entry["exit_code"] = code
        entry["finished_at"] = _utc()
        if entry["status"] == "cancelling":
            entry["status"] = "cancelled"
        elif result is not None:
            entry["status"] = result.get("status", "failed")
        elif code == 2:
            entry["status"] = "timed_out"
        elif result is None:
            entry["status"] = "interrupted"
            entry["result"] = {"status": "interrupted", "reason": "Engine exited without a terminal result"}
        else:
            entry["status"] = "failed"
        if _active == run_id:
            _active = None
        _procs.pop(run_id, None)
        try:
            _store.complete_run(run_id, status=entry["status"], finished_at=entry["finished_at"],
                                exit_code=code, result=entry["result"])
            _persist_events(run_id, entry["log_path"])
            if entry.get("conversation_id"):
                _store.add_message(
                    entry["conversation_id"], role="assistant",
                    content=_terminal_summary(run_id, entry["status"], entry["result"]), run_id=run_id,
                    metadata={"status": entry["status"], "result": entry["result"]},
                )
            _store.remember_verified_result(entry)
        except StorageError as exc:
            # The engine outcome remains authoritative even if its history cannot be saved.
            entry["storage_error"] = str(exc)
    project_id = entry.get("project_id")
    if project_id:
        try:
            project = _store.get_project(project_id)
            if project is not None:
                refresh_project_index(_store, project)
        except (RepositoryIndexError, StorageError) as exc:
            entry["index_error"] = str(exc)


@app.get("/health")
def health():
    return {"service": "reaper-ai", "status": "ok", "interrupted_runs": _interrupted_on_startup,
            "storage_error": _startup_storage_error}


OLLAMA_BASE = "http://127.0.0.1:11434"


def _installed_models() -> list[str]:
    try:
        response = httpx.get(f"{OLLAMA_BASE}/api/tags", timeout=5.0)
        response.raise_for_status()
        payload = response.json()
        models = payload.get("models", [])
        if not isinstance(models, list):
            raise ValueError("Invalid Ollama models response")
        names = [item["name"] for item in models
                 if isinstance(item, dict) and isinstance(item.get("name"), str)]
        return sorted(set(names), key=str.casefold)
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=503, detail=f"Ollama models unavailable: {exc}") from exc


@app.get("/models")
def get_models():
    return {"models": _installed_models()}


class ProjectRequest(BaseModel):
    workspace: str = Field(min_length=1)


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    workspace: str = Field(min_length=1, max_length=10_000)
    description: str | None = Field(default=None, max_length=4_000)
    system_prompt: str = Field(default="", max_length=12_000)
    model: str = Field(default="qwen3.8-q5:latest", min_length=1, max_length=200)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    top_p: float = Field(default=0.9, gt=0.0, le=1.0)
    top_k: int = Field(default=40, ge=1, le=500)
    num_ctx: int = Field(default=16_384, ge=1_024, le=131_072)


class ProjectUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    workspace: str | None = Field(default=None, min_length=1, max_length=10_000)
    description: str | None = Field(default=None, max_length=4_000)
    system_prompt: str | None = Field(default=None, max_length=12_000)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    top_p: float | None = Field(default=None, gt=0.0, le=1.0)
    top_k: int | None = Field(default=None, ge=1, le=500)
    num_ctx: int | None = Field(default=None, ge=1_024, le=131_072)


def _validate_workspace(workspace: str) -> Path:
    # Workspace must be explicitly supplied as an absolute path, not a path
    # relative to the API process or the current user directory.
    candidate = Path(workspace).expanduser()
    if not candidate.is_absolute():
        raise HTTPException(status_code=422, detail="Workspace must be an absolute path")
    root = candidate.resolve()
    if not root.is_dir():
        raise HTTPException(status_code=422, detail="Workspace directory does not exist")
    return root


@app.post("/projects/validate")
def validate_project(request: ProjectRequest):
    root = _validate_workspace(request.workspace)
    return {"workspace": str(root), "valid": True}


def _project_or_404(project_id: str) -> dict:
    try:
        project = _store.get_project(project_id)
    except StorageError as exc:
        raise _storage_failure(exc) from exc
    if project is None:
        raise HTTPException(status_code=404, detail="Unknown project")
    return project


@app.get("/projects")
def list_projects():
    try:
        return {"projects": _store.list_projects()}
    except StorageError as exc:
        raise _storage_failure(exc) from exc


@app.post("/projects", status_code=201)
def create_project(request: ProjectCreateRequest):
    root = _validate_workspace(request.workspace)
    try:
        return _store.create_project(**{**request.model_dump(), "workspace": str(root)})
    except StorageError as exc:
        raise _storage_failure(exc) from exc


@app.get("/projects/{project_id}")
def get_project(project_id: str):
    return _project_or_404(project_id)


@app.patch("/projects/{project_id}")
def update_project(project_id: str, request: ProjectUpdateRequest):
    _project_or_404(project_id)
    changes = request.model_dump(exclude_unset=True)
    if "workspace" in changes:
        changes["workspace"] = str(_validate_workspace(changes["workspace"]))
    try:
        project = _store.update_project(project_id, **changes)
    except StorageError as exc:
        raise _storage_failure(exc) from exc
    if project is None:
        raise HTTPException(status_code=404, detail="Unknown project")
    return project


@app.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: str):
    _project_or_404(project_id)
    with _lock:
        active = _runs.get(_active) if _active else None
        if active is not None and active.get("project_id") == project_id and active.get("status") in {"running", "cancelling"}:
            raise HTTPException(status_code=409, detail=f"Project cannot be deleted while run {_active} is active")
    try:
        if not _store.delete_project(project_id):
            raise HTTPException(status_code=404, detail="Unknown project")
    except StorageError as exc:
        raise _storage_failure(exc) from exc
    return Response(status_code=204)


@app.post("/projects/{project_id}/index")
def refresh_index(project_id: str):
    return _refresh_index(_project_or_404(project_id))


@app.get("/projects/{project_id}/index")
def get_index_status(project_id: str):
    project = _project_or_404(project_id)
    _refresh_index(project)
    try:
        return _store.index_status(project_id)
    except StorageError as exc:
        raise _storage_failure(exc) from exc


@app.get("/projects/{project_id}/index/files")
def get_index_files(project_id: str, query: str | None = None, language: str | None = None,
                    limit: int = 500):
    _refresh_index(_project_or_404(project_id))
    try:
        return {"files": _store.list_index_files(project_id, query=query, language=language,
                                                   limit=max(1, min(limit, 2_000)))}
    except StorageError as exc:
        raise _storage_failure(exc) from exc


@app.get("/projects/{project_id}/index/symbols")
def get_index_symbols(project_id: str, name: str = "", path: str | None = None, limit: int = 100):
    _refresh_index(_project_or_404(project_id))
    try:
        if path and not name:
            return {"symbols": _store.index_symbols_for_file(project_id, path)}
        return {"symbols": _store.find_index_symbols(project_id, name, path=path,
                                                       limit=max(1, min(limit, 1_000)))}
    except StorageError as exc:
        raise _storage_failure(exc) from exc


@app.get("/projects/{project_id}/index/imports")
def get_index_imports(project_id: str, path: str | None = None):
    _refresh_index(_project_or_404(project_id))
    try:
        return {"imports": _store.index_imports(project_id, path)}
    except StorageError as exc:
        raise _storage_failure(exc) from exc


@app.get("/projects/{project_id}/index/top-python-files")
def get_top_python_files(project_id: str):
    _refresh_index(_project_or_404(project_id))
    try:
        return {"files": _store.top_python_files_by_symbols(project_id, limit=5)}
    except StorageError as exc:
        raise _storage_failure(exc) from exc


class ConversationRequest(BaseModel):
    workspace: str | None = Field(default=None, max_length=10_000)
    model: str | None = Field(default=None, max_length=200)
    title: str | None = Field(default=None, max_length=200)
    project_id: str | None = Field(default=None, min_length=1, max_length=64)


class MemoryRequest(BaseModel):
    content: str = Field(min_length=1, max_length=2_000)
    workspace: str | None = Field(default=None, max_length=10_000)
    project_id: str | None = Field(default=None, min_length=1, max_length=64)


@app.get("/conversations")
def list_conversations(project_id: str | None = None):
    try:
        if project_id is not None:
            _project_or_404(project_id)
        return {"conversations": _store.list_conversations(project_id=project_id)}
    except StorageError as exc:
        raise _storage_failure(exc) from exc


@app.post("/conversations", status_code=201)
def create_conversation(request: ConversationRequest):
    workspace, model = request.workspace, request.model
    if request.project_id:
        project = _project_or_404(request.project_id)
        root = _validate_workspace(project["workspace"])
        if workspace and _validate_workspace(workspace) != root:
            raise HTTPException(status_code=422, detail="Conversation workspace must match its project workspace")
        workspace, model = str(root), project["model"]
    try:
        return _store.create_conversation(workspace=workspace, model=model, title=request.title, project_id=request.project_id)
    except StorageError as exc:
        raise _storage_failure(exc) from exc


@app.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: str):
    try:
        conversation = _store.get_conversation_detail(conversation_id)
    except StorageError as exc:
        raise _storage_failure(exc) from exc
    if conversation is None:
        raise HTTPException(status_code=404, detail="Unknown conversation")
    return conversation


@app.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: str):
    if conversation_id in _active_conversation_ids():
        raise HTTPException(status_code=409, detail="Conversation has an active run")
    try:
        if not _store.delete_conversation(conversation_id):
            raise HTTPException(status_code=404, detail="Unknown conversation")
    except StorageError as exc:
        raise _storage_failure(exc) from exc
    return Response(status_code=204)


@app.delete("/conversations", status_code=204)
def delete_all_conversations(project_id: str | None = None):
    try:
        conversations = _store.list_conversations(project_id=project_id)
    except StorageError as exc:
        raise _storage_failure(exc) from exc
    active = _active_conversation_ids()
    if any(item.get("id") in active for item in conversations):
        raise HTTPException(status_code=409, detail="A conversation has an active run")
    try:
        _store.delete_conversations(project_id=project_id)
    except StorageError as exc:
        raise _storage_failure(exc) from exc
    return Response(status_code=204)


@app.get("/projects/{project_id}/conversations")
def list_project_conversations(project_id: str):
    _project_or_404(project_id)
    try:
        return {"conversations": _store.list_conversations(project_id=project_id)}
    except StorageError as exc:
        raise _storage_failure(exc) from exc


@app.get("/memory")
def list_memory(workspace: str | None = None, project_id: str | None = None):
    try:
        if project_id is not None:
            _project_or_404(project_id)
        return {"memory": _store.list_memory(workspace, project_id=project_id)}
    except StorageError as exc:
        raise _storage_failure(exc) from exc


@app.post("/memory", status_code=201)
def add_memory(request: MemoryRequest):
    workspace = str(_validate_workspace(request.workspace)) if request.workspace else None
    if request.project_id:
        project = _project_or_404(request.project_id)
        project_workspace = str(_validate_workspace(project["workspace"]))
        if workspace and workspace != project_workspace:
            raise HTTPException(status_code=422, detail="Project knowledge workspace must match its project workspace")
        workspace = project_workspace
    try:
        return _store.add_memory(content=request.content, kind="user_decision", workspace=workspace,
                                 project_id=request.project_id, verification="user_confirmed")
    except StorageError as exc:
        raise _storage_failure(exc) from exc


@app.get("/projects/{project_id}/memory")
def list_project_memory(project_id: str):
    _project_or_404(project_id)
    try:
        return {"memory": _store.list_memory(project_id=project_id)}
    except StorageError as exc:
        raise _storage_failure(exc) from exc


@app.post("/projects/{project_id}/memory", status_code=201)
def add_project_memory(project_id: str, request: MemoryRequest):
    project = _project_or_404(project_id)
    try:
        return _store.add_memory(content=request.content, kind="user_decision", workspace=project["workspace"],
                                 project_id=project_id, verification="user_confirmed")
    except StorageError as exc:
        raise _storage_failure(exc) from exc


@app.post("/runs", status_code=202)
def start_run(request: StartRequest):
    global _active
    if os.name != "nt":
        raise HTTPException(status_code=501, detail="Windows supervisor required")
    project: dict | None = None
    if request.project_id:
        project = _project_or_404(request.project_id)
        root = _validate_workspace(project["workspace"])
    elif request.workspace:
        root = _validate_workspace(request.workspace)
    else:
        raise HTTPException(status_code=422, detail="Workspace is required for a legacy run without a project")
    if project is not None and root.is_dir():
        _refresh_index(project)
    # Resolve the model at the start of each run. Never silently use another
    # model if the requested one is missing.
    available = _installed_models()
    default = "qwen3.6:35b"
    model = project["model"] if project else request.model or default
    if model not in available:
        raise HTTPException(status_code=422, detail=f"Model not installed: {model}")
    model_options = ({key: project[key] for key in ("temperature", "top_p", "top_k", "num_ctx")}
                     if project else {"temperature": 0.0, "top_p": 0.9, "top_k": 40, "num_ctx": 16_384})
    project_system_prompt = project["system_prompt"] if project else ""
    run_id = uuid.uuid4().hex[:12]
    log_path = Path(tempfile.gettempdir()) / f"reaper-api-{run_id}.log"
    with _lock:
        if _active is not None:
            raise HTTPException(status_code=409, detail=f"Run {_active} is still active")
        try:
            if request.conversation_id:
                conversation = _store.get_conversation(request.conversation_id)
                if conversation is None:
                    raise HTTPException(status_code=404, detail="Unknown conversation")
            else:
                conversation = _store.create_conversation(
                    workspace=str(root), model=model, title=request.task[:120], project_id=request.project_id
                )
            if request.project_id and conversation.get("project_id") != request.project_id:
                raise HTTPException(status_code=409, detail="Conversation belongs to a different or legacy project")
            prior_context = _store.relevant_context(conversation["id"])
            durable_memory = _store.relevant_memory(str(root), project_id=request.project_id)
            entry = {"run_id": run_id, "conversation_id": conversation["id"], "project_id": request.project_id,
                     "workspace": str(root), "model": model, "model_options": model_options,
                     "project_system_prompt": project_system_prompt, "task": request.task, "timeout": request.timeout,
                     "status": "running",
                     "started_at": _utc(), "finished_at": None, "exit_code": None, "result": None,
                     "log_path": str(log_path)}
            _store.create_run(entry)
            _store.add_message(conversation["id"], role="user", content=request.task, run_id=run_id)
        except StorageError as exc:
            raise _storage_failure(exc) from exc
        engine_task = request.task
        if prior_context:
            engine_task += (
                "\n\nRelevant prior conversation context follows. It is limited to user intent and "
                "observed prior run records; re-check repository facts before relying on it.\n" + prior_context
            )
        if durable_memory:
            engine_task += (
                "\n\nRelevant durable memory follows. User decisions and observed results are advisory; "
                "re-check repository facts before relying on them.\n" + durable_memory
            )
        cmd = [sys.executable, "-u", "-m", "reaper_ai.supervisor", "--workspace", str(root),
               "--task", engine_task, "--timeout", str(request.timeout)]
        try:
            with log_path.open("wb") as output:
                env = os.environ.copy()
                env["REAPER_MODEL"] = model
                env["REAPER_MODEL_OPTIONS"] = json.dumps(model_options, separators=(",", ":"))
                env["REAPER_PROJECT_SYSTEM_PROMPT"] = project_system_prompt
                env["REAPER_API_RUN_ID"] = run_id
                if request.project_id:
                    env["REAPER_PROJECT_ID"] = request.project_id
                env["PYTHONUNBUFFERED"] = "1"
                env["PYTHONUTF8"] = "1"
                env["PYTHONIOENCODING"] = "utf-8:backslashreplace"
                proc = subprocess.Popen(cmd, cwd=str(Path(__file__).resolve().parents[2]),
                                        stdout=output, stderr=subprocess.STDOUT, env=env)
        except OSError as exc:
            try:
                _store.complete_run(run_id, status="failed", finished_at=_utc(), exit_code=None,
                                    result={"status": "failed", "reason": f"Supervisor could not start: {exc}"})
            except StorageError:
                pass
            raise HTTPException(status_code=500, detail=f"Supervisor could not start: {exc}") from exc
        _runs[run_id] = entry
        _procs[run_id] = proc
        _active = run_id
    threading.Thread(target=_watch, args=(run_id, proc), daemon=True).start()
    return _public(_runs[run_id])


@app.get("/runs/active")
def get_active_run():
    with _lock:
        entry = _runs.get(_active) if _active else None
        return {"run": _public(entry) if entry is not None else None}


@app.get("/runs/{run_id}")
def get_run(run_id: str):
    return _public(_entry(run_id))


def _recovery_record(run_id: str) -> tuple[dict, dict | None, bool, str]:
    entry = _entry(run_id)
    try:
        checkpoint = _store.get_checkpoint(run_id)
    except StorageError as exc:
        raise _storage_failure(exc) from exc
    allowed = {"interrupted", "cancelled", "timed_out"}
    if entry["status"] not in allowed:
        return entry, checkpoint, False, f"Run status {entry['status']} cannot be resumed"
    if checkpoint is None:
        return entry, None, False, "No checkpoint was recorded before interruption"
    if not checkpoint.get("valid"):
        return entry, checkpoint, False, str(checkpoint.get("invalid_reason") or "Checkpoint is invalid")
    state = checkpoint.get("state")
    valid_stages = {"agent_prepare", "agent_decide", "agent_execute", "agent_observe"}
    if not isinstance(state, dict) or state.get("workspace") != entry["workspace"]:
        try:
            _store.invalidate_checkpoint(run_id, "Checkpoint workspace does not match the run")
        except StorageError as exc:
            raise _storage_failure(exc) from exc
        return entry, checkpoint, False, "Checkpoint workspace does not match the run"
    if checkpoint.get("next_stage") not in valid_stages:
        try:
            _store.invalidate_checkpoint(run_id, "Checkpoint has an unsupported next stage")
        except StorageError as exc:
            raise _storage_failure(exc) from exc
        return entry, checkpoint, False, "Checkpoint belongs to the retired static pipeline or has an unsupported next stage"
    return entry, checkpoint, True, "Explicit user action can resume at the next stage"


@app.get("/runs/{run_id}/recovery")
def get_recovery(run_id: str):
    entry, checkpoint, can_resume, reason = _recovery_record(run_id)
    summary = None
    if checkpoint is not None:
        summary = {key: checkpoint.get(key) for key in
                   ("format_version", "stage", "next_stage", "saved_at", "valid", "invalid_reason")}
    return {"run_id": run_id, "status": entry["status"], "can_resume": can_resume,
            "reason": reason, "checkpoint": summary}


@app.post("/runs/{run_id}/resume", status_code=202)
def resume_run(run_id: str):
    global _active
    if os.name != "nt":
        raise HTTPException(status_code=501, detail="Windows supervisor required")
    entry, checkpoint, can_resume, reason = _recovery_record(run_id)
    if not can_resume:
        raise HTTPException(status_code=409, detail=f"Recovery is unavailable: {reason}")
    root = _validate_workspace(entry["workspace"])
    if entry["model"] not in _installed_models():
        raise HTTPException(status_code=422, detail=f"Model not installed: {entry['model']}")
    log_path = Path(tempfile.gettempdir()) / f"reaper-api-{run_id}-recovery.log"
    cmd = [sys.executable, "-u", "-m", "reaper_ai.supervisor", "--workspace", str(root),
           "--task", entry["task"], "--timeout", str(entry["timeout"])]
    with _lock:
        if _active is not None:
            raise HTTPException(status_code=409, detail=f"Run {_active} is still active")
        try:
            _store.resume_run(run_id)
            with log_path.open("wb") as output:
                env = os.environ.copy()
                env["REAPER_MODEL"] = entry["model"]
                env["REAPER_MODEL_OPTIONS"] = json.dumps(entry.get("model_options") or {
                    "temperature": 0.0, "top_p": 0.9, "top_k": 40, "num_ctx": 16_384
                }, separators=(",", ":"))
                env["REAPER_PROJECT_SYSTEM_PROMPT"] = entry.get("project_system_prompt") or ""
                env["REAPER_API_RUN_ID"] = run_id
                if entry.get("project_id"):
                    env["REAPER_PROJECT_ID"] = entry["project_id"]
                env["REAPER_RESUME"] = "1"
                env["PYTHONUNBUFFERED"] = "1"
                env["PYTHONUTF8"] = "1"
                env["PYTHONIOENCODING"] = "utf-8:backslashreplace"
                proc = subprocess.Popen(cmd, cwd=str(Path(__file__).resolve().parents[2]),
                                        stdout=output, stderr=subprocess.STDOUT, env=env)
        except OSError as exc:
            try:
                _store.update_run_status(run_id, "interrupted")
            except StorageError:
                pass
            raise HTTPException(status_code=500, detail=f"Recovery supervisor could not start: {exc}") from exc
        entry.update({"workspace": str(root), "status": "running", "finished_at": None, "exit_code": None,
                      "result": None, "log_path": str(log_path), "recovery_checkpoint": checkpoint["saved_at"]})
        _runs[run_id] = entry
        _procs[run_id] = proc
        _active = run_id
    threading.Thread(target=_watch, args=(run_id, proc), daemon=True).start()
    return _public(entry)


@app.get("/runs/{run_id}/log")
def get_log(run_id: str):
    entry = _entry(run_id)
    path = entry.get("log_path")
    if not path:
        return {"run_id": run_id, "log": "", "truncated": False,
                "notice": "Raw supervisor logs are not retained after an API restart; use structured events and result."}
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - _MAX_LOG))
            output = f.read().decode("utf-8", "replace")
    except OSError:
        output = ""
    return {"run_id": run_id, "log": output, "truncated": size > _MAX_LOG if output else False}


@app.post("/runs/{run_id}/cancel", status_code=202)
def cancel_run(run_id: str):
    with _lock:
        if run_id not in _runs:
            # A stored run is historical; only a live in-memory process is cancellable.
            try:
                stored = _store.get_run(run_id)
            except StorageError as exc:
                raise _storage_failure(exc) from exc
            if stored is None:
                raise HTTPException(status_code=404, detail="Unknown run")
            raise HTTPException(status_code=409, detail="Run is no longer active")
        entry = _runs[run_id]
        proc = _procs.get(run_id)
        if proc is None or proc.poll() is not None:
            raise HTTPException(status_code=409, detail="Run is no longer active")
        if entry["status"] == "cancelling":
            return {"run_id": run_id, "status": "cancelling"}
        entry["status"] = "cancelling"
        try:
            _store.update_run_status(run_id, "cancelling")
        except StorageError as exc:
            entry["storage_error"] = str(exc)
    # Killing the supervisor closes its Windows Job Object, killing its child tree.
    try:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/F", "/T"],
                       capture_output=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(status_code=500, detail=f"Cancellation failed: {exc}") from exc
    return {"run_id": run_id, "status": "cancelling"}


# REAPER-AI A03 STRUCTURED EVENTS
# Stream only REAPER_EVENT JSON records generated by the engine, never
# infer progress from human-readable stdout/stderr. One reader per client;
# replay from the beginning is intentional for reconnecting local UI clients.
@app.get("/runs/{run_id}/events")
async def stream_events(run_id: str, request: Request):
    entry = _entry(run_id)
    path_value = entry.get("log_path")
    if not path_value:
        try:
            events = _store.list_events(run_id)
        except StorageError as exc:
            raise _storage_failure(exc) from exc

        async def replay():
            for item in events:
                payload = item["payload"] if isinstance(item["payload"], dict) else {"payload": item["payload"]}
                payload["api_run_id"] = run_id
                yield (f"id: {item['sequence']}\nevent: {item['event_type']}\n"
                       f"data: {json.dumps(payload, ensure_ascii=False)}\n\n")

        return StreamingResponse(replay(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    path = Path(path_value)

    async def stream():
        offset = 0
        pending = b""
        emitted_terminal = False
        sequence = 0
        while True:
            if await request.is_disconnected():
                break
            # Do not hold the lock while accessing disk or yielding to clients.
            try:
                with path.open("rb") as f:
                    f.seek(offset)
                    chunk = f.read(64_000)
                    offset = f.tell()
            except OSError:
                chunk = b""
            if chunk:
                pending += chunk
                # Cap a malformed or enormous partial line.
                if len(pending) > 256_000:
                    pending = pending[-256_000:]
                while b"\n" in pending:
                    raw, pending = pending.split(b"\n", 1)
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("REAPER_EVENT:"):
                        continue
                    try:
                        event = json.loads(line[len("REAPER_EVENT:"):].strip())
                    except (ValueError, TypeError):
                        continue
                    if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                        continue
                    sequence += 1
                    if event["type"] == "run_completed":
                        emitted_terminal = True
                    # API run_id is authoritative for run lookup; engine has its own provenance ID.
                    event["api_run_id"] = run_id
                    yield f"id: {sequence}\nevent: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            with _lock:
                run = _runs.get(run_id, entry)
                terminal = run["finished_at"] is not None
                status = run["status"]
                result = run["result"]
                exit_code = run["exit_code"]
            if terminal and not chunk:
                if not emitted_terminal:
                    sequence += 1
                    event = {"type": "run_completed", "api_run_id": run_id,
                             "status": status, "result": result, "exit_code": exit_code,
                             "source": "api"}
                    yield f"id: {sequence}\nevent: run_completed\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                break
            if not chunk:
                yield ": waiting\n\n"
                await asyncio.sleep(0.5)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

# REAPER-AI M4/U01 STATIC UI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

_UI_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(_UI_DIR)), name="static")

@app.get("/", include_in_schema=False)
def ui_index():
    return FileResponse(_UI_DIR / "index.html")

# REAPER-AI M4/U02: bounded, read-only Git diff for finished runs.
@app.get("/runs/{run_id}/diff")
def get_run_diff(run_id: str):
    entry = _entry(run_id)
    if entry["finished_at"] is None:
        raise HTTPException(status_code=409, detail="Run not finished")
    root = Path(entry["workspace"])
    result = entry.get("result") or {}
    paths = result.get("changed_files") or []
    if not isinstance(paths, list) or not paths:
        return {"run_id": run_id, "diff": "", "notice": "No recorded changed files."}
    if not all(isinstance(path, str) and path and not Path(path).is_absolute() and ".." not in Path(path).parts for path in paths):
        raise HTTPException(status_code=500, detail="Invalid paths in run result")
    try:
        proc = subprocess.run(["git", "--no-pager", "diff", "--no-ext-diff", "HEAD", "--", *paths],
                              cwd=root, capture_output=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(status_code=500, detail=f"Git diff unavailable: {exc}") from exc
    if proc.returncode:
        raise HTTPException(status_code=500, detail="Git diff failed")
    limit = 60000
    diff = proc.stdout[:limit].decode("utf-8", "replace")
    suffix = " (truncated)" if len(proc.stdout) > limit else ""
    return {"run_id": run_id, "diff": diff, "notice": "Live Git diff against HEAD, not an immutable snapshot; untracked files not included" + suffix}

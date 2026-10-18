"""M3/A01: minimal, local-only, single-user run API.

In-memory run metadata only (M5 handles persistence). No SSE (A03).
One active run, no publication, and no implicit retries.
"""

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
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

app = FastAPI(title="REAPER-AI", version="0.1.0")
_lock = threading.Lock()
_runs: dict[str, dict] = {}
_active: str | None = None
_procs: dict[str, subprocess.Popen] = {}
_MAX_LOG = 128_000


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


class StartRequest(BaseModel):
    workspace: str = Field(min_length=1)
    task: str = Field(min_length=1, max_length=20_000)
    timeout: int = Field(default=660, ge=1, le=3600)
    model: str | None = Field(default=None, min_length=1, max_length=200)


def _public(entry: dict) -> dict:
    return {k: v for k, v in entry.items() if k not in {"log_path"}}


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
        else:
            entry["status"] = "failed"
        if _active == run_id:
            _active = None
        _procs.pop(run_id, None)


@app.get("/health")
def health():
    return {"service": "reaper-ai", "status": "ok"}


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


def _validate_workspace(workspace: str) -> Path:
    # Workspace must be explicitly supplied as an absolute path, not a path
    # relative to the API process or the current user directory.
    candidate = Path(workspace).expanduser()
    if not candidate.is_absolute():
        raise HTTPException(status_code=422, detail="Workspace must be an absolute path")
    root = candidate.resolve()
    if not root.is_dir():
        raise HTTPException(status_code=422, detail="Workspace directory does not exist")
    try:
        check = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=root,
                               capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(status_code=503, detail=f"Git validation unavailable: {exc}") from exc
    if check.returncode or Path(check.stdout.strip()).resolve() != root:
        raise HTTPException(status_code=422, detail="Workspace must be a Git repository root")
    return root


@app.post("/projects/validate")
def validate_project(request: ProjectRequest):
    root = _validate_workspace(request.workspace)
    return {"workspace": str(root), "valid": True}


@app.post("/runs", status_code=202)
def start_run(request: StartRequest):
    global _active
    if os.name != "nt":
        raise HTTPException(status_code=501, detail="Windows supervisor required")
    root = _validate_workspace(request.workspace)
    # Resolve the model at the start of each run. Never silently use another
    # model if the requested one is missing.
    available = _installed_models()
    default = "qwen3.6:35b"
    model = request.model or default
    if model not in available:
        raise HTTPException(status_code=422, detail=f"Model not installed: {model}")
    run_id = uuid.uuid4().hex[:12]
    log_path = Path(tempfile.gettempdir()) / f"reaper-api-{run_id}.log"
    cmd = [sys.executable, "-u", "-m", "reaper_ai.supervisor", "--workspace", str(root),
           "--task", request.task, "--timeout", str(request.timeout)]
    with _lock:
        if _active is not None:
            raise HTTPException(status_code=409, detail=f"Run {_active} is still active")
        try:
            with log_path.open("wb") as output:
                env = os.environ.copy()
                env["REAPER_MODEL"] = model
                env["PYTHONUNBUFFERED"] = "1"
                proc = subprocess.Popen(cmd, cwd=str(Path(__file__).resolve().parents[2]),
                                        stdout=output, stderr=subprocess.STDOUT, env=env)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Supervisor could not start: {exc}") from exc
        _runs[run_id] = {"run_id": run_id, "workspace": str(root), "model": model, "task": request.task,
                         "status": "running", "started_at": _utc(), "finished_at": None,
                         "exit_code": None, "result": None, "log_path": str(log_path)}
        _procs[run_id] = proc
        _active = run_id
    threading.Thread(target=_watch, args=(run_id, proc), daemon=True).start()
    return _public(_runs[run_id])


@app.get("/runs/{run_id}")
def get_run(run_id: str):
    with _lock:
        if run_id not in _runs:
            raise HTTPException(status_code=404, detail="Unknown run")
        return _public(_runs[run_id])


@app.get("/runs/{run_id}/log")
def get_log(run_id: str):
    with _lock:
        if run_id not in _runs:
            raise HTTPException(status_code=404, detail="Unknown run")
        path = _runs[run_id]["log_path"]
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
            raise HTTPException(status_code=404, detail="Unknown run")
        entry = _runs[run_id]
        proc = _procs.get(run_id)
        if proc is None or proc.poll() is not None:
            raise HTTPException(status_code=409, detail="Run is no longer active")
        if entry["status"] == "cancelling":
            return {"run_id": run_id, "status": "cancelling"}
        entry["status"] = "cancelling"
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
    with _lock:
        if run_id not in _runs:
            raise HTTPException(status_code=404, detail="Unknown run")
        path = Path(_runs[run_id]["log_path"])

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
                run = _runs[run_id]
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
    with _lock:
        if run_id not in _runs:
            raise HTTPException(status_code=404, detail="Unknown run")
        entry = _runs[run_id]
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

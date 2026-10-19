"""Shared state and deterministic dispatch for the iterative Sentinel agent."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, TypedDict

from reaper_ai import system_tools
from reaper_ai import tools

_MAX_CAPTURE_CHARS = 12_000


class AgentState(TypedDict):
    """Serializable state needed by the iterative LangGraph loop."""

    plan: list[dict[str, Any]]
    current_step: int
    next_action: dict[str, Any] | None
    observations: list[dict[str, Any]]
    progress: dict[str, Any]
    verification: dict[str, Any]
    budgets: dict[str, int]


class ToolAction(TypedDict):
    """Uniform request shape accepted by :func:`dispatch_tool`."""

    tool: str
    args: dict[str, Any]
    context: dict[str, Any]


class ToolResult(TypedDict):
    """Observed tool result shape persisted as a compact agent observation."""

    tool: str
    ok: bool
    exit_code: int | None
    stdout: str
    stderr: str
    data: Any
    changes: Any
    error: str
    duration_ms: float


def _compact(value: Any) -> Any:
    """Keep persisted observations useful without retaining unbounded command output."""
    if isinstance(value, str):
        return value[:_MAX_CAPTURE_CHARS]
    if isinstance(value, list):
        return [_compact(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _compact(item) for key, item in value.items()}
    return value


def _result(tool: str, *, ok: bool, exit_code: int | None = None,
            stdout: str = "", stderr: str = "", data: Any = None,
            error: str = "", changes: Any = None,
            started: float | None = None) -> ToolResult:
    return {
        "tool": tool,
        "ok": ok,
        "exit_code": exit_code,
        "stdout": _compact(stdout),
        "stderr": _compact(stderr),
        "data": _compact(data if data is not None else {}),
        "changes": _compact(changes if changes is not None else []),
        "error": error,
        "duration_ms": round((time.monotonic() - started) * 1000, 1) if started else 0,
    }


def _context(action: dict[str, Any]) -> tuple[str, str]:
    context = action.get("context")
    if not isinstance(context, dict):
        raise ValueError("Tool action requires a structured context")
    workspace = context.get("workspace")
    if not isinstance(workspace, str) or not workspace:
        raise ValueError("Tool action context requires a workspace")
    workspace_path = Path(workspace).resolve()
    if not workspace_path.is_dir():
        raise ValueError(f"Workspace is not a directory: {workspace_path}")
    cwd = context.get("cwd", str(workspace_path))
    if not isinstance(cwd, str):
        raise ValueError("Working directory must be a string")
    cwd_path = Path(cwd)
    if not cwd_path.is_absolute():
        cwd_path = workspace_path / cwd_path
    cwd_path = cwd_path.resolve()
    if not cwd_path.is_dir():
        raise ValueError(f"Working directory is not a directory: {cwd_path}")
    return str(workspace_path), str(cwd_path)


def new_agent_state() -> AgentState:
    """Create the minimal state used by a new iterative agent run.

    Checkpoints from earlier runs may contain additional keys.  They remain
    valid because the graph reads only the fields defined here.
    """
    return {
        "plan": [],
        "current_step": 0,
        "next_action": None,
        "observations": [],
        "progress": {
            "last_change": None,
            "checks": [],
            "open_verifications": [],
            "errors": [],
            "last_reason": "",
            "findings": [],
        },
        "verification": {"status": "not_started", "checks": []},
        "budgets": {"tool_calls": 0},
    }


def dispatch_tool(action: ToolAction | dict[str, Any]) -> ToolResult:
    """Execute one validated action and normalize its observed result.

    Actions run with the started user's normal operating-system permissions.
    The dispatcher never elevates permissions; tool failures are returned as
    data for the next agent decision.
    """
    started = time.monotonic()
    tool_name = action.get("tool") if isinstance(action, dict) else None
    if not isinstance(tool_name, str) or not tool_name:
        return _result("unknown", ok=False, error="Tool action requires a tool name", started=started)
    try:
        args = action.get("args", {})
        if not isinstance(args, dict):
            raise ValueError("Tool action arguments must be an object")
        workspace, cwd = _context(action)
        if tool_name == "list_files":
            raw = tools.list_files(workspace, path=args.get("path", "."),
                                   max_results=args.get("max_results"),
                                   include_sizes=args.get("include_sizes", True))
        elif tool_name == "read_file":
            raw = tools.read_file(workspace, path=args["path"], max_chars=args.get("max_chars"))
        elif tool_name == "read_file_range":
            raw = tools.read_file_range(workspace, path=args["path"], start_line=args["start_line"],
                                        end_line=args["end_line"], max_chars=args.get("max_chars"))
            content = raw.get("content") if isinstance(raw, dict) else None
            if isinstance(content, str) and len(content) > _MAX_CAPTURE_CHARS:
                captured = content[:_MAX_CAPTURE_CHARS]
                raw = dict(raw)
                raw["content"] = captured
                raw["truncated"] = True
                raw["captured_end_line"] = (args["start_line"] + captured.count("\n") - 1
                                            if captured.endswith("\n") else args["start_line"] + captured.count("\n"))
        elif tool_name == "search_text":
            raw = tools.search_text(workspace, query=args["query"], path=args.get("path", "."),
                                    max_results=args.get("max_results"),
                                    case_sensitive=args.get("case_sensitive", False))
        elif tool_name == "write_file":
            path = args.get("path")
            content = args.get("content")
            if not isinstance(path, str) or not path:
                raise ValueError("write_file requires a file path")
            if not isinstance(content, str):
                raise ValueError("write_file requires text content")
            # The model's action arguments are the sole source of file content.
            # ``raw`` is deliberately assigned only after the write, so its
            # result metadata can never become an input to ``write_file``.
            raw = tools.write_file(workspace, path=path, content=content,
                                   expected_hash=args.get("expected_hash"))
        elif tool_name == "git_status":
            raw = tools.git_status(cwd)
        elif tool_name == "git_diff":
            raw = tools.git_diff(cwd)
        elif tool_name == "find_python":
            raw = {"ok": True, "executables": system_tools.find_python_executables()}
        elif tool_name == "shell":
            command = args.get("command")
            if not isinstance(command, str) or not command.strip():
                raise ValueError("Shell action requires a non-empty command")
            timeout = args.get("timeout", 60)
            if not isinstance(timeout, int) or not 1 <= timeout <= 600:
                raise ValueError("Shell timeout must be between 1 and 600 seconds")
            raw = system_tools.run_shell_command(command, cwd=cwd, timeout=timeout)
            return _result(tool_name, ok=bool(raw.get("ok")), exit_code=raw.get("returncode"),
                           stdout=raw.get("stdout", ""), stderr=raw.get("stderr", ""),
                           error="" if raw.get("ok") else raw.get("stderr", "Shell command failed"),
                           changes=[], started=started)
        else:
            return _result(tool_name, ok=False, error=f"Unsupported tool: {tool_name}", started=started)

        ok = bool(raw.get("ok"))
        changes = []
        if tool_name == "write_file" and ok and raw.get("changed"):
            changes = [{
                "path": raw.get("path"),
                "before_hash": raw.get("before_hash"),
                "after_hash": raw.get("after_hash"),
            }]
        return _result(tool_name, ok=ok, exit_code=0 if ok else None, data=raw,
                       error="" if ok else str(raw.get("error") or raw.get("output") or "Tool failed"),
                       changes=changes, started=started)
    except (KeyError, OSError, ValueError, TypeError) as exc:
        return _result(tool_name, ok=False, error=str(exc), started=started)

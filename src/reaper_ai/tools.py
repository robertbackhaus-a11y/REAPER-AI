"""Local file and Git tools for REAPER-AI."""

from pathlib import Path
import subprocess


def _file(workspace: str, relative_path: str) -> Path:
    root = Path(workspace).resolve()
    target = (root / relative_path).resolve()

    if not target.is_relative_to(root) or target == root:
        raise ValueError("Path outside workspace or invalid file path")

    if any(part.lower() in {".git", ".venv"} for part in target.relative_to(root).parts):
        raise ValueError("Protected directory")

    return target


def read_file(workspace: str, path: str, max_chars: int = 12000) -> dict:
    try:
        target = _file(workspace, path)
        content = target.read_text(encoding="utf-8")
        if len(content) > max_chars:
            return {
                "ok": True,
                "path": path,
                "content": content[:max_chars],
                "truncated": True,
            }
        return {"ok": True, "path": path, "content": content, "truncated": False}
    except (OSError, UnicodeError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


def write_file(workspace: str, path: str, content: str,
               overwrite: bool = False) -> dict:
    try:
        target = _file(workspace, path)
        if target.exists() and not overwrite:
            return {"ok": False, "error": "File exists; overwrite not permitted"}
        if target.exists() and not target.is_file():
            return {"ok": False, "error": "Target is not a regular file"}

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"ok": True, "path": path, "bytes": target.stat().st_size}
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


def git_status(workspace: str) -> dict:
    try:
        result = subprocess.run(
            ["git", "status", "--short", "--untracked-files=all"],
            cwd=workspace,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        return {
            "ok": result.returncode == 0,
            "output": result.stdout if result.returncode == 0 else result.stderr,
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": str(exc)}


def git_diff(workspace: str) -> dict:
    try:
        result = subprocess.run(
            ["git", "--no-pager", "diff", "--no-ext-diff", "--"],
            cwd=workspace,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        return {
            "ok": result.returncode == 0,
            "diff": result.stdout if result.returncode == 0 else "",
            "error": result.stderr if result.returncode else "",
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": str(exc)}

"""Local file and Git tools for REAPER-AI."""

from pathlib import Path
import os
import subprocess
import hashlib


def _workspace_target(workspace: str, path: str, *, allow_root: bool = False) -> Path:
    """Resolve a tool path, using *workspace* only for relative paths.

    The agent deliberately has the permissions of the process that started it.
    A workspace is useful project context, not a filesystem sandbox.  Operating
    system permissions remain the authority for paths outside that context.
    """
    root = Path(workspace).resolve()
    candidate = Path(path).expanduser()
    target = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    if target == root and not allow_root:
        raise ValueError("A file path, not the workspace root, is required")
    return target


def _file(workspace: str, relative_path: str) -> Path:
    return _workspace_target(workspace, relative_path)


def _display_path(workspace: str, target: Path) -> str:
    """Use a concise relative path when possible, otherwise retain the absolute path."""
    try:
        return target.relative_to(Path(workspace).resolve()).as_posix()
    except ValueError:
        return str(target)


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _git_ignored_directories(workspace: str, directory: Path) -> set[str]:
    """Return directory names that Git ignores directly below *directory*.

    The result is used only to prune ordinary project scans.  It is intentionally
    limited to names that Git reports as ignored at the requested directory level,
    so explicit scans of those directories remain possible.
    """
    try:
        workspace_root = Path(workspace).resolve()
        directory = directory.resolve()
        try:
            rel_dir = directory.relative_to(workspace_root)
        except ValueError:
            rel_dir = Path()
        names = os.listdir(directory)
        if not names:
            return set()
        if rel_dir.parts:
            prefix = rel_dir.as_posix() + "/"
        else:
            prefix = ""
        args = ["git", "check-ignore", "--no-index"]
        for name in names:
            args.append(prefix + name)
        result = subprocess.run(
            args,
            cwd=workspace,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    ignored = set()
    for line in result.stdout.splitlines():
        name = line.strip()
        if not name:
            continue
        if prefix and name.startswith(prefix):
            name = name[len(prefix):]
        if name and (directory / name).is_dir():
            ignored.add(name)
    return ignored


def _is_git_ignored_directory(workspace: str, directory: Path) -> bool:
    """Return whether an explicitly requested directory is itself Git-ignored."""
    try:
        workspace_root = Path(workspace).resolve()
        relative = directory.resolve().relative_to(workspace_root)
        if not relative.parts:
            return False
        result = subprocess.run(
            ["git", "check-ignore", "--quiet", "--no-index", relative.as_posix()],
            cwd=workspace,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        return result.returncode == 0
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


def _iter_regular_files(directory: Path, workspace: str | None = None):
    """Traverse regular files while omitting Git-ignored generated directories.

    Ordinary project scans skip directories that Git reports as ignored, such as
    `.venv`, `__pycache__`, `dist`, and `build`.  Explicitly targeting `.git`,
    `.venv`, or any other ignored directory still exposes its complete recursive
    contents, including Git-ignored descendants.
    """
    include_git_metadata = ".git" in directory.parts
    explicit_ignored_directory = bool(workspace and _is_git_ignored_directory(workspace, directory))
    for current, directories, names in os.walk(directory):
        if not include_git_metadata and not explicit_ignored_directory:
            if workspace is not None:
                ignored_names = _git_ignored_directories(workspace, Path(current))
            else:
                ignored_names = set()
            directories[:] = [name for name in directories if name != ".git" and name not in ignored_names]
        directories.sort()
        for name in sorted(names):
            target = Path(current, name)
            if target.is_file():
                yield target


def list_files(workspace: str, path: str = ".", max_results: int | None = None,
               include_sizes: bool = True) -> dict:
    """List regular files below a directory, optionally with caller-requested truncation.

    When *include_sizes* is False each entry contains only ``path`` without ``bytes``.
    """
    try:
        if max_results is not None and (not isinstance(max_results, int) or max_results < 1):
            raise ValueError("max_results must be positive when supplied")
        directory = _workspace_target(workspace, path, allow_root=True)
        if not directory.is_dir():
            raise ValueError("Path is not a directory")
        files = []
        truncated = False
        for target in _iter_regular_files(directory, workspace):
            if max_results is not None and len(files) >= max_results:
                truncated = True
                break
            entry = {"path": _display_path(workspace, target)}
            if include_sizes:
                entry["bytes"] = target.stat().st_size
            files.append(entry)
        return {"ok": True, "files": files, "truncated": truncated}
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": str(exc), "files": [], "truncated": False}


def read_file(workspace: str, path: str, max_chars: int | None = None) -> dict:
    try:
        if max_chars is not None and (not isinstance(max_chars, int) or max_chars < 1):
            raise ValueError("max_chars must be positive when supplied")
        target = _file(workspace, path)
        content = target.read_text(encoding="utf-8")
        shown_path = _display_path(workspace, target)
        digest = _sha256(content)
        if max_chars is not None and len(content) > max_chars:
            return {
                "ok": True,
                "path": shown_path,
                "content": content[:max_chars],
                "truncated": True,
                "sha256": digest,
            }
        return {"ok": True, "path": shown_path, "content": content, "truncated": False,
                "sha256": digest}
    except (OSError, UnicodeError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


def read_file_range(workspace: str, path: str, start_line: int, end_line: int,
                    max_chars: int | None = None) -> dict:
    """Read a line range without decoding unrelated source sections into context."""
    try:
        if not isinstance(start_line, int) or not isinstance(end_line, int) or start_line < 1 or end_line < start_line:
            raise ValueError("Line range must start at 1 and end after it")
        if max_chars is not None and (not isinstance(max_chars, int) or max_chars < 1):
            raise ValueError("max_chars must be positive when supplied")
        target = _file(workspace, path)
        source_content = target.read_text(encoding="utf-8")
        content = []
        used = 0
        truncated = False
        for line_number, line in enumerate(source_content.splitlines(keepends=True), start=1):
            if line_number < start_line:
                continue
            if line_number > end_line:
                break
            if max_chars is not None:
                remaining = max_chars - used
                if remaining <= 0:
                    truncated = True
                    break
                if len(line) > remaining:
                    content.append(line[:remaining])
                    truncated = True
                    break
            content.append(line)
            used += len(line)
        return {"ok": True, "path": _display_path(workspace, target), "start_line": start_line,
                "end_line": end_line, "content": "".join(content), "truncated": truncated,
                "sha256": _sha256(source_content)}
    except (OSError, UnicodeError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


def search_text(workspace: str, query: str, path: str = ".", max_results: int | None = None,
                case_sensitive: bool = False) -> dict:
    """Search UTF-8 project text line-by-line and return source locations, not whole files."""
    try:
        if not isinstance(query, str) or not query:
            raise ValueError("Search query must not be empty")
        if max_results is not None and (not isinstance(max_results, int) or max_results < 1):
            raise ValueError("max_results must be positive when supplied")
        directory = _workspace_target(workspace, path, allow_root=True)
        if not directory.is_dir():
            raise ValueError("Search path is not a directory")
        needle = query if case_sensitive else query.casefold()
        matches = []
        truncated = False
        for target in _iter_regular_files(directory, workspace):
            try:
                with target.open("r", encoding="utf-8") as source:
                    for line_number, line in enumerate(source, start=1):
                        haystack = line if case_sensitive else line.casefold()
                        if needle not in haystack:
                            continue
                        if max_results is not None and len(matches) >= max_results:
                            truncated = True
                            break
                        matches.append({"path": _display_path(workspace, target), "line": line_number,
                                        "text": line.rstrip("\r\n")[:500]})
            except (OSError, UnicodeError):
                continue
            if truncated:
                break
        return {"ok": True, "matches": matches, "truncated": truncated}
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": str(exc), "matches": [], "truncated": False}


def write_file(workspace: str, path: str, content: str,
               expected_hash: str | None = None) -> dict:
    """Write UTF-8 text after an optional/required existing-content hash check.

    Existing files require the hash returned by a read operation.  This is a
    conflict check rather than a policy restriction: it prevents an agent from
    silently overwriting work that changed after it inspected the file.
    """
    try:
        target = _file(workspace, path)
        if target.exists() and not target.is_file():
            return {"ok": False, "error": "Target is not a regular file"}
        before_hash = None
        if target.exists():
            before_content = target.read_text(encoding="utf-8")
            before_hash = _sha256(before_content)
            if not isinstance(expected_hash, str) or not expected_hash:
                return {"ok": False, "error": "Existing file requires expected_hash from a prior read"}
            if expected_hash != before_hash:
                return {"ok": False, "error": "Source hash conflict: file changed since it was read"}
        if not isinstance(content, str):
            raise ValueError("File content must be text")

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"ok": True, "path": _display_path(workspace, target), "bytes": target.stat().st_size,
                "before_hash": before_hash, "after_hash": _sha256(content),
                "changed": before_hash != _sha256(content)}
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

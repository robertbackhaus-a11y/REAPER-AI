# Copyright (c) 2026 Robert Backhaus

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


def _iter_regular_files(directory: Path, workspace: str | None = None,
                        excluded_directories: set[str] | None = None):
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
        if excluded_directories:
            directories[:] = [name for name in directories if name not in excluded_directories]
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
        shown = content if max_chars is None else content[:max_chars]
        captured_end_line = (shown.count("\n") if shown.endswith("\n") else shown.count("\n") + 1)
        metadata = {"path": shown_path, "start_line": 1, "end_line": captured_end_line,
                    "original_length": len(content), "captured_length": len(shown), "sha256": digest}
        if max_chars is not None and len(content) > max_chars:
            return {"ok": True, **metadata, "content": shown, "truncated": True}
        return {"ok": True, **metadata, "content": shown, "truncated": False}
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
        lines = source_content.splitlines(keepends=True)
        if start_line > len(lines):
            raise ValueError(f"start_line {start_line} is beyond end of file ({len(lines)} lines)")
        content = []
        used = 0
        truncated = False
        last_line = start_line - 1
        for line_number, line in enumerate(lines, start=1):
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
            last_line = line_number
        return {"ok": True, "path": _display_path(workspace, target), "start_line": start_line,
                "end_line": end_line, "last_line": last_line, "content": "".join(content), "truncated": truncated,
                "sha256": _sha256(source_content)}
    except (OSError, UnicodeError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


def search_text(workspace: str, query: str, path: str = ".", max_results: int | None = None,
                case_sensitive: bool = False) -> dict:
    """Search one UTF-8 text file or every regular file below a directory."""
    try:
        if not isinstance(query, str) or not query:
            raise ValueError("Search query must not be empty")
        if max_results is not None and (not isinstance(max_results, int) or max_results < 1):
            raise ValueError("max_results must be positive when supplied")
        target = _workspace_target(workspace, path, allow_root=True)
        if target.is_file():
            targets = [target]
        elif target.is_dir():
            targets = _iter_regular_files(target, workspace)
        else:
            raise ValueError("Search path is neither a file nor a directory")
        needle = query if case_sensitive else query.casefold()
        matches = []
        truncated = False
        for file_target in targets:
            try:
                with file_target.open("r", encoding="utf-8") as source:
                    for line_number, line in enumerate(source, start=1):
                        haystack = line if case_sensitive else line.casefold()
                        if needle not in haystack:
                            continue
                        if max_results is not None and len(matches) >= max_results:
                            truncated = True
                            break
                        matches.append({"path": _display_path(workspace, file_target), "line": line_number,
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


def apply_patch(workspace: str, path: str, expected_text: str, replacement: str,
                expected_hash: str | None = None) -> dict:
    """Replace one exact text occurrence in an existing UTF-8 file.

    The original file hash and the unique expected text make the write
    conflict-safe without transferring the complete file to the model.
    """
    try:
        if not isinstance(expected_text, str) or not expected_text:
            raise ValueError("apply_patch requires non-empty expected_text")
        if not isinstance(replacement, str):
            raise ValueError("apply_patch requires text replacement")
        target = _file(workspace, path)
        if not target.exists() or not target.is_file():
            return {"ok": False, "error": "apply_patch requires an existing regular file"}
        content = target.read_text(encoding="utf-8")
        before_hash = _sha256(content)
        if not isinstance(expected_hash, str) or not expected_hash:
            return {"ok": False, "error": "Existing file requires expected_hash from a prior read"}
        if expected_hash != before_hash:
            return {"ok": False, "error": "Source hash conflict: file changed since it was read"}
        first = content.find(expected_text)
        if first < 0:
            return {"ok": False, "error": "Expected text was not found"}
        if content.find(expected_text, first + 1) >= 0:
            return {"ok": False, "error": "Expected text must occur exactly once"}
        updated = content[:first] + replacement + content[first + len(expected_text):]
        target.write_text(updated, encoding="utf-8")
        after_hash = _sha256(updated)
        return {"ok": True, "path": _display_path(workspace, target), "bytes": target.stat().st_size,
                "before_hash": before_hash, "after_hash": after_hash, "changed": before_hash != after_hash}
    except (OSError, UnicodeError, ValueError) as exc:
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


def git_info(workspace: str) -> dict:
    """Return the current Git branch and commit when *workspace* is a repository."""
    try:
        branch = subprocess.run(
            ["git", "branch", "--show-current"], cwd=workspace, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=15,
        )
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=workspace, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=15,
        )
        if branch.returncode or head.returncode:
            return {"ok": False, "branch": None, "head": None,
                    "error": (head.stderr or branch.stderr).strip()}
        return {"ok": True, "branch": branch.stdout.strip() or None, "head": head.stdout.strip() or None}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "branch": None, "head": None, "error": str(exc)}


_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
_STATUS_LABELS = {"M": "modified", "T": "modified", "A": "added", "D": "deleted",
                  "R": "renamed", "C": "copied", "U": "conflict", "?": "untracked"}


def _git_read(args: list[str], cwd: str | Path) -> subprocess.CompletedProcess:
    """Run a read-only Git command without taking optional index locks."""
    return subprocess.run(["git", "--no-optional-locks", *args], cwd=cwd, capture_output=True,
                          timeout=15)


def _git_text(proc: subprocess.CompletedProcess, stream: str = "stdout") -> str:
    return getattr(proc, stream).decode("utf-8", "replace").strip()


def _git_commit(workspace: str, ref: str) -> str | None:
    """Resolve *ref* to a commit hash; option-like or unknown refs yield None."""
    if not ref or ref.startswith("-"):
        return None
    proc = _git_read(["rev-parse", "--verify", "--quiet", "--end-of-options", f"{ref}^{{commit}}"],
                     workspace)
    return _git_text(proc) if proc.returncode == 0 else None


def git_refs(workspace: str, max_commits: int = 200) -> dict:
    """List local commits touching *workspace* and local tags, newest first."""
    try:
        top = _git_read(["rev-parse", "--show-toplevel"], workspace)
        if top.returncode:
            return {"ok": True, "git": False, "commits": [], "tags": []}
        log = _git_read(["log", f"-n{max_commits}", "--format=%H%x1f%h%x1f%cI%x1f%s", "--", "."],
                        workspace)
        tags = _git_read(["tag", "--list", "--sort=-creatordate"], workspace)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": str(exc)}
    commits = []
    for line in _git_text(log).splitlines() if log.returncode == 0 else []:
        sha, short, date, subject = (line.split("\x1f") + ["", "", ""])[:4]
        commits.append({"sha": sha, "short": short, "date": date, "subject": subject})
    return {"ok": True, "git": True, "commits": commits,
            "tags": _git_text(tags).splitlines() if tags.returncode == 0 else []}


def _diff_range(workspace: str, mode: str, ref: str | None, include_worktree: bool) -> list[str] | str:
    """Return the `git diff` revision arguments of a comparison, or an error message."""
    if mode == "worktree":
        head = _git_read(["rev-parse", "--verify", "--quiet", "HEAD"], workspace)
        return ["HEAD" if head.returncode == 0 else _EMPTY_TREE]
    if mode == "commit":
        commit = _git_commit(workspace, ref or "")
        if commit is None:
            return "Unknown commit"
        return [_git_commit(workspace, f"{commit}^") or _EMPTY_TREE, commit]
    if mode == "release":
        tag = _git_commit(workspace, f"refs/tags/{ref}") if ref and not ref.startswith("-") else None
        if tag is None:
            return "Unknown tag"
        return [tag] if include_worktree else [tag, "HEAD"]
    return "Unknown comparison mode"


def git_changes(workspace: str, mode: str = "worktree", ref: str | None = None,
                untracked: bool = True, include_worktree: bool = False,
                max_files: int = 2_000) -> dict:
    """List the changed files of a read-only Git comparison.

    ``worktree`` compares the working tree with HEAD (untracked files only when
    *untracked*), ``commit`` compares commit *ref* with its first parent, and
    ``release`` compares tag *ref* with HEAD or, with *include_worktree*, with
    the tracked working tree. Paths are repository-relative and limited to
    *workspace*. Nothing is staged, checked out, reset, or otherwise written.
    """
    try:
        top = _git_read(["rev-parse", "--show-toplevel"], workspace)
        if top.returncode:
            return {"ok": True, "git": False, "root": None, "files": [], "truncated": False, "total": 0}
        root = _git_text(top)
        if mode == "worktree":
            proc = _git_read(["status", "--porcelain=v1", "-z", "--untracked-files=all", "--", "."],
                             workspace)
        else:
            revisions = _diff_range(workspace, mode, ref, include_worktree)
            if isinstance(revisions, str):
                return {"ok": False, "error": revisions}
            proc = _git_read(["diff", "--name-status", "-z", "-M", "--no-ext-diff", *revisions, "--", "."],
                             workspace)
        if proc.returncode:
            return {"ok": False, "error": _git_text(proc, "stderr")}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": str(exc)}
    entries = proc.stdout.decode("utf-8", "replace").split("\0")
    files, index = [], 0
    while index < len(entries) and entries[index]:
        if mode == "worktree":
            entry = entries[index]
            code, path, old_path = entry[:2], entry[3:], None
            index += 1
            if "R" in code or "C" in code:
                old_path = entries[index] if index < len(entries) else None
                index += 1
            letter = "U" if "U" in code or code in {"AA", "DD"} else next(
                (char for char in (code[0], code[1]) if char not in {" ", "!"}), "M")
        else:
            code, letter, old_path = entries[index], entries[index][:1], None
            if letter in {"R", "C"}:
                old_path, path = entries[index + 1], entries[index + 2]
                index += 3
            else:
                path = entries[index + 1]
                index += 2
            code = letter
        if letter == "?" and not untracked:
            continue
        files.append({"path": path, "old_path": old_path, "code": code.strip() or "M",
                      "status": _STATUS_LABELS.get(letter, "modified")})
    files.sort(key=lambda item: item["path"])
    return {"ok": True, "git": True, "root": root, "files": files[:max_files],
            "truncated": len(files) > max_files, "total": len(files)}


def git_file_diff(workspace: str, path: str, mode: str = "worktree", ref: str | None = None,
                  include_worktree: bool = False, max_bytes: int = 200_000) -> dict:
    """Return the unified diff of one file listed by the same comparison (read-only)."""
    changes = git_changes(workspace, mode, ref, include_worktree=include_worktree,
                          max_files=1_000_000)
    if not changes.get("ok"):
        return changes
    if not changes.get("git"):
        return {"ok": False, "error": "Workspace is not a Git repository"}
    entry = next((item for item in changes["files"] if item["path"] == path), None)
    if entry is None:
        return {"ok": False, "error": "File has no changes in this comparison"}
    root = changes["root"]
    try:
        if entry["status"] == "untracked":
            proc = _git_read(["diff", "--no-index", "--no-ext-diff", "--no-color", "--",
                              "/dev/null", path], root)
            failed = proc.returncode not in {0, 1}
        else:
            revisions = _diff_range(root, mode, ref, include_worktree)
            paths = [path] + ([entry["old_path"]] if entry["old_path"] else [])
            proc = _git_read(["diff", "--no-ext-diff", "--no-color", "-M", *revisions, "--", *paths], root)
            failed = proc.returncode != 0
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": str(exc)}
    if failed:
        return {"ok": False, "error": _git_text(proc, "stderr") or "Git diff failed"}
    return {"ok": True, **entry, "diff": proc.stdout[:max_bytes].decode("utf-8", "replace"),
            "truncated": len(proc.stdout) > max_bytes}


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

"""Incremental, project-scoped repository metadata and Python symbol index."""

from __future__ import annotations

import ast
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import tools
from .persistence import Store, utc_now


_SKIPPED_DIRECTORIES = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".cache", ".tox", ".nox", ".eggs", ".next",
    "build", "dist", "out", "target", "bin", "obj", "coverage", "htmlcov", "_build",
}
_LANGUAGES = {
    ".py": "python", ".pyi": "python", ".js": "javascript", ".mjs": "javascript",
    ".cjs": "javascript", ".ts": "typescript", ".tsx": "typescript", ".jsx": "javascript",
    ".json": "json", ".toml": "toml", ".yaml": "yaml", ".yml": "yaml", ".md": "markdown",
    ".html": "html", ".css": "css", ".scss": "scss", ".java": "java", ".go": "go",
    ".rs": "rust", ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp",
    ".cs": "csharp", ".sh": "shell", ".ps1": "powershell", ".sql": "sql",
}


class RepositoryIndexError(RuntimeError):
    """A workspace could not be scanned into the local repository index."""


def _language(path: Path) -> str:
    return _LANGUAGES.get(path.suffix.lower(), path.suffix.lower().lstrip(".") or "text")


def _line_count(content: bytes) -> int:
    if not content:
        return 0
    return content.count(b"\n") + (0 if content.endswith(b"\n") else 1)


def _module_names(path: str) -> set[str]:
    relative = Path(path).with_suffix("")
    parts = relative.parts
    candidates = {".".join(parts)}
    if len(parts) > 1 and parts[0] == "src":
        candidates.add(".".join(parts[1:]))
    if parts and parts[-1] == "__init__":
        candidates.add(".".join(parts[:-1]))
        if len(parts) > 2 and parts[0] == "src":
            candidates.add(".".join(parts[1:-1]))
    return {candidate for candidate in candidates if candidate}


class _PythonSymbols(ast.NodeVisitor):
    def __init__(self) -> None:
        self.symbols: list[dict[str, Any]] = []
        self.imports: list[dict[str, Any]] = []
        self._scope: list[tuple[str, str]] = []

    def _add_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef, *, asynchronous: bool) -> None:
        inside_class = any(kind == "class" for _, kind in self._scope)
        kind = "async_method" if asynchronous and inside_class else "async_function" if asynchronous else "method" if inside_class else "function"
        try:
            signature = ast.unparse(node.args)
        except (ValueError, TypeError):
            signature = ""
        qualifier = ".".join([name for name, _ in self._scope] + [node.name])
        self.symbols.append({"name": node.name, "qualname": qualifier, "kind": kind,
                             "signature": f"({signature})", "start_line": node.lineno,
                             "end_line": getattr(node, "end_lineno", node.lineno)})
        self._scope.append((node.name, kind))
        self.generic_visit(node)
        self._scope.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qualifier = ".".join([name for name, _ in self._scope] + [node.name])
        self.symbols.append({"name": node.name, "qualname": qualifier, "kind": "class", "signature": None,
                             "start_line": node.lineno, "end_line": getattr(node, "end_lineno", node.lineno)})
        self._scope.append((node.name, "class"))
        self.generic_visit(node)
        self._scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._add_function(node, asynchronous=False)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._add_function(node, asynchronous=True)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append({"module": alias.name, "imported_name": alias.asname or "", "local_module": None,
                                 "line": node.lineno})

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = "." * node.level + (node.module or "")
        for alias in node.names:
            self.imports.append({"module": module, "imported_name": alias.name, "local_module": None,
                                 "line": node.lineno})


def _parse_python(content: bytes, path: str) -> tuple[list[dict], list[dict]]:
    try:
        tree = ast.parse(content.decode("utf-8"), filename=path)
    except (SyntaxError, UnicodeDecodeError):
        return [], []
    visitor = _PythonSymbols()
    visitor.visit(tree)
    return visitor.symbols, visitor.imports


def refresh_project_index(store: Store, project: dict) -> dict:
    """Scan a project workspace, reparsing only files whose content changed."""
    project_id = project["project_id"]
    root = Path(project["workspace"]).expanduser().resolve()
    if not root.is_dir():
        raise RepositoryIndexError(f"Workspace directory does not exist: {root}")

    known = {entry["path"]: entry for entry in store.index_files(project_id)}
    seen: set[str] = set()
    modules: set[str] = set()
    updated = parsed = unchanged = 0
    try:
        files = tools._iter_regular_files(root, str(root), excluded_directories=_SKIPPED_DIRECTORIES)
        for target in files:
            relative = target.relative_to(root).as_posix()
            seen.add(relative)
            try:
                content = target.read_bytes()
                stat = target.stat()
            except OSError:
                # An inaccessible file is neither parsed nor treated as deleted.
                continue
            digest = hashlib.sha256(content).hexdigest()
            record = {"path": relative, "sha256": digest, "language": _language(target),
                      "size_bytes": stat.st_size, "line_count": _line_count(content),
                      "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()}
            if record["language"] == "python":
                modules.update(_module_names(relative))
            existing = known.get(relative)
            if existing and existing["sha256"] == digest:
                store.update_index_file_metadata(project_id, record)
                unchanged += 1
                continue
            symbols, imports = _parse_python(content, relative) if record["language"] == "python" else ([], [])
            store.replace_index_file(project_id, record, symbols, imports)
            updated += 1
            parsed += int(record["language"] == "python")
    except OSError as exc:
        raise RepositoryIndexError(f"Could not scan workspace {root}: {exc}") from exc

    removed = sorted(set(known) - seen)
    store.remove_index_paths(project_id, removed)
    store.refresh_index_import_relations(project_id, modules)
    git = tools.git_info(str(root))
    scanned_at = utc_now()
    store.set_index_state(project_id, workspace=str(root), scanned_at=scanned_at,
                          git_branch=git.get("branch"), git_head=git.get("head"))
    return {"project_id": project_id, "scanned_at": scanned_at, "file_count": len(seen),
            "updated_files": updated, "parsed_files": parsed, "unchanged_files": unchanged,
            "removed_files": len(removed), "git_branch": git.get("branch"), "git_head": git.get("head")}

"""Small SQLite store for local conversation, run history, and recovery checkpoints."""

from __future__ import annotations

import json
import hashlib
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class StorageError(RuntimeError):
    """A local database operation could not be completed."""


_MAX_EVENT_BYTES = 32_000
_CHECKPOINT_VERSION = 1
_PROJECT_MIGRATION_KEY = "project_core_v1"
_REPOSITORY_INDEX_MIGRATION_KEY = "repository_index_v1"
_DEFAULT_MODEL = "qwen3.8-q5:latest"
_DEFAULT_MODEL_OPTIONS = {"temperature": 0.0, "top_p": 0.9, "top_k": 40, "num_ctx": 16_384}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_database_path() -> Path:
    """Return an application-data path, never a path in a target project."""
    configured = os.environ.get("REAPER_DATA_DIR")
    if configured:
        return Path(configured).expanduser() / "reaper-ai.sqlite3"
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "REAPER-AI" / "reaper-ai.sqlite3"
    return Path.home() / ".local" / "share" / "REAPER-AI" / "reaper-ai.sqlite3"


class Store:
    """One-file SQLite persistence for the local, single-user API."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_database_path()

    @contextmanager
    def _connect(self):
        connection: sqlite3.Connection | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path, timeout=5)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            self._initialize(connection)
            yield connection
            connection.commit()
        except (OSError, sqlite3.Error) as exc:
            if connection is not None:
                connection.rollback()
            raise StorageError(f"SQLite store unavailable at {self.path}: {exc}") from exc
        except Exception:
            if connection is not None:
                connection.rollback()
            raise
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _initialize(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS projects (
                project_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                workspace TEXT NOT NULL,
                description TEXT,
                system_prompt TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT 'qwen3.8-q5:latest',
                temperature REAL NOT NULL DEFAULT 0.0,
                top_p REAL NOT NULL DEFAULT 0.9,
                top_k INTEGER NOT NULL DEFAULT 40,
                num_ctx INTEGER NOT NULL DEFAULT 16384,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                title TEXT NOT NULL,
                workspace TEXT,
                model TEXT
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                run_id TEXT,
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                metadata_json TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS messages_conversation_created
                ON messages(conversation_id, id);
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                conversation_id TEXT REFERENCES conversations(id) ON DELETE SET NULL,
                workspace TEXT NOT NULL,
                model TEXT NOT NULL,
                task TEXT NOT NULL,
                timeout_seconds INTEGER NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                exit_code INTEGER,
                result_json TEXT
            );
            CREATE INDEX IF NOT EXISTS runs_conversation_started
                ON runs(conversation_id, started_at DESC);
            CREATE TABLE IF NOT EXISTS execution_events (
                run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                sequence INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (run_id, sequence)
            );
            CREATE TABLE IF NOT EXISTS stage_results (
                run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                sequence INTEGER NOT NULL,
                stage TEXT NOT NULL,
                status TEXT,
                recorded_at TEXT NOT NULL,
                result_json TEXT NOT NULL,
                PRIMARY KEY (run_id, sequence)
            );
            CREATE TABLE IF NOT EXISTS checkpoints (
                run_id TEXT PRIMARY KEY REFERENCES runs(run_id) ON DELETE CASCADE,
                format_version INTEGER NOT NULL,
                stage TEXT NOT NULL,
                next_stage TEXT NOT NULL,
                saved_at TEXT NOT NULL,
                state_json TEXT NOT NULL,
                stage_result_json TEXT NOT NULL,
                state_digest TEXT NOT NULL,
                valid INTEGER NOT NULL DEFAULT 1,
                invalid_reason TEXT
            );
            CREATE TABLE IF NOT EXISTS durable_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace TEXT,
                kind TEXT NOT NULL CHECK (kind IN ('user_decision', 'verified_result')),
                content TEXT NOT NULL,
                source_run_id TEXT REFERENCES runs(run_id) ON DELETE SET NULL,
                source_conversation_id TEXT REFERENCES conversations(id) ON DELETE SET NULL,
                verification TEXT NOT NULL CHECK (verification IN ('user_confirmed', 'observed')),
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS durable_memory_scope_created
                ON durable_memory(workspace, created_at DESC);
            CREATE UNIQUE INDEX IF NOT EXISTS durable_memory_run_result
                ON durable_memory(source_run_id) WHERE kind = 'verified_result';
            CREATE TABLE IF NOT EXISTS project_index_state (
                project_id TEXT PRIMARY KEY REFERENCES projects(project_id) ON DELETE CASCADE,
                workspace TEXT NOT NULL,
                scanned_at TEXT NOT NULL,
                git_branch TEXT,
                git_head TEXT
            );
            CREATE TABLE IF NOT EXISTS project_index_files (
                project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
                path TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                language TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                line_count INTEGER NOT NULL,
                modified_at TEXT NOT NULL,
                PRIMARY KEY (project_id, path)
            );
            CREATE TABLE IF NOT EXISTS project_index_symbols (
                project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
                path TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                name TEXT NOT NULL,
                qualname TEXT NOT NULL,
                kind TEXT NOT NULL,
                signature TEXT,
                start_line INTEGER NOT NULL,
                end_line INTEGER NOT NULL,
                PRIMARY KEY (project_id, path, qualname, kind, start_line)
            );
            CREATE INDEX IF NOT EXISTS project_index_symbols_name
                ON project_index_symbols(project_id, name);
            CREATE TABLE IF NOT EXISTS project_index_imports (
                project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
                path TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                module TEXT NOT NULL,
                imported_name TEXT NOT NULL DEFAULT '',
                local_module TEXT,
                line INTEGER NOT NULL,
                PRIMARY KEY (project_id, path, module, imported_name, line)
            );
            CREATE INDEX IF NOT EXISTS project_index_imports_path
                ON project_index_imports(project_id, path);
            """
        )
        Store._add_column(connection, "conversations", "project_id", "TEXT")
        Store._add_column(connection, "runs", "project_id", "TEXT")
        Store._add_column(connection, "runs", "model_options_json", "TEXT")
        Store._add_column(connection, "runs", "project_system_prompt", "TEXT")
        Store._add_column(connection, "durable_memory", "project_id", "TEXT")
        connection.execute("CREATE INDEX IF NOT EXISTS conversations_project_updated ON conversations(project_id, updated_at DESC)")
        connection.execute("CREATE INDEX IF NOT EXISTS runs_project_started ON runs(project_id, started_at DESC)")
        connection.execute("CREATE INDEX IF NOT EXISTS durable_memory_project_created ON durable_memory(project_id, created_at DESC)")
        Store._migrate_project_core(connection)
        connection.execute("INSERT OR IGNORE INTO schema_meta(key, value) VALUES(?, ?)",
                           (_REPOSITORY_INDEX_MIGRATION_KEY, utc_now()))

    @staticmethod
    def _add_column(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    @staticmethod
    def _migrate_project_core(connection: sqlite3.Connection) -> None:
        """Create one legacy project per historical workspace exactly once."""
        if connection.execute("SELECT 1 FROM schema_meta WHERE key = ?", (_PROJECT_MIGRATION_KEY,)).fetchone():
            return
        workspaces: set[str] = set()
        for table in ("conversations", "runs", "durable_memory"):
            rows = connection.execute(f"SELECT DISTINCT workspace FROM {table} WHERE workspace IS NOT NULL AND trim(workspace) <> ''").fetchall()
            workspaces.update(str(row["workspace"]) for row in rows)
        projects: dict[str, str] = {}
        now = utc_now()
        for workspace in sorted(workspaces, key=str.casefold):
            project_id = uuid.uuid4().hex
            name = Path(workspace).name or workspace
            connection.execute(
                "INSERT INTO projects(project_id, name, workspace, description, system_prompt, model, temperature, top_p, top_k, num_ctx, created_at, updated_at) "
                "VALUES(?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?)",
                (project_id, name[:200], workspace, "Migrated legacy workspace", _DEFAULT_MODEL,
                 _DEFAULT_MODEL_OPTIONS["temperature"], _DEFAULT_MODEL_OPTIONS["top_p"],
                 _DEFAULT_MODEL_OPTIONS["top_k"], _DEFAULT_MODEL_OPTIONS["num_ctx"], now, now),
            )
            projects[workspace] = project_id
        for workspace, project_id in projects.items():
            connection.execute("UPDATE runs SET project_id = ? WHERE project_id IS NULL AND workspace = ?", (project_id, workspace))
            connection.execute("UPDATE durable_memory SET project_id = ? WHERE project_id IS NULL AND workspace = ?", (project_id, workspace))
        conversations = connection.execute("SELECT id, workspace FROM conversations WHERE project_id IS NULL").fetchall()
        for conversation in conversations:
            workspace = conversation["workspace"]
            if not isinstance(workspace, str) or workspace not in projects:
                continue
            run_rows = connection.execute("SELECT DISTINCT workspace FROM runs WHERE conversation_id = ?", (conversation["id"],)).fetchall()
            run_workspaces = {str(row["workspace"]) for row in run_rows}
            if not run_workspaces or run_workspaces == {workspace}:
                connection.execute("UPDATE conversations SET project_id = ? WHERE id = ?", (projects[workspace], conversation["id"]))
        connection.execute("INSERT INTO schema_meta(key, value) VALUES(?, ?)", (_PROJECT_MIGRATION_KEY, utc_now()))

    @staticmethod
    def _json(value: Any, *, limit: int | None = None) -> str:
        try:
            encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise StorageError(f"Record is not JSON serializable: {exc}") from exc
        if limit is not None and len(encoded.encode("utf-8")) > limit:
            marker = {"truncated": True, "reason": "event payload exceeded persistence limit"}
            encoded = json.dumps(marker, separators=(",", ":"))
        return encoded

    @staticmethod
    def _decoded(value: str | None) -> Any:
        if value is None:
            return None
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {"unavailable": "Stored JSON could not be decoded"}

    def create_project(self, *, name: str, workspace: str, description: str | None = None,
                       system_prompt: str = "", model: str = _DEFAULT_MODEL,
                       temperature: float = _DEFAULT_MODEL_OPTIONS["temperature"],
                       top_p: float = _DEFAULT_MODEL_OPTIONS["top_p"],
                       top_k: int = _DEFAULT_MODEL_OPTIONS["top_k"],
                       num_ctx: int = _DEFAULT_MODEL_OPTIONS["num_ctx"]) -> dict:
        now = utc_now()
        project = {"project_id": uuid.uuid4().hex, "name": name.strip()[:200] or "Unnamed project",
                   "workspace": workspace, "description": description, "system_prompt": system_prompt,
                   "model": model, "temperature": temperature, "top_p": top_p, "top_k": top_k,
                   "num_ctx": num_ctx, "created_at": now, "updated_at": now}
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO projects(project_id, name, workspace, description, system_prompt, model, temperature, top_p, top_k, num_ctx, created_at, updated_at) "
                    "VALUES(:project_id, :name, :workspace, :description, :system_prompt, :model, :temperature, :top_p, :top_k, :num_ctx, :created_at, :updated_at)",
                    project,
                )
        except sqlite3.Error as exc:
            raise StorageError(f"Could not save project: {exc}") from exc
        return project

    def get_project(self, project_id: str) -> dict | None:
        try:
            with self._connect() as connection:
                row = connection.execute("SELECT * FROM projects WHERE project_id = ?", (project_id,)).fetchone()
        except sqlite3.Error as exc:
            raise StorageError(f"Could not load project: {exc}") from exc
        return dict(row) if row else None

    def list_projects(self) -> list[dict]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT p.*, (SELECT COUNT(*) FROM conversations c WHERE c.project_id = p.project_id) AS conversation_count, "
                    "(SELECT COUNT(*) FROM runs r WHERE r.project_id = p.project_id) AS run_count "
                    "FROM projects p ORDER BY p.updated_at DESC"
                ).fetchall()
        except sqlite3.Error as exc:
            raise StorageError(f"Could not list projects: {exc}") from exc
        return [dict(row) for row in rows]

    def update_project(self, project_id: str, **changes: Any) -> dict | None:
        fields = {key: value for key, value in changes.items() if key in {
            "name", "workspace", "description", "system_prompt", "model", "temperature", "top_p", "top_k", "num_ctx"
        } and value is not None}
        if not fields:
            return self.get_project(project_id)
        fields["updated_at"] = utc_now()
        assignments = ", ".join(f"{key} = :{key}" for key in fields)
        fields["project_id"] = project_id
        try:
            with self._connect() as connection:
                cursor = connection.execute(f"UPDATE projects SET {assignments} WHERE project_id = :project_id", fields)
                if cursor.rowcount != 1:
                    return None
                row = connection.execute("SELECT * FROM projects WHERE project_id = ?", (project_id,)).fetchone()
        except sqlite3.Error as exc:
            raise StorageError(f"Could not update project: {exc}") from exc
        return dict(row)

    def delete_project(self, project_id: str) -> bool:
        """Delete only empty projects; historical records are never cascaded away."""
        try:
            with self._connect() as connection:
                for table in ("conversations", "runs", "durable_memory"):
                    if connection.execute(f"SELECT 1 FROM {table} WHERE project_id = ? LIMIT 1", (project_id,)).fetchone():
                        return False
                return connection.execute("DELETE FROM projects WHERE project_id = ?", (project_id,)).rowcount == 1
        except sqlite3.Error as exc:
            raise StorageError(f"Could not delete project: {exc}") from exc

    def index_files(self, project_id: str) -> list[dict]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT * FROM project_index_files WHERE project_id = ? ORDER BY path", (project_id,)
                ).fetchall()
        except sqlite3.Error as exc:
            raise StorageError(f"Could not load repository index files: {exc}") from exc
        return [dict(row) for row in rows]

    def list_index_files(self, project_id: str, *, query: str | None = None,
                         language: str | None = None, limit: int = 500) -> list[dict]:
        clauses = ["project_id = ?"]
        values: list[Any] = [project_id]
        if query:
            clauses.append("path LIKE ?")
            values.append(f"%{query}%")
        if language:
            clauses.append("language = ?")
            values.append(language)
        values.append(limit)
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    f"SELECT * FROM project_index_files WHERE {' AND '.join(clauses)} ORDER BY path LIMIT ?", values
                ).fetchall()
        except sqlite3.Error as exc:
            raise StorageError(f"Could not query repository index files: {exc}") from exc
        return [dict(row) for row in rows]

    def replace_index_file(self, project_id: str, file_record: dict,
                           symbols: list[dict], imports: list[dict]) -> None:
        values = {"project_id": project_id, **file_record}
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO project_index_files(project_id, path, sha256, language, size_bytes, line_count, modified_at) "
                    "VALUES(:project_id, :path, :sha256, :language, :size_bytes, :line_count, :modified_at) "
                    "ON CONFLICT(project_id, path) DO UPDATE SET sha256=excluded.sha256, language=excluded.language, "
                    "size_bytes=excluded.size_bytes, line_count=excluded.line_count, modified_at=excluded.modified_at",
                    values,
                )
                connection.execute("DELETE FROM project_index_symbols WHERE project_id = ? AND path = ?",
                                   (project_id, file_record["path"]))
                connection.execute("DELETE FROM project_index_imports WHERE project_id = ? AND path = ?",
                                   (project_id, file_record["path"]))
                connection.executemany(
                    "INSERT INTO project_index_symbols(project_id, path, sha256, name, qualname, kind, signature, start_line, end_line) "
                    "VALUES(:project_id, :path, :sha256, :name, :qualname, :kind, :signature, :start_line, :end_line)",
                    [{"project_id": project_id, "path": file_record["path"], "sha256": file_record["sha256"], **item}
                     for item in symbols],
                )
                connection.executemany(
                    "INSERT INTO project_index_imports(project_id, path, sha256, module, imported_name, local_module, line) "
                    "VALUES(:project_id, :path, :sha256, :module, :imported_name, :local_module, :line)",
                    [{"project_id": project_id, "path": file_record["path"], "sha256": file_record["sha256"], **item}
                     for item in imports],
                )
        except sqlite3.Error as exc:
            raise StorageError(f"Could not update repository index file: {exc}") from exc

    def update_index_file_metadata(self, project_id: str, file_record: dict) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    "UPDATE project_index_files SET language=:language, size_bytes=:size_bytes, line_count=:line_count, "
                    "modified_at=:modified_at WHERE project_id=:project_id AND path=:path AND sha256=:sha256",
                    {"project_id": project_id, **file_record},
                )
        except sqlite3.Error as exc:
            raise StorageError(f"Could not update repository index metadata: {exc}") from exc

    def remove_index_paths(self, project_id: str, paths: list[str]) -> None:
        if not paths:
            return
        placeholders = ",".join("?" for _ in paths)
        try:
            with self._connect() as connection:
                for table in ("project_index_symbols", "project_index_imports", "project_index_files"):
                    connection.execute(f"DELETE FROM {table} WHERE project_id = ? AND path IN ({placeholders})",
                                       (project_id, *paths))
        except sqlite3.Error as exc:
            raise StorageError(f"Could not remove repository index paths: {exc}") from exc

    def refresh_index_import_relations(self, project_id: str, modules: set[str]) -> None:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT path, module, imported_name, line FROM project_index_imports WHERE project_id = ?", (project_id,)
                ).fetchall()
                for row in rows:
                    module = row["module"]
                    imported_name = row["imported_name"]
                    candidates = [module]
                    if module.startswith("."):
                        level = len(module) - len(module.lstrip("."))
                        parent = list(Path(row["path"]).with_suffix("").parts[:-1])
                        base = parent[:max(0, len(parent) - level + 1)]
                        suffix = module[level:].split(".") if module[level:] else []
                        if suffix or imported_name:
                            candidates = [".".join([*base, *suffix, imported_name] if not suffix else [*base, *suffix])]
                    elif imported_name:
                        candidates.append(f"{module}.{imported_name}")
                    local_module = next((candidate for candidate in candidates if candidate in modules), None)
                    connection.execute(
                        "UPDATE project_index_imports SET local_module = ? WHERE project_id = ? AND path = ? "
                        "AND module = ? AND imported_name = ? AND line = ?",
                        (local_module, project_id, row["path"], module, imported_name, row["line"]),
                    )
        except sqlite3.Error as exc:
            raise StorageError(f"Could not update local import relations: {exc}") from exc

    def set_index_state(self, project_id: str, *, workspace: str, scanned_at: str,
                        git_branch: str | None, git_head: str | None) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO project_index_state(project_id, workspace, scanned_at, git_branch, git_head) "
                    "VALUES(?, ?, ?, ?, ?) ON CONFLICT(project_id) DO UPDATE SET workspace=excluded.workspace, "
                    "scanned_at=excluded.scanned_at, git_branch=excluded.git_branch, git_head=excluded.git_head",
                    (project_id, workspace, scanned_at, git_branch, git_head),
                )
        except sqlite3.Error as exc:
            raise StorageError(f"Could not save repository index state: {exc}") from exc

    def index_status(self, project_id: str) -> dict:
        try:
            with self._connect() as connection:
                state = connection.execute("SELECT * FROM project_index_state WHERE project_id = ?", (project_id,)).fetchone()
                counts = connection.execute(
                    "SELECT COUNT(*) AS file_count, SUM(language = 'python') AS python_file_count FROM project_index_files WHERE project_id = ?",
                    (project_id,),
                ).fetchone()
                symbol_count = connection.execute("SELECT COUNT(*) FROM project_index_symbols WHERE project_id = ?", (project_id,)).fetchone()[0]
        except sqlite3.Error as exc:
            raise StorageError(f"Could not load repository index status: {exc}") from exc
        return {**(dict(state) if state else {}), "project_id": project_id, "file_count": counts["file_count"],
                "python_file_count": counts["python_file_count"] or 0, "symbol_count": symbol_count}

    def find_index_symbols(self, project_id: str, name: str, *, path: str | None = None,
                           limit: int = 100) -> list[dict]:
        clauses = ["project_id = ?", "(name LIKE ? OR qualname LIKE ?)"]
        values: list[Any] = [project_id, f"%{name}%", f"%{name}%"]
        if path:
            clauses.append("path = ?")
            values.append(path)
        values.append(limit)
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    f"SELECT * FROM project_index_symbols WHERE {' AND '.join(clauses)} "
                    "ORDER BY path, start_line LIMIT ?", values
                ).fetchall()
        except sqlite3.Error as exc:
            raise StorageError(f"Could not query repository symbols: {exc}") from exc
        return [dict(row) for row in rows]

    def index_symbols_for_file(self, project_id: str, path: str) -> list[dict]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT * FROM project_index_symbols WHERE project_id = ? AND path = ? ORDER BY start_line",
                    (project_id, path),
                ).fetchall()
        except sqlite3.Error as exc:
            raise StorageError(f"Could not load file symbols: {exc}") from exc
        return [dict(row) for row in rows]

    def index_imports(self, project_id: str, path: str | None = None) -> list[dict]:
        try:
            with self._connect() as connection:
                if path is None:
                    rows = connection.execute("SELECT * FROM project_index_imports WHERE project_id = ? ORDER BY path, line", (project_id,)).fetchall()
                else:
                    rows = connection.execute("SELECT * FROM project_index_imports WHERE project_id = ? AND path = ? ORDER BY line", (project_id, path)).fetchall()
        except sqlite3.Error as exc:
            raise StorageError(f"Could not load file imports: {exc}") from exc
        return [dict(row) for row in rows]

    def top_python_files_by_symbols(self, project_id: str, limit: int = 5) -> list[dict]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT f.path AS path, COUNT(*) AS symbol_count "
                    "FROM project_index_files AS f "
                    "JOIN project_index_symbols AS s ON s.project_id = f.project_id AND s.path = f.path "
                    "WHERE f.project_id = ? AND f.language = 'python' "
                    "GROUP BY f.path "
                    "ORDER BY symbol_count DESC, f.path ASC LIMIT ?",
                    (project_id, limit),
                ).fetchall()
        except sqlite3.Error as exc:
            raise StorageError(f"Could not query top Python files by symbols: {exc}") from exc
        return [dict(row) for row in rows]

    def create_conversation(
        self, *, workspace: str | None = None, model: str | None = None, title: str | None = None,
        project_id: str | None = None,
    ) -> dict:
        now = utc_now()
        conversation = {
            "id": uuid.uuid4().hex,
            "created_at": now,
            "updated_at": now,
            "title": (title or "New conversation").strip()[:200] or "New conversation",
            "workspace": workspace,
            "model": model,
            "project_id": project_id,
        }
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO conversations(id, created_at, updated_at, title, workspace, model, project_id) "
                    "VALUES(:id, :created_at, :updated_at, :title, :workspace, :model, :project_id)",
                    conversation,
                )
        except sqlite3.Error as exc:
            raise StorageError(f"Could not save conversation: {exc}") from exc
        return conversation

    def get_conversation(self, conversation_id: str) -> dict | None:
        try:
            with self._connect() as connection:
                row = connection.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        except sqlite3.Error as exc:
            raise StorageError(f"Could not load conversation: {exc}") from exc
        return dict(row) if row else None

    def list_conversations(self, limit: int = 100, project_id: str | None = None) -> list[dict]:
        try:
            with self._connect() as connection:
                query = (
                    "SELECT c.*, "
                    "(SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS message_count, "
                    "(SELECT COUNT(*) FROM runs r WHERE r.conversation_id = c.id) AS run_count "
                    "FROM conversations c "
                )
                parameters: tuple[Any, ...] = (limit,)
                if project_id is not None:
                    query += "WHERE c.project_id = ? "
                    parameters = (project_id, limit)
                    query += "ORDER BY c.updated_at DESC LIMIT ?"
                else:
                    query += "ORDER BY c.updated_at DESC LIMIT ?"
                rows = connection.execute(query, parameters).fetchall()
        except sqlite3.Error as exc:
            raise StorageError(f"Could not list conversations: {exc}") from exc
        return [dict(row) for row in rows]

    def delete_conversation(self, conversation_id: str) -> bool:
        """Delete one conversation and its messages while retaining its run records."""
        try:
            with self._connect() as connection:
                cursor = connection.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
                return cursor.rowcount == 1
        except sqlite3.Error as exc:
            raise StorageError(f"Could not delete conversation: {exc}") from exc

    def add_message(
        self, conversation_id: str, *, role: str, content: str, run_id: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        if role not in {"user", "assistant"}:
            raise StorageError(f"Unsupported message role: {role}")
        now = utc_now()
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    "INSERT INTO messages(conversation_id, run_id, role, content, metadata_json, created_at) "
                    "VALUES(?, ?, ?, ?, ?, ?)",
                    (conversation_id, run_id, role, content, self._json(metadata) if metadata else None, now),
                )
                if cursor.rowcount != 1:
                    raise StorageError("Could not save message")
                connection.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id))
        except sqlite3.IntegrityError as exc:
            raise StorageError("Conversation does not exist") from exc
        except sqlite3.Error as exc:
            raise StorageError(f"Could not save message: {exc}") from exc

    def get_conversation_detail(self, conversation_id: str) -> dict | None:
        conversation = self.get_conversation(conversation_id)
        if conversation is None:
            return None
        try:
            with self._connect() as connection:
                messages = connection.execute(
                    "SELECT id, run_id, role, content, metadata_json, created_at FROM messages "
                    "WHERE conversation_id = ? ORDER BY id",
                    (conversation_id,),
                ).fetchall()
                runs = connection.execute(
                    "SELECT * FROM runs WHERE conversation_id = ? ORDER BY started_at",
                    (conversation_id,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise StorageError(f"Could not load conversation detail: {exc}") from exc
        conversation["messages"] = [
            {**dict(row), "metadata": self._decoded(row["metadata_json"])} for row in messages
        ]
        for message in conversation["messages"]:
            message.pop("metadata_json", None)
        conversation["runs"] = [self._run_from_row(row) for row in runs]
        return conversation

    def relevant_context(self, conversation_id: str, limit: int = 4) -> str:
        """Return bounded user intent and observed prior outcomes for one conversation.

        Assistant prose is deliberately not replayed. Only stored result metadata,
        which originates from the structured engine outcome, is eligible.
        """
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT role, content, metadata_json FROM messages WHERE conversation_id = ? "
                    "ORDER BY id DESC LIMIT ?", (conversation_id, limit)
                ).fetchall()
        except sqlite3.Error as exc:
            raise StorageError(f"Could not load conversation context: {exc}") from exc
        lines: list[str] = []
        for row in reversed(rows):
            if row["role"] == "user":
                lines.append("Earlier user request: " + row["content"][:1_500])
                continue
            metadata = self._decoded(row["metadata_json"])
            if not isinstance(metadata, dict) or not isinstance(metadata.get("result"), dict):
                continue
            result = metadata["result"]
            status = metadata.get("status", result.get("status", "unknown"))
            changed = result.get("changed_files") if isinstance(result.get("changed_files"), list) else []
            checks = result.get("checks") if isinstance(result.get("checks"), list) else []
            lines.append(
                "Verified prior run: status=" + str(status)
                + "; changed_files=" + ", ".join(str(path) for path in changed[:20])
                + f"; recorded_checks={len(checks)}"
            )
        return "\n".join(lines)[:6_000]

    def add_memory(self, *, content: str, kind: str, workspace: str | None = None, project_id: str | None = None,
                   source_run_id: str | None = None, source_conversation_id: str | None = None,
                   verification: str) -> dict:
        if kind not in {"user_decision", "verified_result"}:
            raise StorageError(f"Unsupported durable-memory kind: {kind}")
        if verification not in {"user_confirmed", "observed"}:
            raise StorageError(f"Unsupported durable-memory verification: {verification}")
        item = {"workspace": workspace, "project_id": project_id, "kind": kind, "content": content.strip()[:2_000],
                "source_run_id": source_run_id, "source_conversation_id": source_conversation_id,
                "verification": verification, "created_at": utc_now()}
        if not item["content"]:
            raise StorageError("Durable-memory content is empty")
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    "INSERT INTO durable_memory(workspace, project_id, kind, content, source_run_id, source_conversation_id, "
                    "verification, created_at) VALUES(:workspace, :project_id, :kind, :content, :source_run_id, "
                    ":source_conversation_id, :verification, :created_at)", item
                )
                item["id"] = cursor.lastrowid
        except sqlite3.IntegrityError as exc:
            raise StorageError(f"Could not save durable memory: {exc}") from exc
        except sqlite3.Error as exc:
            raise StorageError(f"Could not save durable memory: {exc}") from exc
        return item

    def remember_verified_result(self, run: dict) -> dict | None:
        """Promote only an observed completed result, never model-generated prose."""
        result = run.get("result")
        if run.get("status") != "completed" or not isinstance(result, dict):
            return None
        changed = result.get("changed_files") if isinstance(result.get("changed_files"), list) else []
        checks = result.get("checks") if isinstance(result.get("checks"), list) else []
        level = result.get("verification_level", "unknown")
        summary = (f"Observed completed run {run['run_id']}: verification={level}; "
                   f"changed_files={', '.join(str(path) for path in changed[:20]) or 'none'}; "
                   f"recorded_checks={len(checks)}.")
        try:
            return self.add_memory(workspace=run.get("workspace"), project_id=run.get("project_id"), kind="verified_result", content=summary,
                                   source_run_id=run["run_id"],
                                   source_conversation_id=run.get("conversation_id"), verification="observed")
        except StorageError as exc:
            if "UNIQUE constraint failed" in str(exc):
                return None
            raise

    def list_memory(self, workspace: str | None = None, limit: int = 20, project_id: str | None = None) -> list[dict]:
        try:
            with self._connect() as connection:
                if project_id is not None:
                    rows = connection.execute(
                        "SELECT * FROM durable_memory WHERE project_id = ? ORDER BY created_at DESC LIMIT ?", (project_id, limit)
                    ).fetchall()
                elif workspace is None:
                    rows = connection.execute(
                        "SELECT * FROM durable_memory ORDER BY created_at DESC LIMIT ?", (limit,)
                    ).fetchall()
                else:
                    rows = connection.execute(
                        "SELECT * FROM durable_memory WHERE workspace = ? OR workspace IS NULL "
                        "ORDER BY created_at DESC LIMIT ?", (workspace, limit)
                    ).fetchall()
        except sqlite3.Error as exc:
            raise StorageError(f"Could not load durable memory: {exc}") from exc
        return [dict(row) for row in rows]

    def relevant_memory(self, workspace: str, limit: int = 6, project_id: str | None = None) -> str:
        lines = []
        for item in reversed(self.list_memory(workspace, limit, project_id)):
            label = "User-confirmed decision" if item["kind"] == "user_decision" else "Observed prior result"
            lines.append(f"{label}: {item['content']}")
        return "\n".join(lines)[:6_000]

    def create_run(self, entry: dict) -> None:
        values = {
            "run_id": entry["run_id"], "conversation_id": entry.get("conversation_id"),
            "project_id": entry.get("project_id"), "workspace": entry["workspace"], "model": entry["model"], "task": entry["task"],
            "model_options_json": self._json(entry.get("model_options")) if entry.get("model_options") is not None else None,
            "project_system_prompt": entry.get("project_system_prompt"),
            "timeout_seconds": entry.get("timeout", 660), "status": entry["status"],
            "started_at": entry["started_at"], "finished_at": entry.get("finished_at"),
            "exit_code": entry.get("exit_code"), "result_json": self._json(entry.get("result")) if entry.get("result") is not None else None,
        }
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO runs(run_id, conversation_id, project_id, workspace, model, model_options_json, project_system_prompt, task, timeout_seconds, status, "
                    "started_at, finished_at, exit_code, result_json) VALUES(:run_id, :conversation_id, :project_id, :workspace, "
                    ":model, :model_options_json, :project_system_prompt, :task, :timeout_seconds, :status, :started_at, :finished_at, :exit_code, :result_json)",
                    values,
                )
        except sqlite3.Error as exc:
            raise StorageError(f"Could not save run: {exc}") from exc

    def update_run_status(self, run_id: str, status: str) -> None:
        try:
            with self._connect() as connection:
                connection.execute("UPDATE runs SET status = ? WHERE run_id = ?", (status, run_id))
        except sqlite3.Error as exc:
            raise StorageError(f"Could not update run status: {exc}") from exc

    def complete_run(self, run_id: str, *, status: str, finished_at: str, exit_code: int | None, result: Any) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    "UPDATE runs SET status = ?, finished_at = ?, exit_code = ?, result_json = ? WHERE run_id = ?",
                    (status, finished_at, exit_code, self._json(result) if result is not None else None, run_id),
                )
        except sqlite3.Error as exc:
            raise StorageError(f"Could not save completed run: {exc}") from exc

    def record_event(self, run_id: str, sequence: int, event: dict) -> None:
        kind = event.get("type") if isinstance(event.get("type"), str) else "unknown"
        stage = event.get("phase") if isinstance(event.get("phase"), str) else kind
        status = event.get("status") if isinstance(event.get("status"), str) else None
        now = utc_now()
        encoded = self._json(event, limit=_MAX_EVENT_BYTES)
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT OR REPLACE INTO execution_events(run_id, sequence, event_type, recorded_at, payload_json) "
                    "VALUES(?, ?, ?, ?, ?)", (run_id, sequence, kind, now, encoded),
                )
                connection.execute(
                    "INSERT OR REPLACE INTO stage_results(run_id, sequence, stage, status, recorded_at, result_json) "
                    "VALUES(?, ?, ?, ?, ?, ?)", (run_id, sequence, stage, status, now, encoded),
                )
        except sqlite3.Error as exc:
            raise StorageError(f"Could not save run event: {exc}") from exc

    def save_checkpoint(self, run_id: str, *, stage: str, next_stage: str, state: dict,
                        stage_result: dict) -> dict:
        """Atomically replace the one recoverable boundary for a run."""
        state_json = self._json(state)
        result_json = self._json(stage_result)
        digest = hashlib.sha256(state_json.encode("utf-8")).hexdigest()
        checkpoint = {"run_id": run_id, "format_version": _CHECKPOINT_VERSION, "stage": stage,
                      "next_stage": next_stage, "saved_at": utc_now(), "state_json": state_json,
                      "stage_result_json": result_json, "state_digest": digest}
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO checkpoints(run_id, format_version, stage, next_stage, saved_at, state_json, "
                    "stage_result_json, state_digest, valid, invalid_reason) VALUES(:run_id, :format_version, "
                    ":stage, :next_stage, :saved_at, :state_json, :stage_result_json, :state_digest, 1, NULL) "
                    "ON CONFLICT(run_id) DO UPDATE SET format_version=excluded.format_version, "
                    "stage=excluded.stage, next_stage=excluded.next_stage, saved_at=excluded.saved_at, "
                    "state_json=excluded.state_json, stage_result_json=excluded.stage_result_json, "
                    "state_digest=excluded.state_digest, valid=1, invalid_reason=NULL",
                    checkpoint,
                )
        except sqlite3.Error as exc:
            raise StorageError(f"Could not save checkpoint: {exc}") from exc
        return {key: checkpoint[key] for key in ("run_id", "format_version", "stage", "next_stage", "saved_at")}

    def get_checkpoint(self, run_id: str) -> dict | None:
        try:
            with self._connect() as connection:
                row = connection.execute("SELECT * FROM checkpoints WHERE run_id = ?", (run_id,)).fetchone()
        except sqlite3.Error as exc:
            raise StorageError(f"Could not load checkpoint: {exc}") from exc
        if row is None:
            return None
        checkpoint = dict(row)
        if not checkpoint["valid"]:
            checkpoint["state"] = None
            checkpoint["stage_result"] = None
            return checkpoint
        if checkpoint["format_version"] != _CHECKPOINT_VERSION:
            self.invalidate_checkpoint(run_id, "Unsupported checkpoint format")
            checkpoint["valid"] = 0
            checkpoint["invalid_reason"] = "Unsupported checkpoint format"
            checkpoint["state"] = None
            checkpoint["stage_result"] = None
            return checkpoint
        digest = hashlib.sha256(checkpoint["state_json"].encode("utf-8")).hexdigest()
        if digest != checkpoint["state_digest"]:
            self.invalidate_checkpoint(run_id, "Checkpoint integrity check failed")
            checkpoint["valid"] = 0
            checkpoint["invalid_reason"] = "Checkpoint integrity check failed"
            checkpoint["state"] = None
            checkpoint["stage_result"] = None
            return checkpoint
        try:
            checkpoint["state"] = json.loads(checkpoint["state_json"])
            checkpoint["stage_result"] = json.loads(checkpoint["stage_result_json"])
        except json.JSONDecodeError:
            self.invalidate_checkpoint(run_id, "Checkpoint JSON is invalid")
            checkpoint["valid"] = 0
            checkpoint["invalid_reason"] = "Checkpoint JSON is invalid"
            checkpoint["state"] = None
            checkpoint["stage_result"] = None
        return checkpoint

    def invalidate_checkpoint(self, run_id: str, reason: str) -> None:
        try:
            with self._connect() as connection:
                connection.execute("UPDATE checkpoints SET valid = 0, invalid_reason = ? WHERE run_id = ?",
                                   (reason[:500], run_id))
        except sqlite3.Error as exc:
            raise StorageError(f"Could not invalidate checkpoint: {exc}") from exc

    def interrupt_active_runs(self) -> list[str]:
        """Mark stale in-progress rows on API startup; never start them again."""
        now = utc_now()
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT run_id FROM runs WHERE status IN ('running', 'cancelling')"
                ).fetchall()
                ids = [row["run_id"] for row in rows]
                connection.execute(
                    "UPDATE runs SET status = 'interrupted', finished_at = ?, "
                    "result_json = COALESCE(result_json, ?) WHERE status IN ('running', 'cancelling')",
                    (now, self._json({"status": "interrupted", "reason": "API restarted before terminal result"})),
                )
        except sqlite3.Error as exc:
            raise StorageError(f"Could not mark interrupted runs: {exc}") from exc
        return ids

    def resume_run(self, run_id: str) -> None:
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    "UPDATE runs SET status = 'running', finished_at = NULL, exit_code = NULL, result_json = NULL "
                    "WHERE run_id = ?", (run_id,)
                )
                if cursor.rowcount != 1:
                    raise StorageError("Run does not exist")
        except sqlite3.Error as exc:
            raise StorageError(f"Could not mark run for recovery: {exc}") from exc

    @classmethod
    def _run_from_row(cls, row: sqlite3.Row) -> dict:
        item = dict(row)
        item["timeout"] = item.pop("timeout_seconds")
        item["result"] = cls._decoded(item.pop("result_json"))
        item["model_options"] = cls._decoded(item.pop("model_options_json", None))
        return item

    def get_run(self, run_id: str) -> dict | None:
        try:
            with self._connect() as connection:
                row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        except sqlite3.Error as exc:
            raise StorageError(f"Could not load run: {exc}") from exc
        return self._run_from_row(row) if row else None

    def list_events(self, run_id: str) -> list[dict]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT sequence, event_type, payload_json FROM execution_events WHERE run_id = ? ORDER BY sequence",
                    (run_id,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise StorageError(f"Could not load run events: {exc}") from exc
        return [{"sequence": row["sequence"], "event_type": row["event_type"], "payload": self._decoded(row["payload_json"])} for row in rows]

    def list_stage_results(self, run_id: str) -> list[dict]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT sequence, stage, status, recorded_at, result_json FROM stage_results "
                    "WHERE run_id = ? ORDER BY sequence", (run_id,)
                ).fetchall()
        except sqlite3.Error as exc:
            raise StorageError(f"Could not load stage results: {exc}") from exc
        return [{**dict(row), "result": self._decoded(row["result_json"])} for row in rows]

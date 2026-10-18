"""Small SQLite store for local conversation and completed-run history.

The store deliberately contains only API-facing records.  It is not a
LangGraph checkpoint store and it never attempts to resume a run.
"""

from __future__ import annotations

import json
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
            """
        )

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

    def create_conversation(
        self, *, workspace: str | None = None, model: str | None = None, title: str | None = None
    ) -> dict:
        now = utc_now()
        conversation = {
            "id": uuid.uuid4().hex,
            "created_at": now,
            "updated_at": now,
            "title": (title or "New conversation").strip()[:200] or "New conversation",
            "workspace": workspace,
            "model": model,
        }
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO conversations(id, created_at, updated_at, title, workspace, model) "
                    "VALUES(:id, :created_at, :updated_at, :title, :workspace, :model)",
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

    def list_conversations(self, limit: int = 100) -> list[dict]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT c.*, "
                    "(SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS message_count, "
                    "(SELECT COUNT(*) FROM runs r WHERE r.conversation_id = c.id) AS run_count "
                    "FROM conversations c ORDER BY c.updated_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise StorageError(f"Could not list conversations: {exc}") from exc
        return [dict(row) for row in rows]

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

    def create_run(self, entry: dict) -> None:
        values = {
            "run_id": entry["run_id"], "conversation_id": entry.get("conversation_id"),
            "workspace": entry["workspace"], "model": entry["model"], "task": entry["task"],
            "timeout_seconds": entry.get("timeout", 660), "status": entry["status"],
            "started_at": entry["started_at"], "finished_at": entry.get("finished_at"),
            "exit_code": entry.get("exit_code"), "result_json": self._json(entry.get("result")) if entry.get("result") is not None else None,
        }
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO runs(run_id, conversation_id, workspace, model, task, timeout_seconds, status, "
                    "started_at, finished_at, exit_code, result_json) VALUES(:run_id, :conversation_id, :workspace, "
                    ":model, :task, :timeout_seconds, :status, :started_at, :finished_at, :exit_code, :result_json)",
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

    @classmethod
    def _run_from_row(cls, row: sqlite3.Row) -> dict:
        item = dict(row)
        item["timeout"] = item.pop("timeout_seconds")
        item["result"] = cls._decoded(item.pop("result_json"))
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

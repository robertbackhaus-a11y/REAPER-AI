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

    def add_memory(self, *, content: str, kind: str, workspace: str | None = None,
                   source_run_id: str | None = None, source_conversation_id: str | None = None,
                   verification: str) -> dict:
        if kind not in {"user_decision", "verified_result"}:
            raise StorageError(f"Unsupported durable-memory kind: {kind}")
        if verification not in {"user_confirmed", "observed"}:
            raise StorageError(f"Unsupported durable-memory verification: {verification}")
        item = {"workspace": workspace, "kind": kind, "content": content.strip()[:2_000],
                "source_run_id": source_run_id, "source_conversation_id": source_conversation_id,
                "verification": verification, "created_at": utc_now()}
        if not item["content"]:
            raise StorageError("Durable-memory content is empty")
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    "INSERT INTO durable_memory(workspace, kind, content, source_run_id, source_conversation_id, "
                    "verification, created_at) VALUES(:workspace, :kind, :content, :source_run_id, "
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
            return self.add_memory(workspace=run.get("workspace"), kind="verified_result", content=summary,
                                   source_run_id=run["run_id"],
                                   source_conversation_id=run.get("conversation_id"), verification="observed")
        except StorageError as exc:
            if "UNIQUE constraint failed" in str(exc):
                return None
            raise

    def list_memory(self, workspace: str | None = None, limit: int = 20) -> list[dict]:
        try:
            with self._connect() as connection:
                if workspace is None:
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

    def relevant_memory(self, workspace: str, limit: int = 6) -> str:
        lines = []
        for item in reversed(self.list_memory(workspace, limit)):
            label = "User-confirmed decision" if item["kind"] == "user_decision" else "Observed prior result"
            lines.append(f"{label}: {item['content']}")
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

# Copyright (c) 2026 Robert Backhaus

import unittest
import uuid
import json
import signal
from pathlib import Path
from unittest.mock import patch

from reaper_ai import api
from reaper_ai.persistence import Store


class ApiPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.path = Path.cwd() / f".reaper-ai-test-{uuid.uuid4().hex}.sqlite3"
        self.log_path = Path.cwd() / f".reaper-ai-test-{uuid.uuid4().hex}.log"
        self.previous_store = api._store
        self.previous_runs = api._runs
        self.previous_active = api._active
        self.previous_procs = api._procs
        api._store = Store(self.path)
        api._runs = {}
        api._active = None
        api._procs = {}

    def tearDown(self):
        api._store = self.previous_store
        api._runs = self.previous_runs
        api._active = self.previous_active
        api._procs = self.previous_procs
        for suffix in ("", "-shm", "-wal"):
            (Path(str(self.path) + suffix)).unlink(missing_ok=True)
        self.log_path.unlink(missing_ok=True)

    def test_watcher_persists_terminal_result_event_and_summary_message(self):
        conversation = api._store.create_conversation(title="Watched chat")
        entry = {"run_id": "watched-run", "conversation_id": conversation["id"],
                 "workspace": "C:/project", "model": "model", "task": "inspect", "timeout": 20,
                 "status": "running", "started_at": "then", "finished_at": None, "exit_code": None,
                 "result": None, "log_path": str(self.log_path)}
        api._store.create_run(entry)
        result = {"status": "completed", "changed_files": ["answer.py"], "checks": [{"ok": True}]}
        self.log_path.write_text(
            "REAPER_EVENT: " + json.dumps({"type": "phase_changed", "phase": "verify", "status": "done"})
            + "\nRUN_RESULT: " + json.dumps(result) + "\n", encoding="utf-8"
        )
        api._runs["watched-run"] = entry

        class FinishedProcess:
            def wait(self):
                return 0

        api._watch("watched-run", FinishedProcess())
        stored = api._store.get_run("watched-run")
        self.assertEqual(stored["result"], result)
        self.assertEqual(api._store.list_stage_results("watched-run")[0]["stage"], "verify")
        messages = api._store.get_conversation_detail(conversation["id"])["messages"]
        self.assertEqual(messages[0]["role"], "assistant")
        self.assertIn("completed", messages[0]["content"])

    def test_resume_endpoint_starts_only_after_explicit_request(self):
        api._store.create_run({"run_id": "resume-api", "workspace": "C:/project", "model": "model",
                               "task": "resume", "timeout": 20, "status": "interrupted", "started_at": "then"})
        api._store.save_checkpoint("resume-api", stage="agent_observe", next_stage="agent_decide",
                                   state={"workspace": "C:/project", "agent": {"observations": []}},
                                   stage_result={"status": "agent_observed"})
        captured = {}

        class Process:
            pid = 1234

        def fake_popen(*args, **kwargs):
            captured["env"] = kwargs["env"]
            return Process()

        with patch.object(api, "_is_windows", return_value=True), patch.object(api, "_validate_workspace", return_value=Path("C:/project")), \
             patch.object(api, "_installed_models", return_value=["model"]), patch.object(api.subprocess, "Popen", side_effect=fake_popen), \
             patch.object(api.threading, "Thread"):
            response = api.resume_run("resume-api")
        self.assertEqual(response["status"], "running")
        self.assertEqual(captured["env"]["REAPER_RESUME"], "1")
        self.assertEqual(api._store.get_run("resume-api")["status"], "running")

    def test_project_run_uses_its_stored_model_snapshot(self):
        project = api._store.create_project(
            name="Configured", workspace="C:/project", system_prompt="Use this project's conventions.",
            model="configured-model", temperature=0.3, top_p=0.7, top_k=12, num_ctx=24576,
        )
        captured = {}

        class Process:
            pid = 5678

        def fake_popen(*args, **kwargs):
            captured["env"] = kwargs["env"]
            return Process()

        with patch.object(api, "_is_windows", return_value=True), patch.object(api, "_validate_workspace", return_value=Path("C:/project")), \
             patch.object(api, "_installed_models", return_value=["configured-model"]), \
             patch.object(api.subprocess, "Popen", side_effect=fake_popen), patch.object(api.threading, "Thread"):
            response = api.start_run(api.StartRequest(project_id=project["project_id"], task="Inspect"))
        self.assertEqual(response["project_id"], project["project_id"])
        self.assertEqual(response["model"], "configured-model")
        self.assertEqual(json.loads(captured["env"]["REAPER_MODEL_OPTIONS"]),
                         {"temperature": 0.3, "top_p": 0.7, "top_k": 12, "num_ctx": 24576})
        self.assertEqual(captured["env"]["REAPER_PROJECT_SYSTEM_PROMPT"], "Use this project's conventions.")
        self.assertEqual(captured["env"]["REAPER_PROJECT_ID"], project["project_id"])

    def test_posix_start_run_uses_the_existing_supervisor_contract(self):
        captured = {}

        class Process:
            pid = 6789

        def fake_popen(*args, **kwargs):
            captured["command"] = args[0]
            return Process()

        with patch.object(api, "_is_windows", return_value=False), patch.object(api, "_validate_workspace", return_value=Path("C:/project")), \
             patch.object(api, "_installed_models", return_value=["model"]), \
             patch.object(api.subprocess, "Popen", side_effect=fake_popen), patch.object(api.threading, "Thread"):
            response = api.start_run(api.StartRequest(workspace="/project", task="Inspect", model="model"))
        self.assertEqual(response["status"], "running")
        self.assertIn("reaper_ai.supervisor", captured["command"])

    def test_posix_cancel_signals_the_supervisor(self):
        class Process:
            pid = 6789

            def poll(self):
                return None

        api._runs["posix-cancel"] = {"run_id": "posix-cancel", "status": "running"}
        api._procs["posix-cancel"] = Process()
        with patch.object(api, "_is_windows", return_value=False), patch.object(api.os, "kill") as kill:
            response = api.cancel_run("posix-cancel")
        self.assertEqual(response["status"], "cancelling")
        kill.assert_called_once_with(6789, signal.SIGTERM)

    # Terminal SSE replay: live stream and persisted replay must agree, keep every
    # recovery segment, and end with the authoritative run status.
    @staticmethod
    def _log(*events, result=None):
        lines = ["REAPER_EVENT: " + json.dumps({"type": kind, **payload}) for kind, payload in events]
        if result is not None:
            lines.append("RUN_RESULT: " + json.dumps(result))
        return "\n".join(lines) + "\n"

    def _finish(self, run_id, log_path, text, code, *, status="running", watch=True):
        Path(log_path).write_text(text, encoding="utf-8")
        entry = api._runs.get(run_id) or {
            "run_id": run_id, "workspace": "C:/project", "model": "model", "task": "inspect", "timeout": 20,
            "started_at": "then"}
        if run_id not in api._runs:
            api._store.create_run({**entry, "status": "running"})
        entry.update({"status": status, "finished_at": None, "exit_code": None, "result": None,
                      "log_path": str(log_path)})
        api._runs[run_id] = entry

        class FinishedProcess:
            def wait(self):
                return code

        if watch:
            api._watch(run_id, FinishedProcess())
        return lambda: api._watch(run_id, FinishedProcess())

    @staticmethod
    def _stream(run_id):
        class ConnectedRequest:
            async def is_disconnected(self):
                return False

        # Finished-run replay does not await I/O. Drive its coroutine and
        # async generator directly so these persistence tests do not need a
        # Windows event-loop socket.
        request = api.stream_events(run_id, ConnectedRequest())
        try:
            request.send(None)
        except StopIteration as completed:
            response = completed.value
        else:
            raise AssertionError("Finished-run replay unexpectedly awaited I/O")

        chunks = []
        iterator = response.body_iterator
        while True:
            try:
                next_chunk = iterator.__anext__()
                next_chunk.send(None)
            except StopIteration as yielded:
                chunks.append(yielded.value)
            except StopAsyncIteration:
                break
        body = "".join(chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk for chunk in chunks)
        events = []
        for block in body.split("\n\n"):
            fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line and not line.startswith(":"))
            if "event" in fields:
                events.append((int(fields["id"]), fields["event"], json.loads(fields["data"]).get("status")))
        return events

    def test_recovered_run_replay_keeps_every_segment_in_order(self):
        second_log = Path(str(self.log_path) + "-recovery")
        self.addCleanup(second_log.unlink, missing_ok=True)
        self._finish("recovered-run", self.log_path, self._log(
            ("run_started", {}), ("phase_changed", {"phase": "agent_decide"}), ("tool_started", {"tool": "shell"}),
            ("tool_completed", {"tool": "shell", "ok": True}), ("checkpoint_saved", {"stage": "agent_observe"})),
            130, status="cancelling")
        self.assertEqual(api._store.get_run("recovered-run")["status"], "cancelled")
        self._finish("recovered-run", second_log, self._log(
            ("recovery_started", {"next_stage": "agent_decide"}), ("run_completed", {"status": "completed"}),
            result={"status": "completed"}), 0)
        live = self._stream("recovered-run")
        api._runs = {}
        replayed = self._stream("recovered-run")
        self.assertEqual(replayed, live)
        self.assertEqual([kind for _, kind, _ in replayed],
                         ["run_started", "phase_changed", "tool_started", "tool_completed", "checkpoint_saved",
                          "run_completed", "recovery_started", "run_completed"])
        self.assertEqual([sequence for sequence, _, _ in replayed], list(range(1, 9)))
        self.assertEqual(replayed[5][2], "cancelled")
        self.assertEqual(replayed[-1][2], "completed")

    def test_live_stream_of_a_recovered_run_matches_its_later_replay(self):
        second_log = Path(str(self.log_path) + "-recovery")
        self.addCleanup(second_log.unlink, missing_ok=True)
        self._finish("live-recovery", self.log_path, self._log(("run_started", {}), ("tool_started", {"tool": "shell"})),
                     130, status="cancelling")
        finish = self._finish("live-recovery", second_log, self._log(
            ("recovery_started", {"next_stage": "agent_execute"}), ("run_completed", {"status": "completed"}),
            result={"status": "completed"}), 0, watch=False)

        async def finish_while_waiting(_seconds):
            # The supervisor exits while the client is connected to the running stream.
            if api._runs["live-recovery"]["finished_at"] is None:
                finish()

        with patch.object(api.asyncio, "sleep", side_effect=finish_while_waiting):
            live = self._stream("live-recovery")
        api._runs = {}
        replayed = self._stream("live-recovery")
        self.assertEqual(live, replayed)
        self.assertEqual([kind for _, kind, _ in live],
                         ["run_started", "tool_started", "run_completed", "recovery_started", "run_completed"])
        self.assertEqual(live[-1], (5, "run_completed", "completed"))

    def test_terminal_replay_reports_the_authoritative_status(self):
        # The engine finished, but the user had already cancelled: the API status wins.
        self._finish("cancel-race", self.log_path, self._log(
            ("run_started", {}), ("run_completed", {"status": "completed"}), result={"status": "completed"}),
            0, status="cancelling")
        live = self._stream("cancel-race")
        api._runs = {}
        replayed = self._stream("cancel-race")
        self.assertEqual(replayed, live)
        self.assertEqual(api._store.get_run("cancel-race")["status"], "cancelled")
        self.assertEqual(replayed[-1][1:], ("run_completed", "cancelled"))

    def test_timed_out_replay_after_restart_still_ends_with_a_terminal_event(self):
        self._finish("timed-out", self.log_path, self._log(("run_started", {}), ("tool_started", {"tool": "shell"})), 2)
        live = self._stream("timed-out")
        api._runs = {}
        replayed = self._stream("timed-out")
        self.assertEqual(replayed, live)
        self.assertEqual(replayed[-1], (3, "run_completed", "timed_out"))

    def test_completed_run_replay_keeps_the_single_engine_terminal_event(self):
        self._finish("clean-run", self.log_path, self._log(
            ("run_started", {}), ("run_completed", {"status": "completed"}), result={"status": "completed"}), 0)
        api._runs = {}
        self.assertEqual(self._stream("clean-run"), [(1, "run_started", None), (2, "run_completed", "completed")])

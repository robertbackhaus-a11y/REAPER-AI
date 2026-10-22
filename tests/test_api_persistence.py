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

    def test_persisted_run_is_available_when_memory_registry_is_empty(self):
        conversation = api._store.create_conversation(title="Stored chat")
        api._store.create_run({"run_id": "saved-run", "conversation_id": conversation["id"],
                               "workspace": "C:/project", "model": "model", "task": "inspect",
                               "timeout": 20, "status": "completed", "started_at": "then",
                               "finished_at": "later", "exit_code": 0,
                               "result": {"status": "completed"}})
        response = api.get_run("saved-run")
        self.assertEqual(response["run_id"], "saved-run")
        self.assertEqual(response["result"]["status"], "completed")

    def test_conversation_endpoint_functions_return_saved_history(self):
        conversation = api.create_conversation(api.ConversationRequest(title="Saved chat"))
        api._store.add_message(conversation["id"], role="user", content="hello")
        detail = api.get_conversation(conversation["id"])
        self.assertEqual(detail["title"], "Saved chat")
        self.assertEqual(detail["messages"][0]["content"], "hello")
        memory = api.add_memory(api.MemoryRequest(content="Keep this decision"))
        self.assertEqual(memory["verification"], "user_confirmed")
        self.assertEqual(api.list_memory()["memory"][0]["content"], "Keep this decision")

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

    def test_recovery_endpoint_exposes_only_explicitly_resumable_checkpoint(self):
        api._store.create_run({"run_id": "interrupted-run", "workspace": "C:/project", "model": "model",
                               "task": "resume", "timeout": 20, "status": "interrupted", "started_at": "then"})
        api._store.save_checkpoint("interrupted-run", stage="agent_observe", next_stage="agent_decide",
                                   state={"workspace": "C:/project", "agent": {"observations": []}},
                                   stage_result={"status": "agent_observed"})
        recovery = api.get_recovery("interrupted-run")
        self.assertTrue(recovery["can_resume"])
        self.assertEqual(recovery["checkpoint"]["next_stage"], "agent_decide")

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

        with patch.object(api.os, "name", "nt"), patch.object(api, "_validate_workspace", return_value=Path("C:/project")), \
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

        with patch.object(api.os, "name", "nt"), patch.object(api, "_validate_workspace", return_value=Path("C:/project")), \
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

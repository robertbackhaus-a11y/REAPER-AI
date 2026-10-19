# Copyright (c) 2026 Robert Backhaus

import unittest
import uuid
import sqlite3
import shutil
from pathlib import Path

from reaper_ai.persistence import Store
from reaper_ai.repository_index import refresh_project_index


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.path = Path.cwd() / f".reaper-ai-test-{uuid.uuid4().hex}.sqlite3"

    def tearDown(self):
        for suffix in ("", "-shm", "-wal"):
            (Path(str(self.path) + suffix)).unlink(missing_ok=True)

    def test_conversation_run_stage_results_survive_new_store_instance(self):
        with self.subTest("restart"):
            store = Store(self.path)
            conversation = store.create_conversation(
                workspace="C:/projects/sample", model="local-model", title="Add a check"
            )
            store.add_message(conversation["id"], role="user", content="Add a check", run_id="run-1")
            store.create_run({
                "run_id": "run-1", "conversation_id": conversation["id"],
                "workspace": "C:/projects/sample", "model": "local-model", "task": "Add a check",
                "timeout": 60, "status": "running", "started_at": "2026-10-18T10:00:00+00:00",
            })
            store.record_event("run-1", 1, {"type": "phase_changed", "phase": "verify", "status": "started"})
            result = {"status": "completed", "changed_files": ["check.py"], "checks": [{"ok": True}]}
            store.complete_run("run-1", status="completed", finished_at="2026-10-18T10:01:00+00:00",
                               exit_code=0, result=result)
            store.add_message(conversation["id"], role="assistant", content="Run complete", run_id="run-1",
                              metadata={"status": "completed"})

            restarted = Store(self.path)
            loaded = restarted.get_conversation_detail(conversation["id"])
            self.assertEqual([message["role"] for message in loaded["messages"]], ["user", "assistant"])
            self.assertEqual(loaded["runs"][0]["result"], result)
            self.assertEqual(restarted.get_run("run-1")["status"], "completed")
            self.assertEqual(restarted.list_events("run-1")[0]["payload"]["phase"], "verify")
            stage = restarted.list_stage_results("run-1")[0]
            self.assertEqual((stage["stage"], stage["status"]), ("verify", "started"))

    def test_relevant_context_uses_user_intent_and_result_metadata_not_assistant_prose(self):
        store = Store(self.path)
        conversation = store.create_conversation(title="Context")
        store.add_message(conversation["id"], role="user", content="Keep the public API stable")
        store.add_message(conversation["id"], role="assistant", content="Unverified model prose must not appear",
                          metadata={"status": "completed", "result": {"changed_files": ["api.py"], "checks": []}})
        context = store.relevant_context(conversation["id"])
        self.assertIn("Keep the public API stable", context)
        self.assertIn("changed_files=api.py", context)
        self.assertNotIn("Unverified model prose", context)

    def test_durable_memory_survives_restart_and_keeps_scopes_and_sources_separate(self):
        store = Store(self.path)
        conversation = store.create_conversation(workspace="C:/project", title="Memory")
        store.add_message(conversation["id"], role="assistant", content="Model prose is not durable knowledge")
        store.add_memory(content="Use the existing public API contract.", kind="user_decision", workspace="C:/project",
                         source_conversation_id=conversation["id"], verification="user_confirmed")
        run = {"run_id": "memory-run", "conversation_id": conversation["id"], "workspace": "C:/project",
               "model": "model", "task": "change", "timeout": 30, "status": "completed", "started_at": "now",
               "result": {"verification_level": "structural", "changed_files": ["api.py"], "checks": [{"ok": True}]}}
        store.create_run(run)
        store.remember_verified_result(run)

        restarted = Store(self.path)
        remembered = restarted.relevant_memory("C:/project")
        self.assertIn("existing public API contract", remembered)
        self.assertIn("changed_files=api.py", remembered)
        self.assertNotIn("Model prose", remembered)
        self.assertEqual(restarted.relevant_memory("C:/other"), "")

    def test_checkpoint_survives_restart_and_marks_stale_run_interrupted(self):
        store = Store(self.path)
        store.create_run({"run_id": "recover-me", "workspace": "C:/project", "model": "model",
                          "task": "change", "timeout": 30, "status": "running", "started_at": "now"})
        state = {"workspace": "C:/project", "task": "change", "inventory": [], "selected": [],
                 "creating": [], "originals": {}, "changes": {}, "status": "inventoried", "error": "",
                 "details": [], "corrections": 0, "feedback": ""}
        store.save_checkpoint("recover-me", stage="inventory", next_stage="select", state=state,
                              stage_result={"status": "inventoried"})
        restarted = Store(self.path)
        self.assertEqual(restarted.interrupt_active_runs(), ["recover-me"])
        checkpoint = restarted.get_checkpoint("recover-me")
        self.assertEqual((checkpoint["stage"], checkpoint["next_stage"]), ("inventory", "select"))
        self.assertEqual(restarted.get_run("recover-me")["status"], "interrupted")
        restarted.resume_run("recover-me")
        self.assertEqual(restarted.get_run("recover-me")["status"], "running")

    def test_corrupt_checkpoint_is_invalidated_instead_of_returned(self):
        store = Store(self.path)
        store.create_run({"run_id": "corrupt", "workspace": "C:/project", "model": "model", "task": "x",
                          "timeout": 30, "status": "interrupted", "started_at": "now"})
        store.save_checkpoint("corrupt", stage="inventory", next_stage="select", state={"workspace": "C:/project"},
                              stage_result={"status": "inventoried"})
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("UPDATE checkpoints SET state_digest = 'not-a-digest' WHERE run_id = 'corrupt'")
            connection.commit()
        finally:
            connection.close()
        checkpoint = store.get_checkpoint("corrupt")
        self.assertFalse(checkpoint["valid"])
        self.assertIn("integrity", checkpoint["invalid_reason"])

    def test_conversations_keep_runs_separate_and_associated(self):
        store = Store(self.path)
        first = store.create_conversation(title="First")
        second = store.create_conversation(title="Second")
        for run_id, conversation_id in (("run-first", first["id"]), ("run-second", second["id"])):
            store.create_run({"run_id": run_id, "conversation_id": conversation_id,
                              "workspace": "C:/project", "model": "model", "task": run_id,
                              "timeout": 30, "status": "completed", "started_at": "now"})
        self.assertEqual(store.get_conversation_detail(first["id"])["runs"][0]["run_id"], "run-first")
        self.assertEqual(store.get_conversation_detail(second["id"])["runs"][0]["run_id"], "run-second")

    def test_project_migration_is_idempotent_and_projects_with_same_workspace_stay_separate(self):
        store = Store(self.path)
        legacy = store.create_conversation(workspace="C:/legacy", model="old-model", title="Legacy")
        store.create_run({"run_id": "legacy-run", "conversation_id": legacy["id"], "workspace": "C:/legacy",
                          "model": "old-model", "task": "keep", "timeout": 30, "status": "interrupted", "started_at": "now"})
        store.save_checkpoint("legacy-run", stage="agent_observe", next_stage="agent_decide",
                              state={"workspace": "C:/legacy"}, stage_result={"status": "agent_observed"})
        store.add_memory(content="Legacy decision", kind="user_decision", workspace="C:/legacy", verification="user_confirmed")
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("DELETE FROM schema_meta WHERE key = 'project_core_v1'")
            connection.commit()
        finally:
            connection.close()
        migrated = Store(self.path)
        project = migrated.list_projects()[0]
        self.assertEqual(migrated.get_run("legacy-run")["project_id"], project["project_id"])
        self.assertEqual(migrated.get_conversation(legacy["id"])["project_id"], project["project_id"])
        self.assertEqual(migrated.list_memory(project_id=project["project_id"])[0]["content"], "Legacy decision")
        self.assertEqual(migrated.get_checkpoint("legacy-run")["next_stage"], "agent_decide")
        self.assertEqual(len(Store(self.path).list_projects()), 1)

        first = migrated.create_project(name="First", workspace="C:/same", model="first-model", num_ctx=8192)
        second = migrated.create_project(name="Second", workspace="C:/same", model="second-model", num_ctx=32768)
        migrated.add_memory(content="First only", kind="user_decision", workspace="C:/same", project_id=first["project_id"], verification="user_confirmed")
        migrated.add_memory(content="Second only", kind="user_decision", workspace="C:/same", project_id=second["project_id"], verification="user_confirmed")
        self.assertEqual(migrated.list_memory(project_id=first["project_id"])[0]["content"], "First only")
        self.assertEqual(migrated.list_memory(project_id=second["project_id"])[0]["content"], "Second only")

    def test_project_repository_index_is_incremental_and_project_scoped(self):
        store = Store(self.path)
        root = Path.cwd() / f".reaper-index-{uuid.uuid4().hex}"
        try:
            root.mkdir()
            (root / "package").mkdir()
            source = root / "package" / "worker.py"
            source.write_text("from . import helper\n\nclass Worker:\n    async def run(self, value: int) -> str:\n        return str(value)\n", encoding="utf-8")
            (root / "package" / "helper.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
            (root / "node_modules").mkdir()
            (root / "node_modules" / "ignored.js").write_text("export const ignored = true;", encoding="utf-8")
            first = store.create_project(name="First", workspace=str(root))
            second = store.create_project(name="Second", workspace=str(root))

            initial = refresh_project_index(store, first)
            self.assertEqual(initial["parsed_files"], 2)
            self.assertIn("Worker", [item["name"] for item in store.find_index_symbols(first["project_id"], "Worker")])
            method = store.find_index_symbols(first["project_id"], "run")[0]
            self.assertEqual((method["kind"], method["start_line"], method["end_line"]), ("async_method", 4, 5))
            self.assertEqual(store.index_imports(first["project_id"], "package/worker.py")[0]["local_module"], "package.helper")
            self.assertEqual(store.list_index_files(first["project_id"], query="ignored"), [])

            unchanged = refresh_project_index(store, first)
            self.assertEqual((unchanged["parsed_files"], unchanged["unchanged_files"]), (0, 2))
            source.write_text("def replacement(name: str):\n    return name\n", encoding="utf-8")
            (root / "package" / "helper.py").unlink()
            changed = refresh_project_index(store, first)
            self.assertEqual((changed["parsed_files"], changed["removed_files"]), (1, 1))
            self.assertEqual([item["name"] for item in store.find_index_symbols(first["project_id"], "replacement")], ["replacement"])

            refresh_project_index(store, second)
            self.assertEqual(store.index_status(first["project_id"])["file_count"], 1)
            self.assertEqual(store.index_status(second["project_id"])["file_count"], 1)
            self.assertNotEqual(store.index_status(first["project_id"])["project_id"], store.index_status(second["project_id"])["project_id"])
        finally:
            shutil.rmtree(root, ignore_errors=True)


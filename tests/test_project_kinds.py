# Copyright (c) 2026 Robert Backhaus

import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from reaper_ai import api, mcp_client
from reaper_ai.persistence import Store


class ProjectKindTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="reaper-ai-kinds-"))
        self.previous = (api._store, api._runs, api._active, api._procs)
        api._store = Store(self.directory / "reaper-ai.sqlite3")
        api._runs, api._active, api._procs = {}, None, {}

    def tearDown(self):
        api._store, api._runs, api._active, api._procs = self.previous
        shutil.rmtree(self.directory, ignore_errors=True)

    def start(self, request: api.StartRequest) -> tuple[dict, dict]:
        captured = {}

        class Process:
            pid = 4321

        def fake_popen(command, **kwargs):
            captured.update(command=command, env=kwargs["env"])
            return Process()

        with patch.object(api, "_installed_models", return_value=["model", "qwen3.6:35b"]), \
             patch.object(api.subprocess, "Popen", side_effect=fake_popen), patch.object(api.threading, "Thread"), \
             patch.object(api, "_validate_mcp_selection", side_effect=lambda ids: ids):
            return api.start_run(request), captured

    def test_existing_database_keeps_projects_as_coding_and_legacy_chats_untouched(self):
        path = self.directory / "old.sqlite3"
        old = sqlite3.connect(path)
        old.executescript(
            "CREATE TABLE projects (project_id TEXT PRIMARY KEY, name TEXT NOT NULL, workspace TEXT NOT NULL, "
            "description TEXT, system_prompt TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT 'm', "
            "temperature REAL NOT NULL DEFAULT 0.0, top_p REAL NOT NULL DEFAULT 0.9, top_k INTEGER NOT NULL DEFAULT 40, "
            "num_ctx INTEGER NOT NULL DEFAULT 16384, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);"
            "CREATE TABLE conversations (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, "
            "title TEXT NOT NULL, workspace TEXT, model TEXT, project_id TEXT);"
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
            "INSERT INTO schema_meta VALUES('project_core_v1', 'then');"
            "INSERT INTO projects(project_id, name, workspace, created_at, updated_at) VALUES('p1', 'Repo', 'C:/repo', 't', 't');"
            "INSERT INTO conversations VALUES('c1', 't', 't', 'Project chat', 'C:/repo', 'm', 'p1');"
            "INSERT INTO conversations VALUES('c2', 't', 't', 'Old general chat', NULL, 'm', NULL);"
        )
        old.commit()
        old.close()
        store = Store(path)
        project = store.get_project("p1")
        self.assertEqual(project["kind"], "coding")
        self.assertEqual(project["workspace"], "C:/repo")
        self.assertEqual([item["id"] for item in store.list_conversations(legacy_only=True)], ["c2"])
        self.assertEqual({item["id"] for item in store.list_conversations()}, {"c1", "c2"})
        self.assertIsNone(store.get_conversation("c2")["project_id"])

    def test_chat_project_needs_no_workspace_and_has_no_workspace_features(self):
        with self.assertRaises(HTTPException) as missing:
            api.create_project(api.ProjectCreateRequest(name="Code"))
        self.assertEqual(missing.exception.status_code, 422)
        project = api.create_project(api.ProjectCreateRequest(name="Research", kind="chat"))
        self.assertEqual((project["kind"], project["workspace"]), ("chat", None))
        listed = api.list_projects()["projects"][0]
        self.assertEqual((listed["kind"], listed["workspace"]), ("chat", None))
        project_id = project["project_id"]
        for call in (lambda: api.get_index_status(project_id), lambda: api.get_project_file(project_id, "a.py"),
                     lambda: api.get_project_changes(project_id), lambda: api.get_project_change_refs(project_id)):
            with self.assertRaises(HTTPException) as blocked:
                call()
            self.assertEqual(blocked.exception.status_code, 409)
        with self.assertRaises(HTTPException) as update:
            api.update_project(project_id, api.ProjectUpdateRequest(workspace=str(self.directory)))
        self.assertEqual(update.exception.status_code, 422)
        self.assertEqual(api.update_project(project_id, api.ProjectUpdateRequest(name="Renamed"))["kind"], "chat")
        conversation = api.create_conversation(api.ConversationRequest(project_id=project_id, title="Q"))
        self.assertIsNone(conversation["workspace"])
        memory = api.add_project_memory(project_id, api.MemoryRequest(content="Prefers short answers"))
        self.assertIsNone(memory["workspace"])

    def test_chat_project_run_uses_scratch_workspace_and_its_mcp_selection(self):
        project = api._store.create_project(name="Research", workspace="", kind="chat", model="model",
                                            mcp_servers=["searxng"])
        response, captured = self.start(api.StartRequest(project_id=project["project_id"], task="What is new?"))
        scratch = (self.directory / "chat-workspace").resolve()
        self.assertEqual(response["workspace"], str(scratch))
        self.assertTrue(scratch.is_dir())
        self.assertNotIn("REAPER_PROJECT_ID", captured["env"])
        self.assertEqual(json.loads(captured["env"][mcp_client.SELECTION_ENV]), ["searxng"])
        self.assertIsNone(api._store.get_conversation(response["conversation_id"])["workspace"])

    def test_legacy_run_without_workspace_is_a_general_chat_with_all_mcp_servers(self):
        response, captured = self.start(api.StartRequest(task="Explain SSE", model="model"))
        self.assertEqual(response["workspace"], str((self.directory / "chat-workspace").resolve()))
        self.assertIsNone(response["project_id"])
        self.assertNotIn(mcp_client.SELECTION_ENV, captured["env"])
        conversation = api._store.get_conversation(response["conversation_id"])
        self.assertIsNone(conversation["project_id"])
        self.assertIsNone(conversation["workspace"])
        self.assertEqual([item["id"] for item in api.list_conversations(legacy=True)["conversations"]],
                         [conversation["id"]])

    def test_coding_project_run_is_unchanged(self):
        project = api._store.create_project(name="Repo", workspace=str(self.directory), model="model")
        with patch.object(api, "_refresh_index") as refresh:
            response, captured = self.start(api.StartRequest(project_id=project["project_id"], task="Fix it"))
        refresh.assert_called_once()
        self.assertEqual(response["workspace"], str(self.directory.resolve()))
        self.assertEqual(captured["env"]["REAPER_PROJECT_ID"], project["project_id"])
        self.assertFalse((self.directory / "chat-workspace").exists())


if __name__ == "__main__":
    unittest.main()

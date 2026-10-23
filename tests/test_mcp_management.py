# Copyright (c) 2026 Robert Backhaus

import json
import os
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from reaper_ai import api, mcp_client
from reaper_ai.persistence import Store


class MCPManagementTests(unittest.TestCase):
    def setUp(self):
        self.config = Path.cwd() / f".reaper-ai-test-mcp-{uuid.uuid4().hex}.json"
        self.config.write_text(json.dumps({"keep": 1, "mcpServers": {
            "github": {"command": "npx", "args": ["-y", "server-github"]},
            "searxng": {"url": "http://127.0.0.1:9/mcp", "disabled": True},
        }}), encoding="utf-8")
        self.db = Path.cwd() / f".reaper-ai-test-{uuid.uuid4().hex}.sqlite3"
        self.environment = patch.dict(os.environ, {"REAPER_MCP_CONFIG": str(self.config)})
        self.environment.start()
        os.environ.pop(mcp_client.SELECTION_ENV, None)
        self.previous_store = api._store
        api._store = Store(self.db)
        mcp_client._run_servers = None

    def tearDown(self):
        api._store = self.previous_store
        mcp_client._run_servers = None
        self.environment.stop()
        self.config.unlink(missing_ok=True)
        for suffix in ("", "-shm", "-wal"):
            Path(str(self.db) + suffix).unlink(missing_ok=True)

    def test_global_changes_edit_only_mcp_json_and_keep_project_ids_consistent(self):
        workspace = str(Path.cwd().resolve())
        project = api.create_project(api.ProjectCreateRequest(name="A", workspace=workspace,
                                                              mcp_servers=["github"]))
        api.save_mcp_server("files", api.MCPServerRequest(config={"command": "python", "args": ["fs.py"]}))
        api.set_mcp_server_state("github", api.MCPServerStateRequest(enabled=False))
        api.save_mcp_server("gh", api.MCPServerRequest(config={"command": "npx", "disabled": True},
                                                       previous_id="github"))
        stored = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(stored["keep"], 1)
        self.assertEqual(list(stored["mcpServers"]), ["gh", "searxng", "files"])
        self.assertTrue(stored["mcpServers"]["gh"]["disabled"])
        self.assertEqual(api.get_project(project["project_id"])["mcp_servers"], ["gh"])
        listed = {item["id"]: item for item in api.list_mcp_servers()["servers"]}
        self.assertEqual((listed["searxng"]["transport"], listed["searxng"]["enabled"]), ("http", False))
        api.delete_mcp_server("gh")
        self.assertEqual(api.get_project(project["project_id"])["mcp_servers"], [])
        with self.assertRaises(HTTPException):
            api.update_project(project["project_id"], api.ProjectUpdateRequest(mcp_servers=["missing"]))
        with self.assertRaises(HTTPException):
            api.save_mcp_server("bad id", api.MCPServerRequest(config={"command": "x"}))

    def test_runs_see_only_enabled_selected_servers_and_keep_their_start_snapshot(self):
        environment = {}
        api._mcp_environment(environment, {"mcp_servers": ["github", "searxng"]})
        with patch.dict(os.environ, environment):
            selected = mcp_client._active_servers()
            self.assertEqual(list(selected), ["github"])
            # Discovery stores this filtered start snapshot for later tool calls.
            mcp_client._run_servers = selected
            # A configuration change during the run does not alter the started run.
            mcp_client.delete_server("github")
            self.assertEqual(mcp_client._resolve_tool("mcp:github:search")[0], "github")
            mcp_client._run_servers = None
            with self.assertRaises(mcp_client.MCPClientError):
                mcp_client._resolve_tool("mcp:github:search")
        legacy = {mcp_client.SELECTION_ENV: "[]"}
        api._mcp_environment(legacy, None)
        self.assertNotIn(mcp_client.SELECTION_ENV, legacy)


if __name__ == "__main__":
    unittest.main()

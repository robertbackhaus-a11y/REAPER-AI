import os
import subprocess
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from reaper_ai import engine
from reaper_ai.persistence import Store


class RecoveryEngineTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path.cwd() / f".reaper-ai-recovery-{uuid.uuid4().hex}"
        self.directory.mkdir()
        self.database = self.directory / "reaper-ai.sqlite3"
        self.source = self.directory / "module.py"
        self.source.write_text("value = 1\n", encoding="utf-8")

    def tearDown(self):
        for path in self.directory.glob("*"):
            path.unlink()
        self.directory.rmdir()

    def test_engine_recovery_starts_at_checkpoint_next_stage(self):
        store = Store(self.database)
        store.create_run({"run_id": "resume-stage", "workspace": str(self.directory), "model": "model",
                          "task": "change", "timeout": 30, "status": "interrupted", "started_at": "now"})
        state = {"workspace": str(self.directory), "task": "change", "inventory": [], "selected": ["module.py"],
                 "creating": [], "originals": {"module.py": "value = 1\n"},
                 "changes": {"module.py": "value = 2\n"}, "status": "proposed", "error": "",
                 "details": [], "corrections": 0, "feedback": ""}
        store.save_checkpoint("resume-stage", stage="propose", next_stage="apply", state=state,
                              stage_result={"status": "proposed"})
        captured = {}

        class Graph:
            def invoke(self, received, config):
                captured["state"] = received
                return {**received, "status": "structurally_verified", "details": [], "error": ""}

        with patch.dict(os.environ, {"REAPER_DATA_DIR": str(self.directory), "REAPER_API_RUN_ID": "resume-stage",
                                     "REAPER_RESUME": "1"}, clear=False), \
             patch.object(engine, "build_graph", side_effect=lambda stage: captured.setdefault("stage", stage) and Graph()):
            result = engine._original_run("ignored original task", str(self.directory))
        self.assertEqual(captured["stage"], "apply")
        self.assertEqual(captured["state"]["changes"]["module.py"], "value = 2\n")
        self.assertEqual(result["status"], "completed")


class FileSelectionRegressionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path.cwd() / f".reaper-ai-selection-{uuid.uuid4().hex}"
        self.root.mkdir()
        (self.root / "module.py").write_text("value = 1\n", encoding="utf-8")
        (self.root / "pyproject.toml").write_text(
            "[project]\nname = 'sample'\nversion = '0.1.0'\n", encoding="utf-8"
        )

    def tearDown(self):
        for path in self.root.iterdir():
            path.unlink()
        self.root.rmdir()

    def test_tracked_toml_is_selected_edited_and_empty_selection_is_explained(self):
        state = {"workspace": str(self.root), "task": "Set version to 0.2.0", "inventory": [],
                 "selected": [], "creating": [], "originals": {}, "changes": {}, "status": "pending",
                 "error": "", "details": [], "corrections": 0, "feedback": ""}

        def fake_git(root, *args):
            if args[:2] == ("rev-parse", "--show-toplevel"):
                return subprocess.CompletedProcess(args, 0, str(root) + "\n", "")
            if args[:2] == ("ls-files", "--cached"):
                return subprocess.CompletedProcess(args, 0, "module.py\npyproject.toml\n", "")
            if args[:3] == ("diff", "HEAD", "--quiet"):
                return subprocess.CompletedProcess(args, 0, "", "")
            if args[:4] == ("--no-pager", "diff", "--no-ext-diff", "HEAD"):
                return subprocess.CompletedProcess(args, 0, "diff --git a/pyproject.toml b/pyproject.toml\n", "")
            raise AssertionError(f"Unexpected Git call: {args}")

        with patch.object(engine, "git", side_effect=fake_git):
            state.update(engine.inventory(state))
            self.assertIn("pyproject.toml", [item["path"] for item in state["inventory"]])

            with patch.object(engine, "ask_json", return_value={"edit": ["pyproject.toml"], "create": []}):
                state.update(engine.select(state))
            self.assertEqual(state["status"], "selected")
            self.assertIn("version = '0.1.0'", state["originals"]["pyproject.toml"])

            changed = "[project]\nname = 'sample'\nversion = '0.2.0'\n"
            with patch.object(engine, "ask_json", return_value={"changes": {"pyproject.toml": changed}}):
                state.update(engine.propose(state))
            self.assertEqual(state["status"], "proposed")
            state.update(engine.apply(state))
            state.update(engine.verify(state))
            self.assertEqual(state["status"], "structurally_verified")
            self.assertEqual(state["details"], [{"path": "pyproject.toml", "syntax": "ok", "git": "diff"}])

            with patch.object(engine, "ask_json", return_value={"edit": [], "create": []}):
                failed = engine.select({**state, "status": "inventoried", "error": ""})
            self.assertEqual(failed["error"], "File selection must contain 1 to 3 paths (got 0)")

            with patch.object(engine, "ask_json", return_value={"edit": ["unknown.toml"], "create": []}):
                failed = engine.select({**state, "status": "inventoried", "error": ""})
            self.assertEqual(failed["error"], "Selected existing paths are not in the inventory: unknown.toml")

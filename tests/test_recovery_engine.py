import os
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

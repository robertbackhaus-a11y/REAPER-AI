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

    def test_engine_recovery_starts_at_agent_decision_checkpoint(self):
        store = Store(self.database)
        store.create_run({"run_id": "resume-stage", "workspace": str(self.directory), "model": "model",
                          "task": "change", "timeout": 30, "status": "interrupted", "started_at": "now"})
        state = {"workspace": str(self.directory), "task": "change", "status": "agent_observed", "error": "",
                 "details": [], "last_result": None,
                 "agent": {"objective": "change", "project_context": {"workspace": str(self.directory)},
                           "plan": [], "current_step": 1, "next_action": None, "observations": [],
                           "baselines": {}, "change_evidence": [],
                           "verification": {"status": "not_started", "checks": []},
                           "budgets": {"tool_calls": 1, "max_wall_seconds": 600}}}
        store.save_checkpoint("resume-stage", stage="agent_observe", next_stage="agent_decide", state=state,
                              stage_result={"status": "agent_observed"})
        captured = {}

        class Graph:
            def invoke(self, received, config):
                captured["state"] = received
                return {**received, "status": "agent_completed", "details": [], "error": "",
                        "agent": {**received["agent"], "verification": {"summary": "resumed"}}}

        with patch.dict(os.environ, {"REAPER_DATA_DIR": str(self.directory), "REAPER_API_RUN_ID": "resume-stage",
                                     "REAPER_RESUME": "1"}, clear=False), \
             patch.object(engine, "build_agent_graph", side_effect=lambda stage: captured.setdefault("stage", stage) and Graph()):
            result = engine._original_run("ignored original task", str(self.directory))
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(captured["stage"], "agent_decide")
        self.assertEqual(captured["state"]["agent"]["current_step"], 1)


class FileSelectionRegressionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path.cwd() / f".reaper-ai-selection-{uuid.uuid4().hex}"
        self.root.mkdir()
        (self.root / "module.py").write_text("value = 1\n", encoding="utf-8")
        (self.root / "pyproject.toml").write_text(
            "[project]\nname = 'sample'\nversion = '0.1.0'\n", encoding="utf-8"
        )
        for name in ("api.py", "persistence.py"):
            (self.root / name).write_text("value = 1\n", encoding="utf-8")
        for name, marker in (("engine.py", "engine_marker"), ("tools.py", "tools_marker"),
                             ("system_tools.py", "system_tools_marker")):
            (self.root / name).write_text(
                f"def {marker}():\n    return 'old-{marker}'\n\n# untouched\n" + "# context\n" * 2_500,
                encoding="utf-8",
            )
        (self.root / "large.txt").write_text("x" * 20_001, encoding="utf-8")
        self.duplicate_source = (
            "def first():\n    return 'shared'\n\n"
            "def second():\n    return 'shared'\n"
        )
        (self.root / "duplicates.py").write_text(self.duplicate_source, encoding="utf-8")
        (self.root / "image.bin").write_bytes(b"\0binary")

    def tearDown(self):
        for path in self.root.iterdir():
            path.unlink()
        self.root.rmdir()

    def test_full_inventory_supports_large_text_and_safe_text_selection(self):
        state = {"workspace": str(self.root), "task": "Set version to 0.2.0", "inventory": [],
                 "selected": [], "creating": [], "originals": {}, "analysis": [], "edits": [], "changes": {}, "status": "pending",
                 "error": "", "details": [], "corrections": 0, "feedback": ""}

        def fake_git(root, *args):
            if args[:2] == ("rev-parse", "--show-toplevel"):
                return subprocess.CompletedProcess(args, 0, str(root) + "\n", "")
            if args[:2] == ("ls-files", "--cached"):
                return subprocess.CompletedProcess(
                    args, 0, "module.py\npyproject.toml\nengine.py\ntools.py\nsystem_tools.py\napi.py\npersistence.py\nlarge.txt\nimage.bin\n", ""
                )
            if args[:3] == ("diff", "HEAD", "--quiet"):
                return subprocess.CompletedProcess(args, 0, "", "")
            if args[:4] == ("--no-pager", "diff", "--no-ext-diff", "HEAD"):
                return subprocess.CompletedProcess(args, 0, "diff --git a/pyproject.toml b/pyproject.toml\n", "")
            raise AssertionError(f"Unexpected Git call: {args}")

        with patch.object(engine, "git", side_effect=fake_git):
            state.update(engine.inventory(state))
            entries = {item["path"]: item for item in state["inventory"]}
            self.assertTrue({"engine.py", "tools.py", "system_tools.py", "api.py", "persistence.py", "pyproject.toml", "large.txt"} <= set(entries))
            self.assertEqual(entries["large.txt"], {"path": "large.txt", "kind": "text", "bytes": 20_001})
            self.assertEqual(entries["image.bin"]["kind"], "binary")
            self.assertEqual(len(engine._read_text_sections(self.root / "large.txt")), 20_001)

            with patch.object(engine, "ask_json", return_value={
                "edit": ["engine.py", "tools.py", "system_tools.py", "api.py", "persistence.py"], "create": []
            }):
                expanded = {**state, **engine.select({**state, "status": "inventoried", "error": ""})}
            self.assertEqual(expanded["status"], "selected")
            self.assertEqual(engine._next_stage("select", expanded), "analyze")
            self.assertGreater(sum(len(content) for content in expanded["originals"].values()),
                               engine._model_context_budget())
            expanded.update(engine.analyze(expanded))
            self.assertEqual(expanded["status"], "analyzed")
            self.assertTrue({"engine.py", "tools.py", "system_tools.py"} <=
                            {section["path"] for section in expanded["analysis"]})
            self.assertLessEqual(engine._proposal_prompt_size(expanded, expanded["analysis"]),
                                 engine._model_context_budget())

            def edit_for(path, expected, replacement):
                section = next(section for section in expanded["analysis"]
                               if section["path"] == path and expected in section["content"])
                return {key: section[key] for key in ("path", "start_line", "end_line", "start_offset", "end_offset", "source_digest")} | {
                    "expected": expected, "replacement": replacement
                }

            multi_edits = [
                edit_for("engine.py", "    return 'old-engine_marker'\n", "    return 'new-engine_marker'\n"),
                edit_for("tools.py", "    return 'old-tools_marker'\n", "    return 'new-tools_marker'\n"),
                edit_for("system_tools.py", "    return 'old-system_tools_marker'\n", "    return 'new-system_tools_marker'\n"),
            ]
            with patch.object(engine, "ask_json", return_value={"edits": multi_edits, "new_files": {}}):
                expanded.update(engine.propose(expanded))
            self.assertEqual(expanded["status"], "proposed", expanded["error"])
            self.assertEqual(set(expanded["changes"]), {"engine.py", "tools.py", "system_tools.py"})
            expanded.update(engine.apply(expanded))
            self.assertEqual(expanded["status"], "written", expanded["error"])
            for name, marker in (("engine.py", "engine_marker"), ("tools.py", "tools_marker"),
                                 ("system_tools.py", "system_tools_marker")):
                content = (self.root / name).read_text(encoding="utf-8")
                self.assertIn(f"new-{marker}", content)
                self.assertIn("# untouched", content)

            def duplicate_state():
                return {"workspace": str(self.root), "task": "Update only second", "inventory": [],
                        "selected": ["duplicates.py"], "creating": [],
                        "originals": {"duplicates.py": self.duplicate_source}, "analysis": [], "edits": [],
                        "changes": {}, "status": "selected", "error": "", "details": [],
                        "corrections": 0, "feedback": ""}

            targeted = duplicate_state()
            targeted.update(engine.analyze(targeted))
            second = next(section for section in targeted["analysis"] if section["label"] == "function second")
            duplicate_edit = {key: second[key] for key in
                              ("path", "start_line", "end_line", "start_offset", "end_offset", "source_digest")} | {
                "expected": "    return 'shared'\n", "replacement": "    return 'updated'\n"
            }
            with patch.object(engine, "ask_json", return_value={"edits": [duplicate_edit], "new_files": {}}):
                targeted.update(engine.propose(targeted))
            self.assertEqual(targeted["status"], "proposed", targeted["error"])
            targeted.update(engine.apply(targeted))
            self.assertEqual(targeted["status"], "written", targeted["error"])
            self.assertEqual((self.root / "duplicates.py").read_text(encoding="utf-8"),
                             self.duplicate_source.replace("def second():\n    return 'shared'", "def second():\n    return 'updated'"))

            (self.root / "duplicates.py").write_text(self.duplicate_source, encoding="utf-8")
            conflicted = duplicate_state()
            conflicted.update(engine.analyze(conflicted))
            second = next(section for section in conflicted["analysis"] if section["label"] == "function second")
            conflict_edit = {key: second[key] for key in
                             ("path", "start_line", "end_line", "start_offset", "end_offset", "source_digest")} | {
                "expected": "    return 'shared'\n", "replacement": "    return 'updated'\n"
            }
            with patch.object(engine, "ask_json", return_value={"edits": [conflict_edit], "new_files": {}}):
                conflicted.update(engine.propose(conflicted))
            external = self.duplicate_source.replace("def second():\n    return 'shared'", "def second():\n    return 'external'")
            (self.root / "duplicates.py").write_text(external, encoding="utf-8")
            conflicted.update(engine.apply(conflicted))
            self.assertEqual(conflicted["status"], "failed")
            self.assertIn("Expected text is absent from the selected range for duplicates.py", conflicted["error"])
            self.assertEqual((self.root / "duplicates.py").read_text(encoding="utf-8"), external)

            outside_range_change = self.duplicate_source + "# concurrent edit outside target range\n"
            (self.root / "duplicates.py").write_text(outside_range_change, encoding="utf-8")
            conflicted.update(engine.apply(conflicted))
            self.assertEqual(conflicted["status"], "failed")
            self.assertIn("Source digest changed before write for duplicates.py", conflicted["error"])
            self.assertEqual((self.root / "duplicates.py").read_text(encoding="utf-8"), outside_range_change)

            with patch.object(engine, "ask_json", return_value={"edit": ["image.bin"], "create": []}):
                binary = engine.select({**state, "status": "inventoried", "error": ""})
            self.assertEqual(binary["error"], "Binary files cannot be edited as UTF-8 text: image.bin")

            with patch.object(engine, "ask_json", return_value={"edit": ["pyproject.toml"], "create": []}):
                state.update(engine.select(state))
            self.assertEqual(state["status"], "selected")
            self.assertIn("version = '0.1.0'", state["originals"]["pyproject.toml"])

            state.update(engine.analyze(state))
            section = next(section for section in state["analysis"] if section["path"] == "pyproject.toml")
            edit = {key: section[key] for key in ("path", "start_line", "end_line", "start_offset", "end_offset", "source_digest")} | {
                "expected": "version = '0.1.0'", "replacement": "version = '0.2.0'"
            }
            with patch.object(engine, "ask_json", return_value={"edits": [edit], "new_files": {}}):
                state.update(engine.propose(state))
            self.assertEqual(state["status"], "proposed")
            state.update(engine.apply(state))
            state.update(engine.verify(state))
            self.assertEqual(state["status"], "structurally_verified")
            self.assertEqual(state["details"], [{"path": "pyproject.toml", "syntax": "ok", "git": "diff"}])

            with patch.object(engine, "ask_json", return_value={"edit": [], "create": []}):
                failed = engine.select({**state, "status": "inventoried", "error": ""})
            self.assertEqual(failed["error"], "File selection must contain at least one path")

            with patch.object(engine, "ask_json", return_value={"edit": ["unknown.toml"], "create": []}):
                failed = engine.select({**state, "status": "inventoried", "error": ""})
            self.assertEqual(failed["error"], "Selected existing paths are not in the inventory: unknown.toml")

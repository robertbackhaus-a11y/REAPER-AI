import unittest
import uuid
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from reaper_ai.agent import dispatch_tool, new_agent_state
from reaper_ai import engine
from reaper_ai.persistence import Store


class AgentDispatcherTests(unittest.TestCase):
    def test_event_output_is_utf8_with_legacy_windows_codepage(self):
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "cp1252"
        result = subprocess.run(
            [sys.executable, "-c", "from reaper_ai.engine import _emit_event; _emit_event('unicode_probe', text='Müll € �')"],
            capture_output=True, env=env, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
        output = result.stdout.decode("utf-8")
        self.assertIn("Müll € �", output)

    def test_dispatcher_reads_writes_observes_failures_and_timeouts(self):
        root = Path.cwd() / f".reaper-agent-{uuid.uuid4().hex}"
        outside = Path.cwd() / f".reaper-agent-outside-{uuid.uuid4().hex}.txt"
        root.mkdir()
        try:
            (root / "sample.txt").write_text("first line\nneedle line\nthird line\n", encoding="utf-8")
            context = {"workspace": str(root)}

            state = new_agent_state()
            self.assertEqual(state["current_step"], 0)
            self.assertEqual(state["budgets"]["tool_calls"], 0)

            listed = dispatch_tool({"tool": "list_files", "args": {}, "context": context})
            self.assertTrue(listed["ok"], listed)
            self.assertEqual(listed["data"]["files"][0]["path"], "sample.txt")

            read = dispatch_tool({"tool": "read_file", "args": {"path": "sample.txt"}, "context": context})
            self.assertTrue(read["ok"], read)
            self.assertIn("needle line", read["data"]["content"])

            written = dispatch_tool({"tool": "write_file", "args": {
                "path": "sample.txt", "content": "updated\n", "expected_hash": read["data"]["sha256"]
            }, "context": context})
            self.assertTrue(written["ok"], written)
            self.assertEqual(written["changes"][0]["path"], "sample.txt")
            self.assertEqual((root / "sample.txt").read_text(encoding="utf-8"), "updated\n")
            (root / "sample.txt").write_text("first line\nneedle line\nthird line\n", encoding="utf-8")

            outside.write_text("outside before\n", encoding="utf-8")
            outside_read = dispatch_tool({"tool": "read_file", "args": {"path": str(outside)}, "context": context})
            outside_write = dispatch_tool({"tool": "write_file", "args": {
                "path": str(outside), "content": "outside after\n", "expected_hash": outside_read["data"]["sha256"]
            }, "context": context})
            self.assertTrue(outside_write["ok"], outside_write)
            self.assertEqual(outside.read_text(encoding="utf-8"), "outside after\n")

            ranged = dispatch_tool({"tool": "read_file_range", "args": {"path": "sample.txt", "start_line": 2,
                                                                            "end_line": 2}, "context": context})
            self.assertEqual(ranged["data"]["content"], "needle line\n")

            found = dispatch_tool({"tool": "search_text", "args": {"query": "needle"}, "context": context})
            self.assertTrue(found["ok"], found)
            self.assertEqual(found["data"]["matches"][0]["line"], 2)

            python = dispatch_tool({"tool": "find_python", "args": {}, "context": context})
            self.assertTrue(python["ok"], python)
            self.assertIn("executables", python["data"])

            location = dispatch_tool({"tool": "shell", "args": {"command": "Get-Location", "timeout": 10},
                                      "context": context})
            self.assertTrue(location["ok"], location)
            self.assertEqual(location["exit_code"], 0)
            self.assertIn(str(root), location["stdout"])

            failed = dispatch_tool({"tool": "shell", "args": {"command": "Write-Error 'expected failure'; exit 7",
                                                                   "timeout": 10}, "context": context},
                                   )
            self.assertFalse(failed["ok"])
            self.assertEqual(failed["exit_code"], 7)
            self.assertIn("expected failure", failed["stderr"])

            timed_out = dispatch_tool({"tool": "shell", "args": {"command": "Start-Sleep -Seconds 2", "timeout": 1},
                                      "context": context})
            self.assertFalse(timed_out["ok"])
            self.assertEqual(timed_out["exit_code"], -1)
            self.assertIn("timed out", timed_out["error"].lower())

            autonomous = dispatch_tool({"tool": "shell", "args": {"command": "Write-Output autonomous"},
                                        "context": context})
            self.assertTrue(autonomous["ok"], autonomous)
            self.assertIn("autonomous", autonomous["stdout"])
        finally:
            outside.unlink(missing_ok=True)
            sample = root / "sample.txt"
            if sample.exists():
                sample.unlink()
            root.rmdir()

        git_status = dispatch_tool({"tool": "git_status", "args": {},
                                    "context": {"workspace": str(Path.cwd())}})
        self.assertTrue(git_status["ok"], git_status)

    def test_read_only_agent_corrects_tool_error_and_resumes_at_decision_checkpoint(self):
        root = Path.cwd() / f".reaper-agent-loop-{uuid.uuid4().hex}"
        root.mkdir()
        try:
            (root / "sample.txt").write_text("first line\nneedle line\nthird line\n", encoding="utf-8")
            store = Store(root / "reaper-ai.sqlite3")
            store.create_run({"run_id": "agent-loop", "workspace": str(root), "model": "test-model",
                              "task": "inspect sample", "timeout": 30, "status": "running", "started_at": "now"})
            decisions = iter([
                {"kind": "action", "plan": [{"step": "find the relevant text"}],
                 "action": {"tool": "search_text", "args": {"query": "needle"}}},
                {"kind": "action", "action": {"tool": "read_file_range",
                                                    "args": {"path": "missing.txt", "start_line": 1, "end_line": 1}}},
                {"kind": "action", "action": {"tool": "read_file_range",
                                                    "args": {"path": "sample.txt", "start_line": 2, "end_line": 2}}},
                {"kind": "final", "summary": "The selected line contains needle.",
                 "verification": "search and targeted read succeeded"},
                {"kind": "final", "summary": "The selected line contains needle.",
                 "verification": "checkpointed observation remains valid"},
            ])

            with patch.dict("os.environ", {"REAPER_DATA_DIR": str(root), "REAPER_API_RUN_ID": "agent-loop"},
                            clear=False), patch.object(engine, "ask_json", side_effect=lambda *_: next(decisions)):
                result = engine.run("Inspect sample.txt without edits", str(root))
                checkpoint = store.get_checkpoint("agent-loop")
                self.assertEqual(checkpoint["stage"], "agent_observe")
                self.assertEqual(checkpoint["next_stage"], "agent_decide")
                self.assertEqual(result["status"], "completed", result)
                self.assertEqual(result["tool_calls"], 3)
                self.assertFalse(result["checks"][1]["result"]["ok"])
                self.assertTrue(result["checks"][2]["result"]["ok"])

                store.update_run_status("agent-loop", "interrupted")
                with patch.dict("os.environ", {"REAPER_RESUME": "1"}, clear=False):
                    resumed = engine.run("ignored after checkpoint", str(root))
            self.assertEqual(resumed["status"], "completed", resumed)
            self.assertEqual(resumed["tool_calls"], 3)
            self.assertIn("needle", resumed["summary"])
        finally:
            for path in root.glob("*"):
                path.unlink()
            root.rmdir()


if __name__ == "__main__":
    unittest.main()

import unittest
import uuid
import os
import hashlib
import json
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
        git_diff = dispatch_tool({"tool": "git_diff", "args": {},
                                  "context": {"workspace": str(Path.cwd())}})
        self.assertTrue(git_diff["ok"], git_diff)
        self.assertIn("diff", git_diff["data"])
        self.assertIn("error", git_diff["data"])

    def test_agent_keeps_evidence_through_failed_check_correction_and_resume(self):
        root = Path.cwd() / f".reaper-agent-loop-{uuid.uuid4().hex}"
        root.mkdir()
        try:
            initial = "VALUE = 'wrong'\n"
            intermediate = "VALUE = 'still_wrong'\n"
            fixed = "VALUE = 'fixed'\n"
            (root / "sample.py").write_text(initial, encoding="utf-8")
            store = Store(root / "reaper-ai.sqlite3")
            store.create_run({"run_id": "agent-loop", "workspace": str(root), "model": "test-model",
                              "task": "Correct sample.py and verify it", "timeout": 30,
                              "status": "running", "started_at": "now"})
            decisions = iter([
                {"kind": "action", "reason": "Read the existing source before changing it.",
                 "plan": [{"step": "inspect the incorrect value"}],
                 "open_verifications": ["Confirm the corrected value with a command."],
                 "action": {"tool": "read_file", "args": {"path": "sample.py"}}},
                {"kind": "action", "reason": "Apply the first correction attempt.",
                 "plan": [{"step": "write a corrected value"}],
                 "open_verifications": ["Run the value check after the write."],
                 "action": {"tool": "write_file", "args": {
                     "path": "sample.py", "content": intermediate,
                     "expected_hash": hashlib.sha256(initial.encode("utf-8")).hexdigest()}}},
                {"kind": "action", "reason": "Verify the first correction attempt.",
                 "verification_for": ["Run the value check after the write."],
                 "action": {"tool": "shell", "args": {"command": (
                     "if ([IO.File]::ReadAllText('sample.py') -eq \"VALUE = 'fixed'`r`n\") "
                     "{ Write-Output verified } else { Write-Error 'wrong content'; exit 7 }"), "timeout": 10}}},
                {"kind": "action", "reason": "Read the failed correction before replacing it.",
                 "open_verifications": ["Correct the value and rerun the failing check."],
                 "action": {"tool": "read_file", "args": {"path": "sample.py"}}},
                {"kind": "action", "reason": "Correct the value reported by the failed check.",
                 "plan": [{"step": "apply the verified correction"}],
                 "open_verifications": ["Run the value check again."],
                 "action": {"tool": "write_file", "args": {
                     "path": "sample.py", "content": fixed,
                     "expected_hash": hashlib.sha256(intermediate.encode("utf-8")).hexdigest()}}},
                {"kind": "action", "reason": "Verify the corrected file with the same failing check.",
                 "verification_for": ["Run the value check again."],
                 "action": {"tool": "shell", "args": {"command": (
                     "if ([IO.File]::ReadAllText('sample.py') -eq \"VALUE = 'fixed'`r`n\") "
                     "{ Write-Output verified } else { Write-Error 'wrong content'; exit 7 }"), "timeout": 10}}},
                {"kind": "final", "reason": "The corrected value passed the repeated command check.",
                 "summary": "sample.py now contains the fixed value.",
                 "verification": "The final PowerShell check returned verified with exit code 0.",
                 "open_verifications": []},
                {"kind": "final", "reason": "The checkpoint retains the successful verification.",
                 "summary": "sample.py remains fixed.",
                 "verification": "The checkpointed successful command remains observed.",
                 "open_verifications": []},
            ])
            events = []
            with patch.dict("os.environ", {"REAPER_DATA_DIR": str(root), "REAPER_API_RUN_ID": "agent-loop"},
                            clear=False), patch.object(engine, "ask_json", side_effect=lambda *_: next(decisions)), \
                    patch.object(engine, "_emit_event", side_effect=lambda event, **payload: events.append((event, payload))):
                result = engine.run("Correct sample.py and verify it", str(root))
                checkpoint = store.get_checkpoint("agent-loop")
                self.assertEqual(checkpoint["stage"], "agent_observe")
                self.assertEqual(checkpoint["next_stage"], "agent_decide")
                self.assertEqual(result["status"], "completed", result)
                self.assertEqual(result["internal_status"], "agent_completed")
                self.assertEqual(result["tool_calls"], 6)
                self.assertEqual((root / "sample.py").read_text(encoding="utf-8"), fixed)
                self.assertFalse(result["checks"][2]["result"]["ok"])
                self.assertTrue(result["checks"][-1]["result"]["ok"], result["checks"][-1])
                self.assertEqual(result["progress"]["last_change"]["paths"], ["sample.py"])
                self.assertEqual(result["progress"]["open_verifications"], [])
                self.assertTrue(result["progress"]["checks"][-1]["ok"])
                self.assertIn("wrong content", result["progress"]["errors"][0]["error"])
                prompt = engine._agent_prompt(checkpoint["state"])
                prompt_data = json.loads(prompt)
                self.assertIn("progress", prompt_data)
                self.assertLess(prompt.index('"progress"'), prompt.index('"previous_observations"'))
                self.assertTrue(prompt_data["progress"]["checks"][-1]["ok"])
                self.assertEqual(prompt_data["progress"]["open_verifications"], [])
                decision_events = [payload for event, payload in events if event == "agent_decision"]
                self.assertTrue(all("decision_kind" in payload and "progress" in payload and
                                    "verification_status" in payload for payload in decision_events))
                self.assertEqual(decision_events[-1]["decision_kind"], "final")

                store.update_run_status("agent-loop", "interrupted")
                with patch.dict("os.environ", {"REAPER_RESUME": "1"}, clear=False):
                    resumed = engine.run("ignored after checkpoint", str(root))
            self.assertEqual(resumed["status"], "completed", resumed)
            self.assertEqual(resumed["tool_calls"], 6)
            self.assertIn("fixed", resumed["summary"])
        finally:
            for path in root.glob("*"):
                path.unlink()
            root.rmdir()


if __name__ == "__main__":
    unittest.main()

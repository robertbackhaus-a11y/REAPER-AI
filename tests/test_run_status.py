# Copyright (c) 2026 Robert Backhaus

"""P06: agent status and run summary helpers of the web UI (static/run_status.js)."""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "src" / "reaper_ai" / "static"
NODE = shutil.which("node")


def run_js(body: str):
    """Evaluate a snippet against run_status.js in Node and return its JSON output."""
    script = f"const RS = require({json.dumps(str(STATIC / 'run_status.js'))});\n" + body
    proc = subprocess.run([NODE, "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=30)
    if proc.returncode:
        raise AssertionError(proc.stderr)
    return json.loads(proc.stdout)


EVENTS = [
    ("run_started", {"timestamp": 1000.0}),
    ("phase_changed", {"phase": "agent_decide"}),
    ("model_activity", {"status": "started", "model": "qwen"}),
    ("model_activity", {"status": "completed"}),
    ("tool_started", {"tool": "read_file", "action": {"tool": "read_file", "args": {"path": "app.py"}}}),
    ("tool_completed", {"tool": "read_file", "ok": True, "exit_code": 0, "duration_ms": 12.0, "error": ""}),
    ("model_activity", {"status": "started", "model": "qwen"}),
    ("model_activity", {"status": "completed"}),
    ("tool_started", {"tool": "shell", "action": {"tool": "shell", "args": {"command": "python -m pytest -q"}}}),
]


@unittest.skipUnless(NODE, "Node.js is required for the web UI helper tests")
class RunStatusTests(unittest.TestCase):
    def apply(self, events, extra=""):
        return run_js(
            f"const s = RS.newStats(); for (const [t, d] of {json.dumps(events)}) RS.applyEvent(s, t, d);\n{extra}"
        )

    def test_live_status_counts_calls_and_reports_current_activity(self):
        live = self.apply(EVENTS, "const run = {run_id: 'r1', status: 'running', started_at: new Date(1000000).toISOString()};"
                                  "console.log(JSON.stringify(RS.liveStatus(run, s, 1065000)));")
        self.assertEqual(live["label"], "läuft")
        self.assertEqual(live["activity"], "Tests ausführen: python -m pytest -q")
        self.assertEqual(live["modelCalls"], 2)
        self.assertEqual(live["toolCalls"], 1)
        self.assertIn("Datei lesen: app.py · erfolgreich · Exit-Code 0", live["lastAction"])
        self.assertEqual(live["elapsed"], "1 min 05 s")

    def test_failed_tool_shows_error_as_last_action(self):
        events = [("tool_started", {"tool": "apply_patch", "action": {"args": {"path": "x.py"}}}),
                  ("tool_completed", {"tool": "apply_patch", "ok": False, "exit_code": None, "error": "hash mismatch"})]
        live = self.apply(events, "console.log(JSON.stringify(RS.liveStatus({status: 'running'}, s)));")
        self.assertTrue(live["lastAction"].startswith("Code ändern: x.py · fehlgeschlagen"))
        self.assertIn("hash mismatch", live["lastAction"])

    def test_summary_uses_stored_result_only(self):
        run = {"run_id": "r2", "status": "completed", "started_at": "2026-10-22T08:00:00+00:00",
               "finished_at": "2026-10-22T08:02:00+00:00", "result": {
                   "status": "completed", "summary": "Bug behoben.", "changed_files": ["a.py", "a.py", "b.py"],
                   "tool_calls": 3, "usage": {"model_calls": 4, "elapsed_seconds": 118.4},
                   "checks": [
                       {"action": {"tool": "shell", "args": {"command": "python -m unittest"}}, "result": {"ok": True, "exit_code": 0}},
                       {"action": {"tool": "shell", "args": {"command": "pytest tests"}}, "result": {"ok": False, "exit_code": 1, "error": "1 failed"}},
                       {"action": {"tool": "read_file", "args": {"path": "a.py"}}, "result": {"ok": True}},
                   ],
                   "unresolved_questions": [{"summary": "Edge case offen"}]}}
        summary = run_js(f"const x = RS.summarize({json.dumps(run)}, null); console.log(JSON.stringify({{x, text: RS.summaryText(x)}}));")
        x = summary["x"]
        self.assertEqual(x["label"], "abgeschlossen")
        self.assertEqual(x["changedFiles"], ["a.py", "b.py"])
        self.assertEqual([t["ok"] for t in x["tests"]], [True, False])
        self.assertEqual(x["modelCalls"], 4)
        self.assertEqual(x["toolCalls"], 3)
        self.assertEqual(x["duration"], "1 min 58 s")
        self.assertEqual(x["open"], ["Edge case offen"])
        self.assertEqual(len(x["errors"]), 1)  # the failed test call; no reason for a completed run
        self.assertIn("✗ pytest tests (Exit-Code 1)", summary["text"])
        self.assertIn("Ergebnis: Bug behoben.", summary["text"])

    def test_cancelled_run_without_result_invents_nothing(self):
        run = {"run_id": "r3", "status": "cancelled", "result": None,
               "started_at": "2026-10-22T08:00:00+00:00", "finished_at": "2026-10-22T08:00:30+00:00"}
        x = run_js(f"console.log(JSON.stringify(RS.summarize({json.dumps(run)}, null)));")
        self.assertEqual(x["label"], "abgebrochen")
        self.assertIsNone(x["changedFiles"])
        self.assertIsNone(x["tests"])
        self.assertIsNone(x["modelCalls"])
        self.assertIsNone(x["toolCalls"])
        self.assertEqual(x["duration"], "30 s")
        self.assertEqual(x["errors"], [])
        self.assertEqual(x["open"], ["Run wurde abgebrochen, bevor ein Ergebnis gespeichert wurde."])
        interrupted = run_js(f"console.log(JSON.stringify(RS.summarize({json.dumps({**run, 'status': 'interrupted'})}, null)));")
        self.assertEqual(interrupted["errors"], ["Kein gespeichertes Ergebnis vorhanden."])

    def test_cancelled_run_falls_back_to_replayed_event_counts(self):
        events = [*EVENTS, ("run_completed", {"status": "cancelled", "timestamp": 1010.0})]
        x = self.apply(events, "console.log(JSON.stringify(RS.summarize({run_id: 'r4', status: 'cancelled', result: null}, s)));")
        self.assertEqual(x["modelCalls"], 2)
        self.assertEqual(x["toolCalls"], 1)
        self.assertEqual(x["duration"], "10 s")

    def test_timeout_and_failure_report_reason(self):
        for status, label in (("timed_out", "Timeout"), ("failed", "Fehler")):
            run = {"run_id": "r5", "status": status, "result": {"status": status, "reason": "budget exhausted", "checks": [], "changed_files": []}}
            x = run_js(f"console.log(JSON.stringify(RS.summarize({json.dumps(run)}, null)));")
            self.assertEqual(x["label"], label)
            self.assertEqual(x["errors"], ["budget exhausted"])
            self.assertEqual(x["tests"], [])
            self.assertEqual(x["changedFiles"], [])

    def test_panel_markup_keeps_existing_sections(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        for marker in ("run_status.js", 'id="run-status-grid"', 'id="run-summary-section"', 'id="copy-summary"',
                       "Live-Ereignisse", "Ollama Thinking / Laufzeitlog", "Decision Trace"):
            self.assertIn(marker, html)
        self.assertLess(html.index("run_status.js"), html.index("app.js"))


if __name__ == "__main__":
    unittest.main()

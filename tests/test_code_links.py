# Copyright (c) 2026 Robert Backhaus

"""P07: mapping of file references in run output to editor targets (static/code_links.js)."""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "src" / "reaper_ai" / "static"
NODE = shutil.which("node")
WORKSPACE = "C:\\tools\\src\\proj"
FILES = ["app.py", "src/a.py", "src/b.js"]


def run_js(expression: str):
    """Evaluate an expression against code_links.js in Node and return its JSON value."""
    script = (f"const CL = require({json.dumps(str(STATIC / 'code_links.js'))});\n"
              f"const ctx = {{workspace: {json.dumps(WORKSPACE)}, files: new Set({json.dumps(FILES)})}};\n"
              f"console.log(JSON.stringify({expression}));")
    proc = subprocess.run([NODE, "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=30)
    if proc.returncode:
        raise AssertionError(proc.stderr)
    return json.loads(proc.stdout)


def links(text: str):
    return [part["target"] | {"text": part["text"]}
            for part in run_js(f"CL.findReferences({json.dumps(text)}, ctx)") if "target" in part]


@unittest.skipUnless(NODE, "Node.js is required for the web UI helper tests")
class CodeLinksTests(unittest.TestCase):
    def test_paths_outside_the_workspace_are_rejected(self):
        for raw in ["C:\\other\\x.py", "C:\\tools\\src\\project2\\a.py", "../x.py", "C:\\tools\\src\\proj", ""]:
            with self.subTest(raw=raw):
                self.assertIsNone(run_js(f"CL.projectPath(ctx.workspace, {json.dumps(raw)})"))

    def test_free_text_references_with_lines_and_ranges(self):
        found = links("Fehler in src/a.py:12 und C:\\tools\\src\\proj\\src\\b.js:3-9, siehe app.py")
        self.assertEqual([(item["path"], item["line"], item["endLine"]) for item in found],
                         [("src/a.py", 12, None), ("src/b.js", 3, 9), ("app.py", None, None)])
        self.assertTrue(all(item["known"] for item in found))

    def test_action_target_carries_range_and_patch_text(self):
        read = run_js("CL.actionTarget({tool: 'read_file_range', args: {path: 'src/a.py', start_line: 4, end_line: 9}}, ctx)")
        self.assertEqual((read["path"], read["line"], read["endLine"]), ("src/a.py", 4, 9))
        patch = run_js("CL.actionTarget({tool: 'apply_patch', args: {path: 'new.py', replacement: 'x = 1'}}, ctx)")
        self.assertEqual((patch["path"], patch["known"], patch["highlightText"]), ("new.py", False, "x = 1"))
        self.assertIsNone(run_js("CL.actionTarget({tool: 'shell', args: {command: 'dir'}}, ctx)"))
        self.assertIsNone(run_js("CL.actionTarget({tool: 'read_file', args: {path: 'C:\\\\other\\\\x.py'}}, ctx)"))

    def test_diff_lines_map_to_new_file_lines(self):
        diff = "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1,3 +1,4 @@\n a\n-b\n+c\n+d\n e\n@@ -20,2 +21,2 @@\n-y\n+z\n"
        lines = run_js(f"CL.diffLines({json.dumps(diff)})")
        self.assertEqual(lines["numbers"], [None, None, None, 1, 1, 2, 2, 3, 4, 21, 21, 21])
        self.assertEqual(lines["added"], [2, 3, 21])
        self.assertEqual(lines["first"], 2)


if __name__ == "__main__":
    unittest.main()

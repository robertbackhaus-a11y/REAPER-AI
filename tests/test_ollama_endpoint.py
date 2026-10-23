# Copyright (c) 2026 Robert Backhaus

"""The Ollama endpoint defaults to 127.0.0.1:11434 and follows REAPER_OLLAMA_URL."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
PROBE = ("import json; from reaper_ai import api, engine; "
         "print(json.dumps({'api': api.OLLAMA_BASE, 'engine': engine.OLLAMA_URL}))")


def endpoints(value=None):
    """Import api and engine in a fresh interpreter with an isolated data directory."""
    with tempfile.TemporaryDirectory(dir=Path.cwd()) as data_dir:
        env = {**os.environ, "REAPER_DATA_DIR": data_dir, "PYTHONPATH": str(SRC)}
        env.pop("REAPER_OLLAMA_URL", None)
        if value is not None:
            env["REAPER_OLLAMA_URL"] = value
        proc = subprocess.run([sys.executable, "-c", PROBE], capture_output=True, text=True, env=env, timeout=120)
    if proc.returncode:
        raise AssertionError(proc.stderr)
    return json.loads(proc.stdout.strip().splitlines()[-1])


class OllamaEndpointTests(unittest.TestCase):
    def test_default_endpoint_is_unchanged(self):
        self.assertEqual(endpoints(), {"api": "http://127.0.0.1:11434", "engine": "http://127.0.0.1:11434/api/chat"})
        self.assertEqual(endpoints("  "), endpoints())

    def test_configured_endpoint_is_used_by_api_and_engine(self):
        self.assertEqual(endpoints("http://192.168.1.20:11434/"),
                         {"api": "http://192.168.1.20:11434", "engine": "http://192.168.1.20:11434/api/chat"})


if __name__ == "__main__":
    unittest.main()

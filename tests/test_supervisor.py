# Copyright (c) 2026 Robert Backhaus

import subprocess
import unittest
from unittest.mock import patch

from reaper_ai import supervisor


class PosixSupervisorTests(unittest.TestCase):
    def test_engine_exit_keeps_the_normal_exit_code(self):
        class Process:
            pid = 2468

            def poll(self):
                return 0

            def wait(self, timeout=None):
                return 7

        with patch.object(supervisor.subprocess, "Popen", return_value=Process()) as popen, \
             patch.object(supervisor.os, "killpg", create=True) as killpg, \
             patch.object(supervisor.signal, "signal", return_value=None):
            result = supervisor._supervise_posix("/project", "Inspect", 3)
        self.assertEqual(result, 7)
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        killpg.assert_called_once_with(2468, supervisor.signal.SIGTERM)

    def test_timeout_terminates_the_engine_process_group(self):
        class Process:
            pid = 2468
            finished = False

            def poll(self):
                return 0 if self.finished else None

            def wait(self, timeout=None):
                if timeout == 3:
                    raise subprocess.TimeoutExpired("engine", timeout)
                self.finished = True
                return 0

        proc = Process()
        with patch.object(supervisor.subprocess, "Popen", return_value=proc) as popen, \
             patch.object(supervisor.os, "killpg", create=True) as killpg, \
             patch.object(supervisor.signal, "signal", return_value=None):
            result = supervisor._supervise_posix("/project", "Inspect", 3)
        self.assertEqual(result, 2)
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        killpg.assert_called_once_with(2468, supervisor.signal.SIGTERM)

    def test_cancellation_terminates_the_engine_process_group(self):
        class Process:
            pid = 2468
            finished = False

            def poll(self):
                return 0 if self.finished else None

            def wait(self, timeout=None):
                if timeout == 3:
                    raise KeyboardInterrupt
                self.finished = True
                return 0

        proc = Process()
        with patch.object(supervisor.subprocess, "Popen", return_value=proc), \
             patch.object(supervisor.os, "killpg", create=True) as killpg, \
             patch.object(supervisor.signal, "signal", return_value=None):
            result = supervisor._supervise_posix("/project", "Inspect", 3)
        self.assertEqual(result, 130)
        killpg.assert_called_once_with(2468, supervisor.signal.SIGTERM)

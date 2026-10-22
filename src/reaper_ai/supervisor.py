# Copyright (c) 2026 Robert Backhaus

"""Local process-tree supervisor for REAPER-AI.

Windows uses a Job Object. Linux and macOS run the engine in a dedicated POSIX
session, so timeout and cancellation can terminate that process group. No
service or third-party dependency is required.
"""

import argparse
import ctypes
import os
import signal
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path


def _configure_utf8_stdio() -> None:
    """Keep supervisor diagnostics writable with legacy Windows code pages."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (AttributeError, OSError):
            continue


_configure_utf8_stdio()


if sys.platform == "win32":
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                    ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    class THREADENTRY32(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD),
                    ("cntUsage", wintypes.DWORD),
                    ("th32ThreadID", wintypes.DWORD),
                    ("th32OwnerProcessID", wintypes.DWORD),
                    ("tpBasePri", wintypes.LONG),
                    ("tpDeltaPri", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD)]

    kernel.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    kernel.TerminateJobObject.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Thread32First.argtypes = (wintypes.HANDLE, ctypes.POINTER(THREADENTRY32))
    kernel.Thread32First.restype = wintypes.BOOL
    kernel.Thread32Next.argtypes = (wintypes.HANDLE, ctypes.POINTER(THREADENTRY32))
    kernel.Thread32Next.restype = wintypes.BOOL
    kernel.OpenThread.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenThread.restype = wintypes.HANDLE
    kernel.ResumeThread.argtypes = (wintypes.HANDLE,)
    kernel.ResumeThread.restype = wintypes.DWORD

    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
    TH32CS_SNAPTHREAD = 0x00000004
    THREAD_SUSPEND_RESUME = 0x0002
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


def _check(result, operation):
    if not result:
        raise OSError(ctypes.get_last_error(), operation)
    return result


def _resume_process_threads(pid: int) -> None:
    snapshot = _check(kernel.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0),
                      "CreateToolhelp32Snapshot")
    if snapshot == INVALID_HANDLE_VALUE:
        raise OSError(ctypes.get_last_error(), "CreateToolhelp32Snapshot")
    resumed = 0
    try:
        entry = THREADENTRY32()
        entry.dwSize = ctypes.sizeof(entry)
        found = kernel.Thread32First(snapshot, ctypes.byref(entry))
        while found:
            if entry.th32OwnerProcessID == pid:
                thread = kernel.OpenThread(THREAD_SUSPEND_RESUME, False, entry.th32ThreadID)
                if thread:
                    try:
                        if kernel.ResumeThread(thread) != 0xFFFFFFFF:
                            resumed += 1
                    finally:
                        kernel.CloseHandle(thread)
            found = kernel.Thread32Next(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    if resumed == 0:
        raise RuntimeError("Could not resume suspended engine process")


def _engine_command(workspace: str, task: str) -> list[str]:
    return [sys.executable, "-m", "reaper_ai.engine", "--workspace", workspace, "--task", task]


def _supervise_windows(workspace: str, task: str, timeout: int) -> int:
    job = _check(kernel.CreateJobObjectW(None, None), "CreateJobObject")
    proc = None
    try:
        limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        _check(kernel.SetInformationJobObject(
            job, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
            ctypes.byref(limits), ctypes.sizeof(limits)), "SetInformationJobObject")
        proc = subprocess.Popen(
            _engine_command(workspace, task),
            cwd=str(Path.cwd()), creationflags=0x00000004,
        )
        # Assign before the process executes a single instruction, avoiding
        # child-process creation outside the job.
        _check(kernel.AssignProcessToJobObject(job, wintypes.HANDLE(proc._handle)),
               "AssignProcessToJobObject")
        _resume_process_threads(proc.pid)
        try:
            rc = proc.wait(timeout=timeout)
            print(f"ENGINE_EXIT: {rc}", flush=True)
            return rc
        except subprocess.TimeoutExpired:
            print(f"ENGINE_TIMEOUT: {timeout}s; terminating process tree", flush=True)
            return 2
        except KeyboardInterrupt:
            print("ENGINE_CANCELLED: terminating process tree", flush=True)
            return 130
    finally:
        # KILL_ON_JOB_CLOSE covers engine and descendants on timeout, CTRL+C,
        # unexpected exceptions and even supervisor-process termination.
        kernel.CloseHandle(job)
        if proc is not None:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


def _terminate_posix_group(proc: subprocess.Popen) -> None:
    """Terminate an engine session, escalating only when its group survives."""
    engine_running = proc.poll() is None
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    if not engine_running:
        return
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        proc.wait()


def _supervise_posix(workspace: str, task: str, timeout: int) -> int:
    proc = None
    previous_handlers: dict[int, object] = {}

    def _cancel_signal(_signum, _frame) -> None:
        raise KeyboardInterrupt

    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous_handlers[signum] = signal.signal(signum, _cancel_signal)
        proc = subprocess.Popen(_engine_command(workspace, task), cwd=str(Path.cwd()), start_new_session=True)
        try:
            rc = proc.wait(timeout=timeout)
            print(f"ENGINE_EXIT: {rc}", flush=True)
            return rc
        except subprocess.TimeoutExpired:
            print(f"ENGINE_TIMEOUT: {timeout}s; terminating process group", flush=True)
            _terminate_posix_group(proc)
            proc = None
            return 2
        except KeyboardInterrupt:
            print("ENGINE_CANCELLED: terminating process group", flush=True)
            _terminate_posix_group(proc)
            proc = None
            return 130
    finally:
        if proc is not None:
            _terminate_posix_group(proc)
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


def supervise(workspace: str, task: str, timeout: int) -> int:
    if timeout <= 0:
        raise ValueError("Timeout must be positive")
    if sys.platform == "win32":
        return _supervise_windows(workspace, task, timeout)
    return _supervise_posix(workspace, task, timeout)


def main() -> None:
    parser = argparse.ArgumentParser(description="REAPER engine supervisor")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--timeout", type=int, default=660)
    args = parser.parse_args()
    try:
        raise SystemExit(supervise(args.workspace, args.task, args.timeout))
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"SUPERVISOR_ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()


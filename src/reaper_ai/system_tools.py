# Copyright (c) 2026 Robert Backhaus

"""System-level tools for REAPER-AI: shell execution and Python discovery."""

import subprocess
import os
import sys
import shutil
from pathlib import Path
from typing import Optional


def run_shell_command(
    command: str,
    cwd: Optional[str] = None,
    timeout: int = 60,
    env_override: Optional[dict] = None
) -> dict:
    """
    Execute a shell command (PowerShell, CMD, or CLI tool).
    
    Args:
        command: The command string to execute.
        cwd: Working directory. Defaults to current working directory.
        timeout: Timeout in seconds.
        env_override: Optional environment variables to override/add.
        
    Returns:
        Dict with 'ok', 'stdout', 'stderr', 'returncode'.
    """
    try:
        # Determine shell based on OS
        if os.name == 'nt':
            shell_cmd = ["powershell", "-Command", command]
        else:
            shell_cmd = ["/bin/sh", "-c", command]
            
        # Prepare environment
        env = os.environ.copy()
        if env_override:
            env.update(env_override)
            
        result = subprocess.run(
            shell_cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env
        )
        
        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "returncode": -1,
            "stdout": "",
            "stderr": f"Command timed out after {timeout} seconds",
        }
    except Exception as exc:
        return {
            "ok": False,
            "returncode": -1,
            "stdout": "",
            "stderr": str(exc),
        }


def find_python_executables() -> list[str]:
    """Return the active interpreter and Python launchers resolvable on PATH."""
    candidates = {str(Path(sys.executable).resolve())}
    names = ("python.exe", "python", "py.exe", "py") if os.name == "nt" else ("python3", "python")
    for name in names:
        if executable := shutil.which(name):
            candidates.add(str(Path(executable).resolve()))
    return sorted(candidates, key=str.casefold)

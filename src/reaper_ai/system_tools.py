"""System-level tools for REAPER-AI: Shell execution, Python env management, and global file access."""

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


def find_python_executables() -> list:
    """
    Find available Python executables on the system.
    
    Returns:
        List of paths to Python executables.
    """
    candidates = []
    
    # Check common locations
    if os.name == 'nt':
        common_paths = [
            "C:\\Python39\\python.exe",
            "C:\\Python310\\python.exe",
            "C:\\Python311\\python.exe",
            "C:\\Python312\\python.exe",
            "C:\\Program Files\\Python39\\python.exe",
            "C:\\Program Files\\Python310\\python.exe",
            "C:\\Program Files\\Python311\\python.exe",
            "C:\\Program Files\\Python312\\python.exe",
        ]
    else:
        common_paths = [
            "/usr/bin/python3",
            "/usr/bin/python",
            "/usr/local/bin/python3",
            "/usr/local/bin/python",
        ]
        
    # Also check PATH
    path_dirs = os.environ.get("PATH", "").split(os.pathsep)
    for d in path_dirs:
        p = Path(d) / ("python.exe" if os.name == 'nt' else "python3")
        if p.exists():
            candidates.append(str(p.resolve()))
            
    # Add common paths if not already in PATH
    for p in common_paths:
        if Path(p).exists() and str(Path(p).resolve()) not in candidates:
            candidates.append(str(Path(p).resolve()))
            
    return list(set(candidates))


def create_python_env(
    env_path: str,
    python_executable: Optional[str] = None,
    packages: Optional[list] = None
) -> dict:
    """
    Create a Python virtual environment and optionally install packages.
    
    Args:
        env_path: Path to create the virtual environment.
        python_executable: Path to Python executable. If None, uses current interpreter.
        packages: List of packages to install after creation.
        
    Returns:
        Dict with 'ok', 'env_path', 'installed_packages'.
    """
    try:
        # Determine Python executable
        if python_executable is None:
            python_executable = sys.executable
            
        if not Path(python_executable).exists():
            return {
                "ok": False,
                "error": f"Python executable not found: {python_executable}",
            }
            
        # Create virtual environment
        result = subprocess.run(
            [python_executable, "-m", "venv", env_path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60
        )
        
        if result.returncode != 0:
            return {
                "ok": False,
                "error": f"Failed to create venv: {result.stderr}",
            }
            
        # Determine pip executable
        if os.name == 'nt':
            pip_executable = Path(env_path) / "Scripts" / "pip.exe"
        else:
            pip_executable = Path(env_path) / "bin" / "pip"
            
        installed_packages = []
        
        # Install packages if specified
        if packages:
            for package in packages:
                install_result = subprocess.run(
                    [str(pip_executable), "install", package],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=120
                )
                if install_result.returncode == 0:
                    installed_packages.append(package)
                else:
                    return {
                        "ok": False,
                        "error": f"Failed to install {package}: {install_result.stderr}",
                        "installed_packages": installed_packages,
                    }
                    
        return {
            "ok": True,
            "env_path": str(Path(env_path).resolve()),
            "installed_packages": installed_packages,
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
        }
# File reads and writes are centralized in reaper_ai.tools.

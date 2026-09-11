#!/usr/bin/env python3
"""Run both the PDF to LaTeX FastAPI backend and the React frontend."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
VENV_DIR = PROJECT_ROOT / ".venv"
BACKEND_DIR = PROJECT_ROOT / "backend"
FRONTEND_DIR = PROJECT_ROOT / "frontend"


def get_venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def is_usable_python(interpreter: Path) -> bool:
    """Return whether an environment interpreter can start successfully."""
    if not interpreter.is_file():
        return False
    try:
        completed = subprocess.run(
            [str(interpreter), "-c", "import ensurepip, sys"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def stream_output(process: subprocess.Popen, prefix: str):
    """Stream subprocess stdout/stderr with a colored or tagged prefix."""
    try:
        for line in iter(process.stdout.readline, ""):
            if not line:
                break
            clean_line = line.rstrip("\r\n")
            if clean_line:
                print(f"[{prefix}] {clean_line}", flush=True)
    except Exception:
        pass


def main():
    venv_py = get_venv_python()
    if not is_usable_python(venv_py):
        print(f"Error: Virtual environment is missing or unusable at {VENV_DIR}", file=sys.stderr)
        print("Please run setup.bat (Windows) or ./setup.sh (Linux/macOS) first.", file=sys.stderr)
        sys.exit(1)

    # If not running inside .venv, re-enter with .venv python
    if Path(sys.executable).resolve() != venv_py.resolve():
        cmd = [str(venv_py), str(Path(__file__).resolve())] + sys.argv[1:]
        sys.exit(subprocess.call(cmd))

    print("=" * 60)
    print("           Starting PDF -> LaTeX Service")
    print("=" * 60)
    print(f"Project root: {PROJECT_ROOT}")
    print("Backend:      http://localhost:8000 (API Docs: http://localhost:8000/docs)")
    print("Frontend UI:  http://localhost:5173")
    print("=" * 60)
    print("Press Ctrl+C to stop both services.\n")

    processes: list[subprocess.Popen] = []

    try:
        # 1. Start FastAPI backend
        backend_cmd = [
            str(venv_py),
            "-m",
            "uvicorn",
            "backend.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
            "--log-level",
            "info",
        ]
        backend_proc = subprocess.Popen(
            backend_cmd,
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        processes.append(backend_proc)
        threading.Thread(
            target=stream_output, args=(backend_proc, "backend"), daemon=True
        ).start()

        # 2. Start Frontend (if directory exists)
        if FRONTEND_DIR.exists():
            npm_cmd = "npm.cmd" if os.name == "nt" else "npm"
            frontend_cmd = [npm_cmd, "run", "dev"]
            frontend_proc = subprocess.Popen(
                frontend_cmd,
                cwd=str(FRONTEND_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            processes.append(frontend_proc)
            threading.Thread(
                target=stream_output, args=(frontend_proc, "frontend"), daemon=True
            ).start()
        else:
            print("[warning] Frontend directory not found; running backend only.")

        # Keep supervisor alive and monitor child processes
        while True:
            for p in processes:
                if p.poll() is not None:
                    print(f"\nA service process exited with code {p.returncode}. Shutting down.")
                    return
            time.sleep(0.5)

    except KeyboardInterrupt:
        print("\nStopping services...")
    finally:
        for p in processes:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    p.kill()
        print("All services stopped.")


if __name__ == "__main__":
    main()

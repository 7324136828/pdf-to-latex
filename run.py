#!/usr/bin/env python3
"""Run both the PDF to LaTeX FastAPI backend and the React frontend."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from backend.setup_tools.environment import REQUIRED_PYTHON

PROJECT_ROOT = Path(__file__).resolve().parent
VENV_DIR = PROJECT_ROOT / ".venv"
BACKEND_DIR = PROJECT_ROOT / "backend"
FRONTEND_DIR = PROJECT_ROOT / "frontend"
REQUIRED_PYTHON_TEXT = ".".join(str(part) for part in REQUIRED_PYTHON)


def get_local_venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def in_active_environment() -> bool:
    return (
        sys.prefix != getattr(sys, "base_prefix", sys.prefix)
        or bool(os.environ.get("VIRTUAL_ENV"))
        or bool(os.environ.get("CONDA_PREFIX"))
    )


def get_runtime_python() -> Path:
    if in_active_environment():
        return Path(sys.executable)
    return get_local_venv_python()


def requested_port(variable: str, default: int) -> int:
    value = os.environ.get(variable, str(default))
    try:
        port = int(value)
    except ValueError as error:
        raise RuntimeError(f"{variable} must be an integer, not {value!r}") from error
    if not 1 <= port <= 65535:
        raise RuntimeError(f"{variable} must be between 1 and 65535")
    return port


def available_port(start: int, reserved: set[int] | None = None) -> int:
    reserved = reserved or set()
    for port in range(start, 65536):
        if port in reserved:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
        return port
    raise RuntimeError(f"No available TCP port was found at or above {start}")


def is_usable_python(interpreter: Path) -> bool:
    """Return whether the environment uses the required working Python."""
    if not interpreter.is_file():
        return False
    try:
        completed = subprocess.run(
            [
                str(interpreter),
                "-c",
                "import ensurepip, sys; "
                f"raise SystemExit(sys.version_info[:3] != {REQUIRED_PYTHON!r})",
            ],
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
    runtime_py = get_runtime_python()
    if not is_usable_python(runtime_py):
        print(
            f"Error: the selected Python environment is missing, unusable, or is "
            f"not Python {REQUIRED_PYTHON_TEXT}: {runtime_py}",
            file=sys.stderr,
        )
        print("Please run setup.bat (Windows) or ./setup.sh (Linux/macOS) first.", file=sys.stderr)
        sys.exit(1)

    if Path(sys.executable).resolve() != runtime_py.resolve():
        cmd = [str(runtime_py), str(Path(__file__).resolve())] + sys.argv[1:]
        sys.exit(subprocess.call(cmd))

    backend_port = available_port(requested_port("BACKEND_PORT", 8000))
    frontend_port = available_port(
        requested_port("FRONTEND_PORT", 5173), {backend_port}
    )
    backend_url = f"http://127.0.0.1:{backend_port}"
    frontend_url = f"http://localhost:{frontend_port}"

    print("=" * 60)
    print("           Starting PDF -> LaTeX Service")
    print("=" * 60)
    print(f"Project root: {PROJECT_ROOT}")
    print(f"Python:       {sys.executable}")
    print(f"Backend:      {backend_url} (API Docs: {backend_url}/docs)")
    print(f"Frontend UI:  {frontend_url}")
    print("=" * 60)
    print("Press Ctrl+C to stop both services.\n")

    processes: list[subprocess.Popen] = []

    try:
        # 1. Start FastAPI backend
        backend_cmd = [
            str(runtime_py),
            "-m",
            "uvicorn",
            "backend.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(backend_port),
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
            frontend_cmd = [
                npm_cmd,
                "run",
                "dev",
                "--",
                "--port",
                str(frontend_port),
                "--strictPort",
            ]
            frontend_env = os.environ.copy()
            frontend_env["VITE_BACKEND_URL"] = backend_url
            frontend_proc = subprocess.Popen(
                frontend_cmd,
                cwd=str(FRONTEND_DIR),
                env=frontend_env,
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

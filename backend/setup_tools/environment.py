"""Interpreter checks, the project-local virtual environment, and re-entry.

``setup.py`` is started by whatever Python the user has on PATH.  Everything
after the first two steps has to happen inside ``.venv`` instead, so this module
creates that environment and re-runs the same script with its interpreter.  The
user never has to activate anything: every later command names the interpreter
explicitly.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

#: Pix2Text's ONNX runtime publishes wheels for these; torch is happy here too.
MINIMUM_PYTHON = (3, 11)
MAXIMUM_PYTHON = (3, 14)          # exclusive

#: Set on the child process so a failure to detect ``.venv`` cannot loop.
REENTRY_MARKER = "PDFCONV_SETUP_REENTERED"


class SetupError(RuntimeError):
    """A setup step cannot continue."""


def version_text(info=None) -> str:
    info = info or sys.version_info
    return f"{info.major}.{info.minor}.{info.micro}"


def check_python_version(info=None) -> str:
    """-> a description of this interpreter, or raise if it is unsupported."""
    info = info or sys.version_info
    current = (info.major, info.minor)
    if current < MINIMUM_PYTHON or current >= MAXIMUM_PYTHON:
        raise SetupError(
            f"Python {version_text(info)} is not supported. This project needs "
            f"Python {MINIMUM_PYTHON[0]}.{MINIMUM_PYTHON[1]} up to "
            f"{MAXIMUM_PYTHON[0]}.{MAXIMUM_PYTHON[1] - 1}: the OCR runtime "
            "publishes wheels only for those versions.\n"
            f"Interpreter: {sys.executable}")
    return f"Python {version_text(info)} at {sys.executable}"


def venv_python(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def running_inside(venv_dir: Path) -> bool:
    """-> True when this interpreter *is* the one in ``venv_dir``.

    Comparing ``sys.prefix`` is what makes this reliable: it holds regardless of
    whether the environment was activated or the interpreter was named directly.
    """
    try:
        return Path(sys.prefix).resolve() == venv_dir.resolve()
    except OSError:
        return False


def is_usable(venv_dir: Path) -> bool:
    """-> True when ``venv_dir`` holds an interpreter that actually starts.

    A previous run interrupted midway leaves a directory that looks like an
    environment and is not one; that has to be recreated rather than used.
    """
    interpreter = venv_python(venv_dir)
    if not interpreter.is_file():
        return False
    try:
        completed = subprocess.run([str(interpreter), "-c", "import sys, ensurepip"],
                                   capture_output=True, text=True, timeout=120,
                                   check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def remove_venv(venv_dir: Path) -> None:
    if venv_dir.exists():
        shutil.rmtree(venv_dir)


def ensure_venv(venv_dir: Path, *, recreate: bool = False) -> tuple[Path, str]:
    """-> (interpreter, what happened).  Creates ``.venv`` only when needed."""
    if recreate and venv_dir.exists():
        remove_venv(venv_dir)
        action = "recreated"
    elif venv_dir.exists() and not is_usable(venv_dir):
        remove_venv(venv_dir)
        action = "replaced (the existing one was incomplete)"
    elif venv_dir.exists():
        return venv_python(venv_dir), "reused"
    else:
        action = "created"

    builder = venv.EnvBuilder(with_pip=True, upgrade_deps=False, clear=False,
                              symlinks=os.name != "nt")
    try:
        builder.create(str(venv_dir))
    except (OSError, subprocess.SubprocessError) as error:
        raise SetupError(f"Could not create the virtual environment at "
                         f"{venv_dir}: {error}") from error
    interpreter = venv_python(venv_dir)
    if not interpreter.is_file():
        raise SetupError(f"The virtual environment was created but has no "
                         f"interpreter at {interpreter}")
    return interpreter, action


def reenter(interpreter: Path, script: Path, argv: list[str]) -> int:
    """Re-run ``script`` with the environment's interpreter and pass on its status."""
    if os.environ.get(REENTRY_MARKER):
        raise SetupError(
            f"Re-entered setup is still not running inside the virtual "
            f"environment (interpreter: {sys.executable}). Delete "
            f"{interpreter.parent.parent} and run setup again.")
    environment = dict(os.environ, **{REENTRY_MARKER: "1"})
    command = [str(interpreter), str(script), *argv]
    completed = subprocess.run(command, env=environment, check=False)
    return completed.returncode

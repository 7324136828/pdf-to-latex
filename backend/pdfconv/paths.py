"""Canonical filesystem locations, resolved from this file rather than the CWD.

Every path the project touches is derived here so no module carries an absolute
path of its own and nothing depends on the directory a command was started in.
"""

from __future__ import annotations

import os
from pathlib import Path

# .../<project>/python/pdfconv/paths.py -> <project>
PACKAGE_ROOT = Path(__file__).resolve().parent
PYTHON_ROOT = PACKAGE_ROOT.parent
PROJECT_ROOT = PYTHON_ROOT.parent

VENV_DIR = PROJECT_ROOT / ".venv"
INPUT_DIR = PROJECT_ROOT / "input"
OUTPUT_DIR = PROJECT_ROOT / "output"
#: Page caches, checkpoints and other recoverable intermediates.
WORK_DIR = PROJECT_ROOT / "output_tmp"
#: Downloaded OCR model weights and their prepared caches.
MODEL_DIR = PROJECT_ROOT / ".models"

REQUIREMENTS = PYTHON_ROOT / "requirements.txt"
REQUIREMENTS_MATH = PYTHON_ROOT / "requirements-math.txt"


def venv_python(venv_dir: Path = VENV_DIR) -> Path:
    """-> the interpreter inside a virtual environment, on either platform."""
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def relative_to_root(path: Path) -> str:
    """-> ``path`` shown relative to the project when it lies inside it."""
    path = Path(path)
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)

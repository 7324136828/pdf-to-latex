"""pip operations, all of them aimed at the virtual environment.

Every command here runs as ``sys.executable -m pip``, and every caller is
already re-entered into ``.venv``, so nothing this module does can reach the
system Python's site-packages.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, requires, version
from pathlib import Path

from .environment import SetupError

#: Long enough for a torch wheel on a slow connection.
PIP_TIMEOUT = 3600.0


def pip(*arguments: str, capture: bool = False,
        timeout: float = PIP_TIMEOUT) -> subprocess.CompletedProcess:
    """Run one pip command inside this interpreter's environment."""
    command = [sys.executable, "-m", "pip", *arguments]
    try:
        return subprocess.run(command, check=True, timeout=timeout, text=True,
                              capture_output=capture)
    except subprocess.CalledProcessError as error:
        output = "\n".join(part for part in (error.stdout, error.stderr) if part)
        raise SetupError(f"pip {' '.join(arguments)} failed "
                         f"(exit {error.returncode}){chr(10) + output if output else ''}"
                         ) from error
    except subprocess.TimeoutExpired as error:
        raise SetupError(f"pip {' '.join(arguments)} timed out after "
                         f"{timeout:.0f}s") from error
    except OSError as error:
        raise SetupError(f"pip could not be started: {error}") from error


#: "setuptools", optionally followed by a version specifier and nothing else.
SETUPTOOLS_REQUIREMENT = re.compile(r"^setuptools\s*(?:[<>=!~][^A-Za-z]*)?$")


def setuptools_requirement() -> str:
    """-> "setuptools" with whatever bound the installed torch puts on it.

    PyTorch caps setuptools (``setuptools<82`` for 2.11). Upgrading to the
    newest release regardless would make pip report a conflict on every run and
    the next dependency install undo it again, so the cap is read back from the
    distribution that sets it rather than written down here.
    """
    try:
        for requirement in requires("torch") or []:
            head = requirement.split(";")[0].strip()
            if SETUPTOOLS_REQUIREMENT.match(head):
                return head
    except Exception:                       # torch is not installed yet
        pass
    return "setuptools"


def upgrade_packaging_tools() -> None:
    """Bring pip, setuptools and wheel up to date inside the environment."""
    pip("install", "--upgrade", "pip", setuptools_requirement(), "wheel")


def installed_version(package: str) -> str | None:
    try:
        return version(package)
    except PackageNotFoundError:
        return None
    except Exception:                    # a half-removed distribution
        return None


def install_requirements(requirements: Path, *, constraints: Path | None = None,
                         upgrade: bool = False) -> None:
    """Install one requirements file, refusing to disturb constrained packages."""
    if not requirements.is_file():
        raise SetupError(f"Requirements file not found: {requirements}")
    arguments = ["install", "-r", str(requirements)]
    if constraints is not None:
        arguments += ["--constraint", str(constraints)]
    if upgrade:
        arguments.append("--upgrade")
    pip(*arguments)


def write_constraints(path: Path, pins: dict[str, str]) -> Path:
    """Write a pip constraints file that freezes the packages in ``pins``.

    This is the guard that keeps a later ``-r requirements.txt`` from pulling a
    plain PyPI torch over the CUDA build: pip refuses the resolution instead of
    silently replacing the wheels.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(f"{name}=={pin}\n" for name, pin in pins.items())
    path.write_text("# Written by setup.py; do not edit.\n" + body, encoding="utf-8")
    return path


#: The two ONNX Runtime distributions install the same ``onnxruntime`` package,
#: so exactly one of them may be present. Which one decides whether the OCR
#: fallback can use the GPU.
ONNXRUNTIME_CPU = "onnxruntime"
ONNXRUNTIME_CUDA = "onnxruntime-gpu"


def select_onnxruntime(requirements: Path, *, cuda: bool,
                       constraints: Path | None = None) -> str:
    """Install the ONNX Runtime build for this hardware; -> what was done.

    Both distributions own the same ``onnxruntime`` package directory, so only
    one may be present -- and Pix2Text asks for ``cnocr[ort-cpu]``, which means
    a plain ``pip install -r requirements-math.txt`` drags the CPU build back in
    on every run. Removing it then deletes files the GPU build still needs while
    leaving the GPU build's metadata in place, so pip would consider it
    installed and skip the repair; the reinstall below is what closes that.
    """
    wanted, unwanted = ((ONNXRUNTIME_CUDA, ONNXRUNTIME_CPU) if cuda
                        else (ONNXRUNTIME_CPU, ONNXRUNTIME_CUDA))
    notes = []
    repair = installed_version(unwanted) is not None
    if repair:
        pip("uninstall", "-y", unwanted)
        notes.append(f"removed {unwanted}")
    arguments = ["install", "-r", str(requirements)]
    if constraints is not None:
        arguments += ["--constraint", str(constraints)]
    if repair:
        arguments += ["--force-reinstall", "--no-deps"]
    pip(*arguments)
    notes.append(f"{wanted} {installed_version(wanted) or 'not installed'}")
    return "; ".join(notes)


def import_check(module: str) -> str | None:
    """-> None when ``module`` imports in this environment, else the error."""
    completed = subprocess.run([sys.executable, "-c", f"import {module}"],
                               capture_output=True, text=True, check=False)
    if completed.returncode == 0:
        return None
    lines = (completed.stderr or completed.stdout or "").strip().splitlines()
    return lines[-1] if lines else f"exit status {completed.returncode}"


#: External programs the converters can use.  Neither is required: PyMuPDF
#: renders pages itself, and the LaTeX output is a deliverable, not something
#: this project compiles.
EXTERNAL_TOOLS = {
    "xelatex": "compile the generated .tex files (optional; install MiKTeX or TeX Live)",
    "nvidia-smi": "report NVIDIA GPUs (installed with the NVIDIA driver)",
}


def check_external_tools() -> list[tuple[str, str | None, str]]:
    """-> (name, resolved path or None, why it matters) for each optional tool."""
    return [(name, shutil.which(name), why) for name, why in EXTERNAL_TOOLS.items()]

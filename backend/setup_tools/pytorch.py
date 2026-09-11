"""The one place PyTorch is installed.

Nothing else in the project may name torch, torchvision or torchaudio as a
dependency: the version an RTX machine needs carries a ``+cu128`` local
version that plain PyPI does not publish, and a later ``pip install -r`` that
mentions ``torch`` would quietly pull the CPU build over it.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass

from .dependencies import PIP_TIMEOUT, installed_version, pip
from .environment import SetupError

#: Exactly the build an NVIDIA GeForce RTX card needs here.
CUDA_TAG = "cu128"
CUDA_INDEX = f"https://download.pytorch.org/whl/{CUDA_TAG}"
CUDA_VERSIONS = {
    "torch": f"2.11.0+{CUDA_TAG}",
    "torchvision": f"0.26.0+{CUDA_TAG}",
    "torchaudio": f"2.11.0+{CUDA_TAG}",
}

#: The same releases without the CUDA build, for machines that cannot use it.
CPU_VERSIONS = {
    "torch": "2.11.0",
    "torchvision": "0.26.0",
    "torchaudio": "2.11.0",
}

PACKAGES = tuple(CUDA_VERSIONS)

#: What ``torch.version.cuda`` should say once the CUDA build is in place.
EXPECTED_CUDA_RUNTIME = "12.8"


@dataclass
class TorchReport:
    """The verification result, gathered by running inside the environment."""

    ok: bool
    lines: list[str]
    cuda_available: bool = False
    problems: list[str] | None = None


def wanted_versions(cuda: bool) -> dict[str, str]:
    return dict(CUDA_VERSIONS if cuda else CPU_VERSIONS)


def current_versions() -> dict[str, str | None]:
    return {name: installed_version(name) for name in PACKAGES}


def already_correct(wanted: dict[str, str]) -> bool:
    """-> True when all three packages are installed at exactly these versions."""
    current = current_versions()
    return all(current[name] == pin for name, pin in wanted.items())


def install(wanted: dict[str, str], *, cuda: bool, force: bool = False) -> str:
    """Put exactly ``wanted`` in place; -> a one-line description of the action.

    The three packages are replaced together.  Mixing a new torch with an old
    torchvision produces an import error at the first model load rather than
    here, which is a much worse place to discover it.
    """
    if not force and already_correct(wanted):
        return "already installed at the required versions; nothing to do"

    current = current_versions()
    if any(current[name] is not None for name in PACKAGES):
        pip("uninstall", "-y", *PACKAGES)

    specifications = [f"{name}=={pin}" for name, pin in wanted.items()]
    arguments = ["install", *specifications]
    if cuda:
        # --index-url, not --extra-index-url: the CUDA build must not be
        # resolvable from PyPI, where the same version number means CPU wheels.
        arguments += ["--index-url", CUDA_INDEX]
    pip(*arguments)
    return "installed " + ", ".join(specifications)


#: Run inside the environment so the verification sees the real interpreter.
_PROBE = r"""
import json
report = {}
try:
    import torch, torchvision, torchaudio
except Exception as error:
    print(json.dumps({"import_error": f"{type(error).__name__}: {error}"}))
    raise SystemExit(0)
report["torch"] = torch.__version__
report["torchvision"] = torchvision.__version__
report["torchaudio"] = torchaudio.__version__
report["cuda_runtime"] = torch.version.cuda
try:
    report["cuda_available"] = bool(torch.cuda.is_available())
    report["devices"] = [torch.cuda.get_device_name(i)
                         for i in range(torch.cuda.device_count())]
except Exception as error:
    report["cuda_available"] = False
    report["cuda_error"] = f"{type(error).__name__}: {error}"
print(json.dumps(report))
"""

#: A single small tensor is enough to prove the GPU is reachable, and it costs
#: milliseconds; nothing here loads a model or reads a PDF.
_SMOKE_TEST = r"""
import json
import torch
try:
    with torch.inference_mode():
        a = torch.randn(64, 64, device="cuda")
        b = (a @ a).sum().item()
    print(json.dumps({"ok": True, "value": b,
                      "device": torch.cuda.get_device_name(0)}))
except Exception as error:
    print(json.dumps({"ok": False, "error": f"{type(error).__name__}: {error}"}))
"""


def _run_probe(source: str) -> dict:
    try:
        completed = subprocess.run([sys.executable, "-c", source], text=True,
                                   capture_output=True, timeout=PIP_TIMEOUT,
                                   check=False)
    except (OSError, subprocess.SubprocessError) as error:
        raise SetupError(f"Could not run the PyTorch check: {error}") from error
    if completed.returncode != 0:
        raise SetupError("The PyTorch check failed:\n"
                         + (completed.stderr or completed.stdout).strip())
    output = completed.stdout.strip().splitlines()
    if not output:
        raise SetupError("The PyTorch check produced no output.")
    try:
        return json.loads(output[-1])
    except ValueError as error:
        raise SetupError(f"Unreadable PyTorch check output: {output[-1]}") from error


def verify(wanted: dict[str, str], *, cuda: bool, smoke_test: bool = True) -> TorchReport:
    """Check the installed build against what was asked for.

    An RTX machine that ends up without CUDA is a failure, not a note: the OCR
    fallback would still run, just slowly enough to look like a hang.
    """
    report = _run_probe(_PROBE)
    if "import_error" in report:
        return TorchReport(False, [f"PyTorch is not importable: {report['import_error']}"],
                           problems=[report["import_error"]])

    lines = [f"PyTorch version: {report['torch']}",
             f"Torchvision version: {report['torchvision']}",
             f"Torchaudio version: {report['torchaudio']}",
             f"PyTorch CUDA version: {report.get('cuda_runtime') or 'none (CPU build)'}",
             f"CUDA available: {report.get('cuda_available')}"]
    for name in report.get("devices", []):
        lines.append(f"CUDA device: {name}")

    problems = []
    for package in PACKAGES:
        if report[package] != wanted[package]:
            problems.append(f"{package} is {report[package]}, expected {wanted[package]}")
    if cuda:
        if not report.get("cuda_available"):
            detail = report.get("cuda_error", "torch.cuda.is_available() is False")
            problems.append(
                "an NVIDIA GeForce RTX GPU was detected but this PyTorch cannot use "
                f"CUDA ({detail}); check the NVIDIA driver version")
        runtime = report.get("cuda_runtime") or ""
        if not runtime.startswith(EXPECTED_CUDA_RUNTIME):
            problems.append(f"torch reports CUDA runtime {runtime or 'none'}, "
                            f"expected {EXPECTED_CUDA_RUNTIME}")
        if not problems and smoke_test:
            smoke = _run_probe(_SMOKE_TEST)
            if smoke.get("ok"):
                lines.append(f"CUDA matrix multiply: OK on {smoke['device']}")
            else:
                problems.append(f"a CUDA computation failed: {smoke.get('error')}")

    return TorchReport(not problems, lines,
                       cuda_available=bool(report.get("cuda_available")),
                       problems=problems)

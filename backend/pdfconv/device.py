"""GPU detection and compute-device selection.

Detection has to work *before* PyTorch exists, because the setup program uses
it to decide which PyTorch to install.  ``nvidia-smi`` is therefore the
authority for "is there an NVIDIA GPU here", and torch is consulted only
afterwards, to confirm that the installed build can actually reach it.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass, field

#: A driver query is cheap, but it still spawns a process; give it a bound.
NVIDIA_SMI_TIMEOUT = 20.0

#: The marker that selects the CUDA build.  Matched case-insensitively.
RTX_MARKER = "nvidia geforce rtx"


@dataclass(frozen=True)
class GpuInfo:
    """What ``nvidia-smi`` reported, or why it reported nothing."""

    names: tuple[str, ...] = ()
    detail: str = ""

    @property
    def available(self) -> bool:
        return bool(self.names)

    @property
    def rtx_names(self) -> tuple[str, ...]:
        return tuple(n for n in self.names if RTX_MARKER in n.casefold())

    @property
    def has_rtx(self) -> bool:
        return bool(self.rtx_names)

    def describe(self) -> str:
        if not self.names:
            return self.detail or "no NVIDIA GPU detected"
        return ", ".join(self.names)


def detect_nvidia_gpus() -> GpuInfo:
    """-> every GPU name ``nvidia-smi`` reports, tolerating every failure mode.

    A missing tool, a missing driver, a non-zero exit and an empty GPU list are
    all ordinary answers on a machine without an NVIDIA card, so none of them
    is allowed to raise.
    """
    executable = shutil.which("nvidia-smi")
    if executable is None:
        return GpuInfo(detail="nvidia-smi not found on PATH")
    command = [executable, "--query-gpu=name", "--format=csv,noheader"]
    try:
        completed = subprocess.run(command, capture_output=True, text=True,
                                   timeout=NVIDIA_SMI_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        return GpuInfo(detail=f"nvidia-smi could not be run: {error}")
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "").strip().splitlines()
        reason = message[0] if message else f"exit status {completed.returncode}"
        return GpuInfo(detail=f"nvidia-smi failed: {reason}")
    names = tuple(line.strip() for line in completed.stdout.splitlines() if line.strip())
    if not names:
        return GpuInfo(detail="nvidia-smi reported no GPUs")
    return GpuInfo(names=names)


@dataclass
class TorchStatus:
    """What the installed PyTorch says about itself, if it is installed."""

    installed: bool = False
    version: str = ""
    cuda_version: str | None = None
    cuda_available: bool = False
    device_names: tuple[str, ...] = ()
    error: str = ""


def probe_torch() -> TorchStatus:
    """-> the installed torch's own view of CUDA, without importing it twice."""
    try:
        import torch
    except Exception as error:            # a broken install must not be fatal
        return TorchStatus(error=f"{type(error).__name__}: {error}")
    status = TorchStatus(installed=True, version=torch.__version__,
                         cuda_version=torch.version.cuda)
    try:
        status.cuda_available = bool(torch.cuda.is_available())
        if status.cuda_available:
            status.device_names = tuple(torch.cuda.get_device_name(i)
                                        for i in range(torch.cuda.device_count()))
    except Exception as error:
        status.error = f"{type(error).__name__}: {error}"
    return status


def onnx_cuda_provider() -> tuple[bool, str]:
    """-> (CUDAExecutionProvider present, what ONNX Runtime reports).

    Torch is imported first on purpose: the CUDA provider's library depends on
    cuBLAS and cuDNN, which the CUDA torch wheels ship and ONNX Runtime does
    not. See pdfconv.pdf2tex.math_ocr.preload_cuda_runtime.
    """
    try:
        import torch                                     # noqa: F401
    except Exception:
        pass
    try:
        import onnxruntime as ort
    except Exception as error:
        return False, f"onnxruntime is not importable ({type(error).__name__}: {error})"
    providers = ort.get_available_providers()
    return "CUDAExecutionProvider" in providers, ", ".join(providers)


@dataclass
class DeviceChoice:
    """The resolved compute device plus the evidence behind the decision."""

    device: str                      # "cuda" or "cpu"
    reason: str
    gpu: GpuInfo = field(default_factory=GpuInfo)
    torch: TorchStatus = field(default_factory=TorchStatus)
    #: What ONNX Runtime offers; the OCR fallback runs on ONNX, not on torch.
    onnx_providers: str = ""
    #: Set when an RTX card is present but CUDA cannot be used after all.
    degraded: bool = False

    @property
    def is_cuda(self) -> bool:
        return self.device == "cuda"


def resolve_device(requested: str = "auto", gpu: GpuInfo | None = None) -> DeviceChoice:
    """Pick the device the OCR fallback should run on.

    ``auto`` means CUDA when an NVIDIA GPU is present *and* the installed torch
    can reach it.  An RTX card that torch cannot use is reported as degraded
    rather than quietly demoted, because on those machines CPU OCR is a wrong
    answer that merely looks like a slow one.
    """
    requested = requested.lower()
    if requested not in ("auto", "cuda", "cpu"):
        raise ValueError(f"unknown device: {requested}")
    gpu = detect_nvidia_gpus() if gpu is None else gpu

    if requested == "cpu":
        return DeviceChoice("cpu", "requested explicitly", gpu)

    status = probe_torch()
    has_provider, providers = onnx_cuda_provider()

    if not status.installed:
        reason = f"PyTorch is not importable ({status.error})"
    elif not status.cuda_available:
        reason = ("torch.cuda.is_available() is False"
                  + (f" ({status.error})" if status.error else "")
                  + f"; this build is {status.version}")
    elif not has_provider:
        # The OCR fallback runs on ONNX Runtime, so a CUDA torch alone is not
        # enough: without onnxruntime-gpu there is no CUDAExecutionProvider and
        # the session would run on the CPU while reporting CUDA everywhere else.
        reason = ("ONNX Runtime has no CUDAExecutionProvider (installed providers: "
                  f"{providers}); run setup.bat to install onnxruntime-gpu")
    else:
        return DeviceChoice("cuda", "torch and ONNX Runtime both reach the GPU",
                            gpu, status, onnx_providers=providers)

    if requested == "cuda":
        raise RuntimeError(f"--device cuda was requested but {reason}")
    return DeviceChoice("cpu", reason, gpu, status, onnx_providers=providers,
                        degraded=gpu.has_rtx)


def describe_choice(choice: DeviceChoice) -> list[str]:
    """-> human-readable device lines for the conversion header."""
    lines = [f"  NVIDIA GPU: {choice.gpu.describe()}"]
    if choice.torch.installed:
        lines.append(f"  torch {choice.torch.version} "
                     f"(CUDA {choice.torch.cuda_version or 'none'})")
        lines.append(f"  CUDA available: {choice.torch.cuda_available}")
        for name in choice.torch.device_names:
            lines.append(f"  CUDA device: {name}")
    else:
        lines.append(f"  torch: not available ({choice.torch.error})")
    if choice.onnx_providers:
        lines.append(f"  ONNX Runtime providers: {choice.onnx_providers}")
    lines.append(f"  pdf2tex device: {choice.device}  ({choice.reason})")
    if choice.degraded:
        lines.append("  WARNING: an NVIDIA GeForce RTX GPU is present but CUDA is "
                     "unusable; OCR will be far slower. Re-run setup.bat.")
    return lines


if __name__ == "__main__":                # a quick manual probe
    detected = detect_nvidia_gpus()
    print("GPUs:", detected.describe())
    print("\n".join(describe_choice(resolve_device("auto", detected))))
    sys.exit(0)

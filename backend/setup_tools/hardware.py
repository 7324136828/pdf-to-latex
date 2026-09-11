"""GPU detection for setup, before PyTorch exists to ask.

The detection itself lives in :mod:`pdfconv.device`, so setup and the converter
cannot disagree about what hardware is present; this module only adds the
reporting the setup log wants.
"""

from __future__ import annotations

from pdfconv.device import RTX_MARKER, GpuInfo, detect_nvidia_gpus

__all__ = ["GpuInfo", "RTX_MARKER", "detect_nvidia_gpus", "describe_gpus"]


def describe_gpus(gpu: GpuInfo) -> list[str]:
    """-> the lines the setup log prints about the detected hardware."""
    if not gpu.available:
        return [gpu.detail or "No NVIDIA GPU detected.",
                "CPU setup selected."]
    lines = [f"Detected NVIDIA GPU: {name}" for name in gpu.names]
    if gpu.has_rtx:
        lines.append("CUDA-enabled setup selected.")
        if len(gpu.rtx_names) > 1:
            lines.append(f"{len(gpu.rtx_names)} RTX GPUs present; "
                         "the CUDA build serves all of them.")
    else:
        lines.append("No NVIDIA GeForce RTX GPU among them; CPU setup selected.")
    return lines

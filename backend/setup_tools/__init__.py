"""The setup program, split by concern.

    console       the numbered, readable log
    environment   interpreter checks, .venv creation and re-entry
    hardware      NVIDIA GPU detection, before PyTorch exists to ask
    dependencies  every pip command, all aimed at .venv
    pytorch       the single authoritative PyTorch installation
    validation    proof that both converters can start

``setup.py`` at the top of this folder is the entry point; ``setup.bat`` only
launches it.
"""

from .environment import SetupError

__all__ = ["SetupError"]

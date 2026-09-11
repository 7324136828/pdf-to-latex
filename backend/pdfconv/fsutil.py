"""Small filesystem helpers shared by both converters."""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from pathlib import Path


def atomic_write(path: Path, text: str) -> None:
    """Commit a file with a same-filesystem atomic replacement.

    An interrupted conversion must never leave a half-written ``.tex`` behind:
    the orchestrator treats a non-empty output as finished work.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def file_digest(path: Path) -> str:
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def has_content(path: Path, minimum: int = 1) -> bool:
    """-> True when ``path`` exists and holds at least ``minimum`` bytes."""
    try:
        return path.is_file() and path.stat().st_size >= minimum
    except OSError:
        return False


def use_utf8_console() -> None:
    """Print Unicode filenames on a legacy Windows console without crashing.

    A console still on code page 1252 raises UnicodeEncodeError the first time a
    path contains a character it cannot represent, which would end a run over a
    filename rather than over anything to do with the PDF.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass

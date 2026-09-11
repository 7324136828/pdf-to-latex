"""An advisory whole-file lock, so two runs cannot share one checkpoint.

Moved out of the OCR command, which used ``fcntl`` directly and therefore could
not run on Windows at all.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path


@contextlib.contextmanager
def exclusive_lock(path: Path):
    """Hold an advisory whole-file lock, on POSIX and on Windows alike."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a")
    try:
        if os.name == "nt":
            import msvcrt

            # Lock a stable byte even if an earlier run left content behind.
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise RuntimeError("Another run is using this checkpoint "
                                   "directory") from error
            try:
                yield handle
            finally:
                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
        else:
            import fcntl

            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError("Another run is using this checkpoint "
                                   "directory") from error
            yield handle
    finally:
        handle.close()

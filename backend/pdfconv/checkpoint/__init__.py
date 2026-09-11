"""Resumable conversion state, shared by both converters.

    store     the checkpoint file, its per-document entries and the page cache
    locking   an advisory lock so two runs cannot share one checkpoint

Both converters cost minutes per document, so both record every completed page
and can be re-run to continue rather than to start over.
"""

from .locking import exclusive_lock
from .store import CHECKPOINT_VERSION, CheckpointStore, DocumentEntry, valid_file

__all__ = ["CHECKPOINT_VERSION", "CheckpointStore", "DocumentEntry",
           "exclusive_lock", "valid_file"]

"""Resumable state for a whole conversion run.

Generalised from the OCR command, which was the only thing that had it. Both
converters now use it, because both are slow enough that starting over after an
interruption is the wrong answer: OCR spends a minute or more per page, and the
text-layer route still costs seconds a page over hundreds of pages.

The shape on disk:

    <work dir>/<name>.json      one entry per document, keyed by relative path
    <work dir>/pages/<hash>/    one file per converted page, named by index

A document's ``fingerprint`` records everything that would change its output --
the PDF's own hash, the converter, its settings. When any of that moves, the
entry is discarded and the pages are converted again. When none of it has, the
cached pages are reused and only their hashes are checked.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from ..fsutil import atomic_write, file_digest

#: Bumped when the layout on disk changes in a way older files cannot satisfy.
CHECKPOINT_VERSION = 2


def valid_file(path: Path, digest: str | None) -> bool:
    """-> True when ``path`` exists and still hashes to ``digest``."""
    try:
        return bool(digest) and path.is_file() and file_digest(path) == digest
    except OSError:
        # A cache/output can disappear or become unreadable between the
        # existence check and the read, especially after an interrupted run.
        return False


@dataclass
class DocumentEntry:
    """One PDF's cached pages and publication state.

    ``data`` is the JSON-serialisable dictionary held in the checkpoint file;
    this class is the vocabulary for reading and writing it.
    """

    key: str
    data: dict
    cache: Path
    suffix: str
    commit: callable

    # -- pages ---------------------------------------------------------------

    def cached_page(self, index: int) -> str | None:
        """-> the page's converted text, or None when it must be redone."""
        record = self.data["pages"].get(str(index))
        if not record:
            return None
        path = self.page_path(index)
        if not valid_file(path, record.get("sha256")):
            return None
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return None

    def store_page(self, index: int, text: str, method: str) -> None:
        """Commit one converted page and record it in the checkpoint."""
        path = self.page_path(index)
        atomic_write(path, text)
        self.data["pages"][str(index)] = {"sha256": file_digest(path),
                                          "method": method}
        self.commit()

    def page_path(self, index: int) -> Path:
        return self.cache / f"{index + 1:06d}{self.suffix}"

    def begin_extraction(self) -> None:
        """Mark the document unfinished, because a page is about to change."""
        changed = self.data.get("extracted") or self.data.get("split")
        self.data["extracted"] = False
        self.data["split"] = False
        if changed:
            # Persist invalidation before conversion, which can be interrupted
            # before store_page gets the chance to commit it.
            self.commit()

    def finish_extraction(self) -> None:
        self.data["extracted"] = True
        self.commit()

    # -- publication ---------------------------------------------------------

    @property
    def split_done(self) -> bool:
        return bool(self.data.get("split"))

    def outputs(self) -> list[dict]:
        return list(self.data.get("outputs", []))

    def pending_outputs(self) -> list[dict]:
        return list(self.data.get("pending_outputs", []))

    def begin_publication(self, outputs: list[dict]) -> list[dict]:
        """Record publication intent before any file is written.

        -> the outputs a previous run left behind, so the caller can remove the
        ones this run no longer produces. Retaining them across the write is
        what lets a changed split configuration clean up after an interrupted
        run rather than leaving orphans forever.
        """
        previous = self.outputs() + self.pending_outputs()
        self.data["split"] = False
        self.data["pending_outputs"] = previous + outputs
        self.commit()
        return previous

    def finish_publication(self, outputs: list[dict], *, settings: dict,
                           method: str) -> None:
        self.data.update(split=True, split_settings=settings, method=method,
                         outputs=outputs)
        self.data.pop("pending_outputs", None)
        self.commit()

    def publication_matches(self, settings: dict, destination: Path) -> bool:
        """-> True when this document's files are already on disk and correct."""
        return bool(self.split_done
                    and self.data.get("split_settings") == settings
                    and self.data.get("outputs")
                    and all(valid_file(destination / row["file"], row["sha256"])
                            for row in self.data["outputs"]))

    def record_error(self, message: str) -> None:
        self.data["error"] = message
        self.data["split"] = False
        self.commit()


class CheckpointStore:
    """The checkpoint file for one input tree, plus its page cache."""

    def __init__(self, work_dir: Path, name: str, identity: dict):
        self.work_dir = Path(work_dir)
        self.path = self.work_dir / f"{name}.json"
        self.identity = {"version": CHECKPOINT_VERSION, **identity}
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.state = self._load()

    def _fresh(self) -> dict:
        return {**self.identity, "documents": {}}

    def _load(self) -> dict:
        """Read the checkpoint, discarding one that describes a different run.

        A checkpoint from another input or output directory is not an error to
        report at the user -- it is simply not about this run -- but silently
        merging it would attribute one book's pages to another.
        """
        if not self.path.is_file():
            return self._fresh()
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return self._fresh()
        if not isinstance(state, dict) or not isinstance(state.get("documents"), dict):
            return self._fresh()
        if any(state.get(key) != value for key, value in self.identity.items()):
            return self._fresh()
        return state

    def commit(self) -> None:
        atomic_write(self.path,
                     json.dumps(self.state, indent=2, ensure_ascii=False) + "\n")

    def documents(self) -> dict:
        return self.state["documents"]

    def peek(self, key: str) -> dict | None:
        """-> a document's recorded state without resetting anything.

        ``document()`` discards an entry whose fingerprint no longer matches,
        which is right when about to convert and wrong when merely asking
        whether the work is already done.
        """
        return self.state["documents"].get(key)

    def document(self, key: str, fingerprint: dict, *, suffix: str = ".tex",
                 force: bool = False) -> DocumentEntry:
        """-> the entry for one PDF, reset when its fingerprint no longer holds."""
        data = self.state["documents"].get(key, {})
        if force or data.get("fingerprint") != fingerprint:
            data = {"fingerprint": fingerprint, "pages": {}, "extracted": False,
                    "split": False, "outputs": data.get("outputs", []),
                    "pending_outputs": data.get("pending_outputs", [])}
            self.state["documents"][key] = data
        data.pop("error", None)
        cache = self.work_dir / "pages" / hashlib.sha256(
            key.encode("utf-8")).hexdigest()
        entry = DocumentEntry(key=key, data=data, cache=cache, suffix=suffix,
                              commit=self.commit)
        self.commit()
        return entry

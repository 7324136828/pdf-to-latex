"""Drive the OCR pipeline one document at a time, reusing one loaded model.

``main`` is the standalone command: it discovers its own PDFs and keeps its own
checkpoint. The orchestrator needs something narrower -- one document, the
caller's checkpoint, and above all one model shared by every fallback -- so this
adapter keeps the expensive part of ``main`` (``ocr.extract_pages``, with its
page rendering and per-page cache) and replaces only the driver around it.

The converter is created lazily, on the first PDF the text-layer route cannot
handle, and then reused: loading Pix2Text costs far more than converting a
page, and on CUDA a second copy of the weights is VRAM the first copy needs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pymupdf

from . import ocr as ocr_engine
from .latex_output import latex_document
from ..checkpoint import DocumentEntry
from ..paths import MODEL_DIR

#: Bumped when a change here would recognise the same page differently.
EXTRACTION_VERSION = 2


@dataclass
class Pdf2TexPages:
    """What one OCR pass produced, before it is cut into chapters."""

    pages: list[str]
    toc: list
    device: str
    backend: str

    @property
    def characters(self) -> int:
        return sum(len(page) for page in self.pages)


def render(heading: str, body: str, source: str) -> str:
    """Wrap one chapter as a standalone XeLaTeX/LuaLaTeX document."""
    return latex_document(body, source=source, heading=heading)


def _fingerprint(digest: str, *, backend: str, dpi: int) -> dict:
    return {"sha256": digest, "version": EXTRACTION_VERSION,
            "converter": "pdf2tex", "mode": "math", "dpi": dpi,
            "backend": backend, "pymupdf": pymupdf.VersionBind}


def fingerprint_matches(saved: dict, digest: str, *, device: str, dpi: int) -> bool:
    """Check a completed run without loading models or probing hardware.

    The orchestrator already resolves its device to CPU or CUDA. Other device
    requests need the model's backend selection before their cache is trusted.
    """
    return (device in ("cpu", "cuda")
            and saved == _fingerprint(digest, backend=device, dpi=dpi))


class Pdf2TexConverter:
    """A loaded OCR model, reused across every document that needs it."""

    def __init__(self, device: str = "auto", *, model_dir: Path = MODEL_DIR,
                 dpi: int = 300, verbose: bool = False):
        self.device = device
        self.model_dir = Path(model_dir)
        self.dpi = dpi
        self.verbose = verbose
        self._ocr = None

    # -- model ---------------------------------------------------------------

    @property
    def loaded(self) -> bool:
        return self._ocr is not None

    def ocr(self):
        """-> the OCR backend, loading it on first use."""
        if self._ocr is None:
            from .math_ocr import MathOCR

            self._ocr = MathOCR(self.device, self.model_dir)
        return self._ocr

    def describe_device(self) -> str:
        if self._ocr is None:
            return f"{self.device} (model not loaded yet)"
        return self._ocr.describe()

    def fingerprint(self, digest: str) -> dict:
        """What must hold for this document's recognised pages to still count.

        The model is loaded first on purpose: which backend answered is part of
        the fingerprint, and a CPU run's pages should not be reused as if the
        GPU had produced them.
        """
        backend = self.ocr()
        return _fingerprint(digest, backend=backend.backend, dpi=self.dpi)

    # -- conversion ------------------------------------------------------------

    def convert(self, pdf: Path, entry: DocumentEntry, *,
                cancel_requested: Callable[[], bool] | None = None) -> Pdf2TexPages:
        """Recognise every page of one PDF, reusing the checkpoint's pages."""
        backend = self.ocr()
        try:
            with pymupdf.open(pdf) as doc:
                if doc.needs_pass:
                    raise RuntimeError("PDF is password protected")
                if not len(doc):
                    raise RuntimeError("PDF contains no pages")
                pages = ocr_engine.extract_pages(doc, entry, mode="math", dpi=self.dpi,
                                                 workers=1, get_ocr=self.ocr,
                                                 cancel_requested=cancel_requested)
                toc = doc.get_toc()
        finally:
            # Release scratch memory even if a page fails; the same loaded
            # model must still be usable for the next document in the walk.
            self.release()
        return Pdf2TexPages(pages=pages, toc=toc, device=backend.device_label,
                            backend=backend.backend)

    def release(self) -> None:
        """Hand back per-document GPU scratch space, keeping the weights loaded."""
        if self._ocr is not None:
            self._ocr.release()

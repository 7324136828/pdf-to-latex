"""Reading pages as images: the OCR engines, and the checkpointed page loop.

Split out of ``main`` so the recursive converter can drive page extraction
without going through that command's own tree walk. The loop and the Vision
backend are otherwise unchanged; only the checkpoint bookkeeping moved behind
:class:`pdfconv.checkpoint.DocumentEntry`.
"""

from __future__ import annotations

import sys
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Callable

import pymupdf

from ..checkpoint import DocumentEntry

#: Below this many alphanumerics a page's embedded text is not worth trusting,
#: and ``--mode auto`` renders and recognises the page instead.
EMBEDDED_TEXT_THRESHOLD = 40


class VisionOCR:
    """Resolve PyObjC bindings once on the main thread, then OCR independent images."""

    def __init__(self, device: str):
        if sys.platform != "darwin":
            raise RuntimeError("Apple Vision OCR requires macOS. Use --mode text for embedded text.")
        try:
            from objc import autorelease_pool
            from Foundation import NSData
            from Quartz import CGImageSourceCreateImageAtIndex, CGImageSourceCreateWithData
            from Vision import (VNImageRequestHandler, VNRecognizeTextRequest,
                                VNRequestTextRecognitionLevelAccurate)
        except ImportError as error:
            raise RuntimeError("Install OCR dependencies with: python -m pip install -r requirements.txt") from error
        self.pool = autorelease_pool
        self.data = NSData
        self.image_at_index = CGImageSourceCreateImageAtIndex
        self.image_source = CGImageSourceCreateWithData
        self.handler = VNImageRequestHandler
        self.request = VNRecognizeTextRequest
        self.level = VNRequestTextRecognitionLevelAccurate
        self.device = device
        self.compute_devices = []
        with self.pool():
            probe = self.new_request()
            if device == "gpu":
                try:
                    from CoreML import MLGPUComputeDevice
                except ImportError as error:
                    raise RuntimeError("GPU selection requires macOS 14+ and pyobjc-framework-CoreML") from error
                if not hasattr(probe, "supportedComputeStageDevicesAndReturnError_"):
                    raise RuntimeError("Explicit GPU selection requires macOS 14+. Use --device auto on older macOS.")
                stages, error = probe.supportedComputeStageDevicesAndReturnError_(None)
                if error is not None or stages is None:
                    raise RuntimeError(f"Cannot query Vision compute devices: {error}")
                for stage, devices in stages.items():
                    gpu = next((d for d in devices if isinstance(d, MLGPUComputeDevice)), None)
                    if gpu is not None:
                        self.compute_devices.append((stage, gpu))
                if not self.compute_devices:
                    raise RuntimeError("Vision reports no supported GPU stages. Use --device auto or --device cpu.")
                print("Vision GPU stages: " + ", ".join(str(s) for s, _ in self.compute_devices))
            else:
                print(f"Vision device: {device}")

    def new_request(self):
        request = self.request.alloc().init()
        request.setRecognitionLevel_(self.level)
        request.setUsesLanguageCorrection_(True)
        request.setUsesCPUOnly_(self.device == "cpu")
        for stage, device in self.compute_devices:
            request.setComputeDevice_forComputeStage_(device, stage)
        return request

    def __call__(self, png: bytes) -> str:
        with self.pool():
            data = self.data.dataWithBytes_length_(png, len(png))
            source = self.image_source(data, None)
            if source is None:
                raise RuntimeError("Cannot decode rendered page")
            image = self.image_at_index(source, 0, None)
            if image is None:
                raise RuntimeError("Cannot create OCR image")
            handler = self.handler.alloc().initWithCGImage_options_(image, None)
            request = self.new_request()
            success, error = handler.performRequests_error_([request], None)
            if not success or error is not None:
                raise RuntimeError(f"Vision OCR failed: {error}")
            lines = []
            for observation in request.results() or []:
                candidates = observation.topCandidates_(1)
                if candidates:
                    lines.append(str(candidates[0].string()))
            return "\n".join(lines)


def extract_pages(doc, entry: DocumentEntry, *, mode: str, dpi: int, workers: int,
                  get_ocr, cancel_requested: Callable[[], bool] | None = None
                  ) -> list[str]:
    """Keep at most ``workers`` rendered images in flight; checkpoint completions."""
    pages = [""] * len(doc)
    pending: dict = {}
    entry.cache.mkdir(parents=True, exist_ok=True)

    def record(index: int, text: str, method: str) -> None:
        entry.store_page(index, text, method)
        pages[index] = text
        print(f"  page {index + 1}/{len(doc)} ({method})", flush=True)

    def collect() -> None:
        completed, _ = wait(pending, return_when=FIRST_COMPLETED)
        errors = []
        for future in completed:
            index = pending.pop(future)
            try:
                record(index, future.result(), "vision")
            except Exception as error:
                errors.append(error)
        if errors:
            raise errors[0]

    with ThreadPoolExecutor(max_workers=workers) as executor:
        for index in range(len(doc)):
            if cancel_requested and cancel_requested():
                while pending:
                    collect()
                raise InterruptedError("Conversion discarded by user")
            cached = entry.cached_page(index)
            if cached is not None:
                pages[index] = cached
                continue
            entry.begin_extraction()
            page = doc[index]  # All PyMuPDF operations stay on this thread.
            embedded = page.get_text(sort=True) if mode in ("auto", "text") else ""
            if mode == "text" or (mode == "auto" and sum(c.isalnum() for c in embedded)
                                  >= EMBEDDED_TEXT_THRESHOLD):
                record(index, embedded, "embedded")
                continue
            ocr = get_ocr()
            png = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB,
                                  alpha=False).tobytes("png")
            if mode == "math":
                # Shared inference models are not thread-safe. Math recognition
                # always examines page images, even when embedded text exists.
                record(index, ocr(png), "pix2text")
                continue
            pending[executor.submit(ocr, png)] = index
            if len(pending) >= workers:
                collect()
        while pending:
            collect()
    entry.finish_extraction()
    return pages

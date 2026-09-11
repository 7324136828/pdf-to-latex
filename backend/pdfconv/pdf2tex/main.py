#!/usr/bin/env python3
"""Recursively OCR PDFs, mirror their folders, and split LaTeX into chapters.

    .venv\\Scripts\\python.exe -m pdfconv.pdf2tex.main
    .venv\\Scripts\\python.exe -m pdfconv.pdf2tex.main --mode text --workers 2

The original OCR command, kept because it is the only way to reach the Apple
Vision plain-text pipeline (``--mode text``/``ocr``/``auto``). For the ordinary
job -- convert everything under input/, text layer first and OCR only where it
has to -- use convert_pdfs.py, which drives both converters.

The parts this shares with the rest of the project now live beside it:
page extraction in ``pdfconv.pdf2tex.ocr``, chapter splitting in
``pdfconv.chapter_split``, resumability in ``pdfconv.checkpoint``.

Math-aware Pix2Text OCR is the default, on CUDA or Apple's GPU when either is
available. Rendering, chapter detection, and file I/O use the CPU. Page content
is retained in output_tmp so interruptions, missing outputs, and splitting
changes do not require another OCR pass. Re-running the same command resumes.
"""
from __future__ import annotations

import argparse
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import pymupdf

from . import ocr as ocr_engine
from .latex_output import latex_document
from .ocr import VisionOCR
from ..chapter_split import (PAGE_SEPARATOR, flat_section_filename, plan, publish,
                             settings_for, write_catalog)
from ..checkpoint import CheckpointStore, exclusive_lock
from ..fsutil import file_digest
from ..paths import INPUT_DIR, MODEL_DIR, OUTPUT_DIR

BASE_DIR = Path(__file__).resolve().parent
EXTRACTION_VERSION = 2


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", "--source", dest="source", type=Path,
                        default=INPUT_DIR, help="PDF file or recursive input folder")
    parser.add_argument("--output", "--destination", dest="destination", type=Path,
                        default=OUTPUT_DIR, help="Output folder (structure is mirrored)")
    parser.add_argument("--checkpoint-dir", type=Path,
                        help="Page cache/checkpoint folder (default: <output>_tmp)")
    parser.add_argument("--format", choices=("latex", "text"),
                        help="Chapter output format (default: latex; .tex files)")
    parser.add_argument("--mode", choices=("math", "ocr", "auto", "text"),
                        help="math: formula-aware OCR; ocr: Vision; auto: embedded text/Vision; "
                             "text: embedded text only (default: math for latex, ocr for text)")
    parser.add_argument("--device", choices=("gpu", "auto", "cpu", "cuda"),
                        default="auto",
                        help="GPU (CoreML or CUDA), automatic, CPU, or CUDA "
                             "execution (default: auto)")
    parser.add_argument("--model-dir", type=Path, default=MODEL_DIR,
                        help="Local math OCR model/cache folder (downloaded on first use)")
    parser.add_argument("--workers", type=int, default=2,
                        help="Concurrent Vision pages (default: 2); math OCR processes one page at a time")
    parser.add_argument("--dpi", type=int, default=300, help="OCR rendering DPI (default: 300)")
    parser.add_argument("--min-section-chars", type=int, default=3000,
                        help="Minimum heading spacing for text detection (default: 3000)")
    parser.add_argument("--force", action="store_true", help="Re-extract and split all PDFs")
    args = parser.parse_args(argv)
    # Keep explicit legacy --mode text/ocr/auto commands working as before.
    args.format = args.format or ("text" if args.mode in ("ocr", "auto", "text") else "latex")
    args.mode = args.mode or ("math" if args.format == "latex" else "ocr")
    if (args.format == "latex") != (args.mode == "math"):
        parser.error("LaTeX requires --mode math; plain-text modes require --format text")
    args.model_dir = args.model_dir.resolve()
    for name in ("workers", "dpi"):
        if getattr(args, name) < 1:
            parser.error(f"--{name} must be positive")
    if args.min_section_chars < 0:
        parser.error("--min-section-chars must not be negative")
    args.source = args.source.resolve()
    args.destination = args.destination.resolve()
    args.checkpoint_dir = (args.checkpoint_dir or args.destination.with_name(
        args.destination.name + "_tmp")).resolve()
    if not args.source.exists():
        parser.error(f"Source does not exist: {args.source}")
    if args.source.is_file() and args.source.suffix.casefold() != ".pdf":
        parser.error("Source file must be a PDF")
    if args.destination == args.checkpoint_dir:
        parser.error("Output and checkpoint directories must be different")
    source_root = args.source.parent if args.source.is_file() else args.source
    if any(source_root.is_relative_to(path) for path in (args.destination, args.checkpoint_dir)):
        parser.error("Output/checkpoint directories cannot contain or equal the input directory")
    return args


def renderer_for(args: argparse.Namespace):
    """-> the function that turns one section into a file's contents."""
    if args.format != "latex":
        return lambda heading, body, source: body
    return lambda heading, body, source: latex_document(body, source=source,
                                                        heading=heading)


def run(args: argparse.Namespace) -> int:
    source_root = args.source.parent if args.source.is_file() else args.source
    pdfs = [args.source] if args.source.is_file() else sorted(
        (p for p in args.source.rglob("*") if p.is_file() and p.suffix.casefold() == ".pdf"
         and not p.is_relative_to(args.destination) and not p.is_relative_to(args.checkpoint_dir)),
        key=lambda p: p.relative_to(source_root).as_posix().casefold())
    if not pdfs:
        print(f"No PDFs found under {args.source}")
        return 0
    # Detect filename aliases before writing any chapters (including case aliases).
    prefixes = set()
    for pdf in pdfs:
        relative = pdf.relative_to(source_root)
        prefix = (relative.parent / flat_section_filename(relative.stem, 0, "")).as_posix().casefold()
        if prefix in prefixes:
            raise RuntimeError(f"Output filename collision; rename one of the PDFs: {relative}")
        prefixes.add(prefix)
    args.destination.mkdir(parents=True, exist_ok=True)
    args.checkpoint_dir.mkdir(parents=True, exist_ok=True)
    suffix = ".tex" if args.format == "latex" else ".txt"
    render = renderer_for(args)
    split_settings = settings_for(args.min_section_chars, {"format": args.format})

    # Prevent two runs racing on the same checkpoint; the lock is released with
    # the file handle on every platform.
    with exclusive_lock(args.checkpoint_dir / "run.lock"):
        store = CheckpointStore(args.checkpoint_dir, "checkpoint",
                                {"source": str(source_root),
                                 "destination": str(args.destination)})
        backend = None

        def get_ocr():
            nonlocal backend
            if backend is None:
                if args.mode == "math":
                    from .math_ocr import MathOCR
                    backend = MathOCR(args.device, args.model_dir)
                else:
                    backend = VisionOCR(args.device)
            return backend

        # Fail once, before invalidating checkpoints, if math dependencies are missing.
        math_versions = {}
        if args.mode == "math":
            try:
                math_versions = {name: version(name) for name in
                                 ("pix2text", "optimum", "transformers")}
                # CUDA setup replaces the CPU distribution. Both expose the
                # same import package, but only their own metadata name exists.
                try:
                    math_versions["onnxruntime"] = version("onnxruntime")
                except PackageNotFoundError:
                    math_versions["onnxruntime-gpu"] = version("onnxruntime-gpu")
            except PackageNotFoundError as error:
                raise RuntimeError("The math OCR dependencies are missing. Run "
                                   "setup.bat, which creates .venv and installs "
                                   "requirements-math.txt. See README.md.") from error

        failures = 0
        for pdf in pdfs:
            relative = pdf.relative_to(source_root)
            key = relative.as_posix()
            print(f"PDF: {key}", flush=True)
            fingerprint = {"sha256": file_digest(pdf), "version": EXTRACTION_VERSION,
                           "mode": args.mode, "device": args.device, "dpi": args.dpi,
                           "pymupdf": pymupdf.VersionBind}
            if args.mode == "math":
                fingerprint.update(math_version=1, models="mfd-1.5/mfr-1.5-onnx",
                                   packages=math_versions)
            entry = store.document(key, fingerprint, suffix=suffix, force=args.force)
            try:
                with pymupdf.open(pdf) as doc:
                    if doc.needs_pass:
                        raise RuntimeError("PDF is password protected")
                    if not len(doc):
                        raise RuntimeError("PDF contains no pages")
                    pages = ocr_engine.extract_pages(
                        doc, entry, mode=args.mode, dpi=args.dpi,
                        workers=args.workers, get_ocr=get_ocr)
                    toc = doc.get_toc()
                entry.data["converter"] = "pdf2tex"
                # Extraction above re-verified every cached page; only the far
                # cheaper split is skipped when its output is already correct.
                if entry.publication_matches(split_settings, args.destination):
                    print(f"  Skip chapters (verified): {relative}")
                else:
                    split = plan(relative, pages, toc, render=render,
                                 minimum=args.min_section_chars, suffix=suffix)
                    outputs = publish(entry, split, args.destination,
                                      settings=split_settings)
                    print(f"  Wrote {len(outputs)} sections ({split.method}): "
                          f"{relative}")
                write_catalog(store.documents(), args.destination)
            except Exception as error:
                failures += 1
                entry.record_error(str(error))
                print(f"  FAILED: {key}: {error}", file=sys.stderr)
        write_catalog(store.documents(), args.destination)
        print(f"Finished: {len(pdfs) - failures}/{len(pdfs)} PDFs; output: {args.destination}")
        return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return run(args)
    except KeyboardInterrupt:
        print("\nInterrupted. Re-run the same command to resume saved pages.", file=sys.stderr)
        return 130
    except (RuntimeError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Convert PDFs under ``input/`` into chapter ``.tex`` files under ``output/``.

    .venv\\Scripts\\python.exe convert_pdfs.py
    .venv\\Scripts\\python.exe convert_pdfs.py --force --verbose
    .venv\\Scripts\\python.exe convert_pdfs.py --input D:\\books --output D:\\latex

Each PDF is converted with the project's text-layer pipeline first and, only if
that cannot read it, with the Pix2Text OCR pipeline on the GPU.  Run setup.bat
first; it creates ``.venv`` and installs everything this needs.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pdfconv import chapter_split, paths                         # noqa: E402
from pdfconv.fsutil import use_utf8_console                      # noqa: E402
from pdfconv.orchestrator import Converter, Options              # noqa: E402


def parse_args(argv: list[str] | None = None) -> Options:
    parser = argparse.ArgumentParser(
        prog="convert_pdfs.py", description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", type=Path, default=paths.INPUT_DIR,
                        help="folder searched recursively for PDFs "
                             "(default: %(default)s)")
    parser.add_argument("--output", type=Path, default=paths.OUTPUT_DIR,
                        help="folder the input tree is mirrored into "
                             "(default: %(default)s)")
    parser.add_argument("--work-dir", type=Path, default=paths.WORK_DIR,
                        help="page cache and checkpoints for both converters "
                             "(default: %(default)s)")
    parser.add_argument("--model-dir", type=Path, default=paths.MODEL_DIR,
                        help="downloaded OCR models (default: %(default)s)")
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto",
                        help="device for the OCR fallback; auto uses CUDA when an "
                             "NVIDIA GPU and a CUDA PyTorch are both present")
    parser.add_argument("--dpi", type=int, default=300,
                        help="OCR page rendering resolution (default: %(default)s)")
    parser.add_argument("--min-section-chars", type=int,
                        default=chapter_split.DEFAULT_MIN_SECTION_CHARS,
                        help="minimum heading spacing when a PDF has no bookmarks "
                             "and chapters must be found in the text "
                             "(default: %(default)s)")
    parser.add_argument("--force", action="store_true",
                        help="reprocess PDFs whose .tex output already exists")
    parser.add_argument("--traditional-only", action="store_true",
                        help="never fall back to OCR")
    parser.add_argument("--pdf2tex-only", action="store_true",
                        help="skip the text-layer route and always use OCR")
    parser.add_argument("--verbose", action="store_true",
                        help="print full tracebacks for failures")
    args = parser.parse_args(argv)

    if args.traditional_only and args.pdf2tex_only:
        parser.error("--traditional-only and --pdf2tex-only are mutually exclusive")
    if args.dpi < 1:
        parser.error("--dpi must be positive")
    if args.min_section_chars < 0:
        parser.error("--min-section-chars must not be negative")
    source, destination = args.input.resolve(), args.output.resolve()
    work_dir = args.work_dir.resolve()
    if not source.exists():
        parser.error(f"input folder does not exist: {source}")
    if not source.is_dir():
        parser.error(f"input is not a folder: {source}")
    if source == destination or source.is_relative_to(destination):
        parser.error("the output folder cannot contain or equal the input folder")
    if destination == work_dir:
        parser.error("the output and checkpoint folders must be different")
    if source.is_relative_to(work_dir):
        parser.error("the checkpoint folder cannot contain or equal the input folder")
    return Options(source=source, destination=destination,
                   work_dir=work_dir,
                   model_dir=args.model_dir.resolve(), device=args.device,
                   dpi=args.dpi, min_section_chars=args.min_section_chars,
                   force=args.force,
                   traditional_only=args.traditional_only,
                   pdf2tex_only=args.pdf2tex_only, verbose=args.verbose)


def main(argv: list[str] | None = None) -> int:
    use_utf8_console()
    options = parse_args(argv)
    options.destination.mkdir(parents=True, exist_ok=True)
    try:
        summary = Converter(options).run()
    except KeyboardInterrupt:
        return 130
    except (RuntimeError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 1 if summary.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

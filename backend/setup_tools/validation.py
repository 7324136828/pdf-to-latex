"""Prove the two converters can actually start before setup claims success.

Neither check reads a real book.  The text-layer route is exercised on a PDF
built in memory, and the OCR route is only asked which device it would choose,
because loading Pix2Text downloads several hundred megabytes of weights.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass

from .dependencies import PIP_TIMEOUT
from .environment import SetupError


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


def _probe(source: str) -> dict:
    """Run a check in a child interpreter so a segfaulting library cannot end setup."""
    try:
        completed = subprocess.run([sys.executable, "-c", source], text=True,
                                   capture_output=True, timeout=PIP_TIMEOUT,
                                   check=False)
    except (OSError, subprocess.SubprocessError) as error:
        raise SetupError(f"Could not run a converter check: {error}") from error
    lines = [line for line in completed.stdout.splitlines() if line.startswith("{")]
    if not lines:
        detail = (completed.stderr or completed.stdout or "no output").strip()
        return {"ok": False, "detail": detail.splitlines()[-1] if detail else "no output"}
    try:
        return json.loads(lines[-1])
    except ValueError:
        return {"ok": False, "detail": lines[-1]}


#: A one-page PDF with a text layer, converted end to end through the real
#: pipeline: imports, glyph model, layout, LaTeX emission and validation.
_TRADITIONAL = r"""
import json, sys, tempfile
from pathlib import Path
sys.path.insert(0, PYTHON_ROOT)
try:
    import pymupdf
    from pdfconv.checkpoint import CheckpointStore
    from pdfconv.traditional.runner import convert_document
    with tempfile.TemporaryDirectory() as folder:
        folder_path = Path(folder)
        pdf = folder_path / "probe.pdf"
        document = pymupdf.open()
        page = document.new_page()
        body = ("The probability that the sum of two dice equals seven is one "
                "sixth, and this sentence exists only so the converter has real "
                "prose to lay out and escape on its way to LaTeX. ") * 6
        page.insert_textbox(pymupdf.Rect(72, 72, 523, 720), body, fontsize=11)
        document.save(pdf)
        document.close()
        store = CheckpointStore(folder_path / "cache", "probe", {})
        entry = store.document("probe.pdf", {"sha256": "probe"})
        result, toc, title, author = convert_document(pdf, entry)
        chars = sum(len(p) for p in result.pages)
    print(json.dumps({"ok": True,
                      "detail": f"converted a probe page to {chars} bytes "
                                f"of LaTeX (PyMuPDF {pymupdf.VersionBind})"}))
except Exception as error:
    print(json.dumps({"ok": False, "detail": f"{type(error).__name__}: {error}"}))
"""

#: Which backend the OCR route would pick, without constructing it.
_PDF2TEX = r"""
import json, sys
sys.path.insert(0, PYTHON_ROOT)
try:
    from pdfconv.pdf2tex.math_ocr import MathOCR, preload_cuda_runtime
    cuda_torch = preload_cuda_runtime()
    import onnxruntime as ort
    from PIL import Image                      # noqa: F401 - import check only
    import pix2text                            # noqa: F401 - import check only
    from importlib.metadata import version
    providers = ort.get_available_providers()
    backend = MathOCR._resolve_backend(REQUESTED, providers, cuda_torch)
    print(json.dumps({"ok": True, "backend": backend, "providers": providers,
                      "onnxruntime": ort.__version__, "cuda_torch": cuda_torch,
                      "pix2text": version("pix2text")}))
except Exception as error:
    print(json.dumps({"ok": False, "detail": f"{type(error).__name__}: {error}"}))
"""


def _with(source: str, **values: str) -> str:
    for name, value in values.items():
        source = source.replace(name, json.dumps(value))
    return source


def check_traditional(python_root: str) -> CheckResult:
    result = _probe(_with(_TRADITIONAL, PYTHON_ROOT=python_root))
    return CheckResult("Traditional converter", bool(result.get("ok")),
                       result.get("detail", ""))


def check_pdf2tex(python_root: str, *, cuda: bool) -> tuple[CheckResult, str]:
    """-> (readiness, the device the OCR route would use)."""
    requested = "cuda" if cuda else "cpu"
    result = _probe(_with(_PDF2TEX, PYTHON_ROOT=python_root, REQUESTED=requested))
    if not result.get("ok"):
        return CheckResult("pdf2tex converter", False, result.get("detail", "")), "none"
    backend = result["backend"]
    detail = (f"pix2text {result['pix2text']}, onnxruntime {result['onnxruntime']} "
              f"({', '.join(p.replace('ExecutionProvider', '') for p in result['providers'])})")
    return CheckResult("pdf2tex converter", True, detail), backend

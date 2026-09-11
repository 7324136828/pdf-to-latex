"""Formula-output pipeline integration without downloading or loading models."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pymupdf

from pdfconv.chapter_split import sections as split_sections
from pdfconv.checkpoint import CheckpointStore
from pdfconv.pdf2tex import main, runner


MATH_PAGES = (
    "Preface\nA 5\\% rate.\n",
    "Chapter 1 First\n" + r"\(a_i = \frac{x^2}{\sqrt{y}}\)" + "\n",
    "Chapter 2 Second\n" + r"\[\sum_{k=0}^{n} v^k = \frac{1-v^{n+1}}{1-v}\]" + "\n",
)


class MathPipelineTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source = self.root / "input"
        self.source.mkdir()
        self.output = self.root / "output"
        self.cache = self.root / "output_tmp"
        dependency_versions = patch.object(main, "version", return_value="test-version")
        dependency_versions.start()
        self.addCleanup(dependency_versions.stop)

    def pdf(self, relative="nested/book.pdf"):
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with pymupdf.open() as document:
            for text in ("Preface\nPlain embedded text.", "Chapter 1 First\nFirst body.",
                         "Chapter 2 Second\nSecond body."):
                document.new_page().insert_text((72, 72), text)
            document.set_toc([[1, "Chapter 1 First", 2], [1, "Chapter 2 Second", 3]])
            document.save(path)
        return path

    def arguments(self, *extra):
        return ["--input", str(self.source), "--output", str(self.output),
                "--workers", "1", "--dpi", "72", "--min-section-chars", "0", *extra]

    def run_pipeline(self, *extra):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main.main(self.arguments(*extra))

    def documents(self):
        return json.loads((self.cache / "checkpoint.json").read_text())["documents"]

    def test_default_is_math_latex_and_legacy_mode_infers_text(self):
        defaults = main.parse_args(self.arguments())
        self.assertEqual((defaults.mode, defaults.format, defaults.device), ("math", "latex", "auto"))
        for mode in ("text", "ocr", "auto"):
            with self.subTest(mode=mode):
                args = main.parse_args(self.arguments("--mode", mode))
                self.assertEqual((args.mode, args.format), (mode, "text"))
        args = main.parse_args(self.arguments("--format", "text"))
        self.assertEqual((args.mode, args.format), ("ocr", "text"))

    def test_incompatible_mode_and_format_are_rejected(self):
        for mode, output_format in (("math", "text"), ("text", "latex"),
                                    ("ocr", "latex"), ("auto", "latex")):
            with self.subTest(mode=mode, output_format=output_format):
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                    main.parse_args(self.arguments("--mode", mode, "--format", output_format))
                self.assertEqual(error.exception.code, 2)

    def test_explicit_legacy_text_output_still_uses_embedded_text(self):
        self.pdf()
        with patch("pdfconv.pdf2tex.math_ocr.MathOCR", side_effect=AssertionError("unexpected math OCR")):
            self.assertEqual(self.run_pipeline("--mode", "text", "--format", "text"), 0)
        entry = self.documents()["nested/book.pdf"]
        outputs = [self.output / row["file"] for row in entry["outputs"]]
        self.assertTrue(all(path.suffix == ".txt" for path in outputs))
        self.assertIn("Plain embedded text.", "".join(path.read_text() for path in outputs))
        self.assertTrue(all(page["method"] == "embedded" for page in entry["pages"].values()))

    def test_recursive_chapters_are_standalone_tex_with_unchanged_math(self):
        for relative in ("one/book.pdf", "two/deep/book.PDF"):
            self.pdf(relative)
        with patch("pdfconv.pdf2tex.math_ocr.MathOCR") as backend:
            backend.return_value.side_effect = MATH_PAGES * 2
            self.assertEqual(self.run_pipeline(), 0)
            self.assertEqual(backend.call_count, 1)
            self.assertEqual(backend.return_value.call_count, 6)
        documents = self.documents()
        self.assertEqual(set(documents), {"one/book.pdf", "two/deep/book.PDF"})
        for relative, entry in documents.items():
            self.assertTrue(entry["extracted"] and entry["split"])
            self.assertEqual(entry["method"], "PDF bookmarks")
            self.assertEqual(len(entry["outputs"]), 3)
            contents = []
            for row in entry["outputs"]:
                path = Path(row["file"])
                self.assertEqual(path.parent, Path(relative).parent)
                self.assertEqual(path.suffix, ".tex")
                document = (self.output / path).read_text()
                self.assertEqual(document.count(r"\begin{document}"), 1)
                self.assertTrue(document.endswith("\\end{document}\n"))
                contents.append(document)
            combined = "".join(contents)
            for page in MATH_PAGES:
                self.assertIn(page, combined)
            self.assertTrue(all(page["method"] == "pix2text" for page in entry["pages"].values()))
        self.assertEqual(len(list((self.cache / "pages").rglob("*.tex"))), 6)

    def test_checkpoint_rerun_does_not_load_models_or_render_pages(self):
        self.pdf()
        with patch("pdfconv.pdf2tex.math_ocr.MathOCR") as backend:
            backend.return_value.side_effect = MATH_PAGES
            self.assertEqual(self.run_pipeline(), 0)
        entry = self.documents()["nested/book.pdf"]
        original = {row["file"]: (self.output / row["file"]).stat().st_mtime_ns
                    for row in entry["outputs"]}
        with patch("pdfconv.pdf2tex.math_ocr.MathOCR", side_effect=AssertionError("model loaded on resume")), \
             patch.object(pymupdf.Page, "get_pixmap", side_effect=AssertionError("rendered cached page")):
            self.assertEqual(self.run_pipeline(), 0)
        for relative, modified in original.items():
            self.assertEqual((self.output / relative).stat().st_mtime_ns, modified)

    def test_cuda_runtime_distribution_is_accepted(self):
        self.pdf()

        def installed_version(name):
            if name == "onnxruntime":
                raise main.PackageNotFoundError(name)
            return "test-version"

        with patch.object(main, "version", side_effect=installed_version), \
             patch("pdfconv.pdf2tex.math_ocr.MathOCR") as backend:
            backend.return_value.side_effect = MATH_PAGES
            self.assertEqual(self.run_pipeline("--device", "cuda"), 0)
            self.assertEqual(backend.return_value.call_count, 3)
        packages = self.documents()["nested/book.pdf"]["fingerprint"]["packages"]
        self.assertIn("onnxruntime-gpu", packages)
        self.assertNotIn("onnxruntime", packages)

    def test_completed_runner_fingerprint_checks_do_not_load_models(self):
        with patch("pdfconv.pdf2tex.math_ocr.MathOCR") as backend:
            backend.return_value.backend = "cuda"
            saved = runner.Pdf2TexConverter("cuda", dpi=72).fingerprint("digest")
        with patch("pdfconv.pdf2tex.math_ocr.MathOCR", side_effect=AssertionError("model loaded")):
            self.assertTrue(runner.fingerprint_matches(saved, "digest", device="cuda", dpi=72))
            for digest, device, dpi in (("changed", "cuda", 72), ("digest", "cpu", 72),
                                       ("digest", "cuda", 300), ("digest", "auto", 72)):
                with self.subTest(digest=digest, device=device, dpi=dpi):
                    self.assertFalse(runner.fingerprint_matches(saved, digest, device=device, dpi=dpi))
            old_version = {**saved, "version": runner.EXTRACTION_VERSION - 1}
            self.assertFalse(runner.fingerprint_matches(old_version, "digest", device="cuda", dpi=72))

    def test_runner_releases_failed_document_and_resumes_saved_pages(self):
        pdf = self.pdf()
        store = CheckpointStore(self.cache, "runner", {"source": str(self.source)})
        with patch("pdfconv.pdf2tex.math_ocr.MathOCR") as backend, \
             contextlib.redirect_stdout(io.StringIO()):
            model = backend.return_value
            model.backend = "cuda"
            model.device_label = "cuda"
            model.side_effect = [MATH_PAGES[0], RuntimeError("failed page")]
            converter = runner.Pdf2TexConverter("cuda", dpi=72)
            saved = converter.fingerprint("digest")
            entry = store.document("nested/book.pdf#pdf2tex", saved)
            with self.assertRaisesRegex(RuntimeError, "failed page"):
                converter.convert(pdf, entry)
            model.release.assert_called_once()
        self.assertEqual(set(entry.data["pages"]), {"0"})
        self.assertFalse(entry.data["extracted"])

        # A fresh process reloads the checkpoint and OCRs only unfinished pages.
        store = CheckpointStore(self.cache, "runner", {"source": str(self.source)})
        with patch("pdfconv.pdf2tex.math_ocr.MathOCR") as backend, \
             contextlib.redirect_stdout(io.StringIO()):
            model = backend.return_value
            model.backend = "cuda"
            model.device_label = "cuda"
            model.side_effect = MATH_PAGES[1:]
            converter = runner.Pdf2TexConverter("cuda", dpi=72)
            entry = store.document("nested/book.pdf#pdf2tex", converter.fingerprint("digest"))
            result = converter.convert(pdf, entry)
            self.assertEqual(model.call_count, 2)
            model.release.assert_called_once()
        self.assertEqual(result.pages, list(MATH_PAGES))
        self.assertEqual(result.toc, [[1, "Chapter 1 First", 2], [1, "Chapter 2 Second", 3]])
        self.assertTrue(entry.data["extracted"])

    def test_switching_from_plain_text_invalidates_cached_pages(self):
        self.pdf()
        self.assertEqual(self.run_pipeline("--mode", "text"), 0)
        old_entry = self.documents()["nested/book.pdf"]
        old_outputs = [self.output / row["file"] for row in old_entry["outputs"]]
        with patch("pdfconv.pdf2tex.math_ocr.MathOCR") as backend:
            backend.return_value.side_effect = MATH_PAGES
            self.assertEqual(self.run_pipeline(), 0)
            self.assertEqual(backend.return_value.call_count, 3)
        new_entry = self.documents()["nested/book.pdf"]
        self.assertEqual(new_entry["fingerprint"]["mode"], "math")
        self.assertNotEqual(old_entry["fingerprint"], new_entry["fingerprint"])
        self.assertTrue(all(not path.exists() for path in old_outputs))
        contents = "".join((self.output / row["file"]).read_text() for row in new_entry["outputs"])
        self.assertIn(MATH_PAGES[1], contents)
        self.assertNotIn("Plain embedded text.", contents)

    def test_split_recovery_uses_cached_math_fragments(self):
        self.pdf()
        original_write = split_sections.atomic_write
        writes = 0

        def fail_second_chapter(path, text):
            nonlocal writes
            if path.is_relative_to(self.output) and path.suffix == ".tex":
                writes += 1
                if writes == 2:
                    raise OSError("simulated chapter write failure")
            return original_write(path, text)

        with patch("pdfconv.pdf2tex.math_ocr.MathOCR") as backend, patch.object(split_sections, "atomic_write", fail_second_chapter):
            backend.return_value.side_effect = MATH_PAGES
            self.assertEqual(self.run_pipeline(), 1)
        entry = self.documents()["nested/book.pdf"]
        self.assertTrue(entry["extracted"])
        self.assertFalse(entry["split"])
        self.assertEqual(len(entry["pages"]), 3)
        with patch("pdfconv.pdf2tex.math_ocr.MathOCR", side_effect=AssertionError("repeated OCR after split failure")), \
             patch.object(pymupdf.Page, "get_pixmap", side_effect=AssertionError("rendered cached page")):
            self.assertEqual(self.run_pipeline(), 0)
        entry = self.documents()["nested/book.pdf"]
        self.assertTrue(entry["split"])
        self.assertNotIn("pending_outputs", entry)
        combined = "".join((self.output / row["file"]).read_text() for row in entry["outputs"])
        for page in MATH_PAGES:
            self.assertIn(page, combined)

    def test_failed_math_ocr_resumes_only_unfinished_pages(self):
        self.pdf()
        with patch("pdfconv.pdf2tex.math_ocr.MathOCR") as backend:
            backend.return_value.side_effect = [MATH_PAGES[0], RuntimeError("simulated OCR failure")]
            self.assertEqual(self.run_pipeline(), 1)
        entry = self.documents()["nested/book.pdf"]
        self.assertEqual(set(entry["pages"]), {"0"})
        self.assertFalse(entry["extracted"])
        with patch("pdfconv.pdf2tex.math_ocr.MathOCR") as backend:
            backend.return_value.side_effect = MATH_PAGES[1:]
            self.assertEqual(self.run_pipeline(), 0)
            self.assertEqual(backend.return_value.call_count, 2)
        self.assertTrue(self.documents()["nested/book.pdf"]["split"])


if __name__ == "__main__":
    unittest.main()

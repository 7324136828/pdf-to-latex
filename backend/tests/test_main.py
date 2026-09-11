"""Pipeline regression tests; fixtures are temporary and require no OCR hardware."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pymupdf

from pdfconv.chapter_split import sections as split_sections
from pdfconv.pdf2tex import main


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / "input"
        self.source.mkdir()
        self.output = self.root / "output"
        self.cache = self.root / "output_tmp"

    def pdf(self, relative="nested/book.pdf", bookmarks=True):
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with pymupdf.open() as doc:
            for text in ("Preface\nA small book.", "Chapter 1 First\nFirst chapter body.",
                         "Chapter 2 Second\nSecond chapter body."):
                doc.new_page().insert_text((72, 72), text)
            if bookmarks:
                doc.set_toc([[1, "Chapter 1 First", 2], [1, "Chapter 2 Second", 3]])
            doc.save(path)
        return path

    def run_pipeline(self, *extra):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main.main(["--input", str(self.source), "--output", str(self.output),
                              "--mode", "text", "--min-section-chars", "0", *extra])

    def state(self):
        return json.loads((self.cache / "checkpoint.json").read_text())["documents"]

    def test_recursive_case_insensitive_and_lossless_chapters(self):
        first = self.pdf("one/book.pdf")
        self.pdf("two/deep/book.PDF")
        self.assertEqual(self.run_pipeline(), 0)
        state = self.state()
        self.assertEqual(set(state), {"one/book.pdf", "two/deep/book.PDF"})
        for key, entry in state.items():
            self.assertTrue(entry["extracted"] and entry["split"])
            self.assertEqual(len(entry["outputs"]), 3)
            self.assertEqual(entry["method"], "PDF bookmarks")
            self.assertEqual(Path(entry["outputs"][0]["file"]).parent, Path(key).parent)
        with pymupdf.open(first) as doc:
            expected = main.PAGE_SEPARATOR.join(page.get_text(sort=True) for page in doc)
        actual = "".join((self.output / row["file"]).read_text()
                         for row in state["one/book.pdf"]["outputs"])
        self.assertEqual(actual, expected)
        self.assertTrue((self.output / "sections_catalog.tsv").is_file())

    def test_unchanged_resume_and_missing_output_use_cache(self):
        self.pdf()
        self.assertEqual(self.run_pipeline(), 0)
        entry = self.state()["nested/book.pdf"]
        output = self.output / entry["outputs"][1]["file"]
        original = output.read_bytes()
        modified = output.stat().st_mtime_ns
        with patch.object(pymupdf.Page, "get_text", side_effect=AssertionError("re-extracted")):
            self.assertEqual(self.run_pipeline(), 0)
            self.assertEqual(output.stat().st_mtime_ns, modified)
            output.unlink()
            self.assertEqual(self.run_pipeline(), 0)
        self.assertEqual(output.read_bytes(), original)

    def test_changed_source_invalidates_pages_and_cleans_old_chapters(self):
        path = self.pdf()
        self.assertEqual(self.run_pipeline(), 0)
        old_outputs = {self.output / row["file"] for row in self.state()["nested/book.pdf"]["outputs"]}
        path.unlink()
        with pymupdf.open() as doc:
            doc.new_page().insert_text((72, 72), "Replacement text without chapters")
            doc.save(path)
        self.assertEqual(self.run_pipeline(), 0)
        entry = self.state()["nested/book.pdf"]
        self.assertEqual(len(entry["pages"]), 1)
        self.assertEqual(len(entry["outputs"]), 1)
        self.assertTrue(all(not path.exists() for path in old_outputs))
        self.assertIn("Replacement", (self.output / entry["outputs"][0]["file"]).read_text())

    def test_failed_ocr_resumes_only_unfinished_pages(self):
        self.pdf()
        args = ("--mode", "ocr", "--workers", "1", "--dpi", "72")
        with patch.object(main, "VisionOCR") as backend:
            backend.return_value.side_effect = ["Preface", RuntimeError("simulated OCR failure")]
            self.assertEqual(self.run_pipeline(*args), 1)
        entry = self.state()["nested/book.pdf"]
        self.assertEqual(set(entry["pages"]), {"0"})
        self.assertFalse(entry["extracted"])
        with patch.object(main, "VisionOCR") as backend:
            backend.return_value.side_effect = ["Chapter 1 First", "Chapter 2 Second"]
            self.assertEqual(self.run_pipeline(*args), 0)
            self.assertEqual(backend.return_value.call_count, 2)
        self.assertTrue(self.state()["nested/book.pdf"]["split"])

    def test_split_failure_resumes_without_ocr(self):
        self.pdf()
        original = split_sections.atomic_write

        def fail_output(path, text):
            if path.is_relative_to(self.output):
                raise OSError("simulated disk error")
            return original(path, text)

        with patch.object(split_sections, "atomic_write", side_effect=fail_output):
            self.assertEqual(self.run_pipeline(), 1)
        self.assertTrue(self.state()["nested/book.pdf"]["extracted"])
        with patch.object(pymupdf.Page, "get_text", side_effect=AssertionError("re-extracted")):
            self.assertEqual(self.run_pipeline(), 0)

    def test_corrupt_cached_page_is_reextracted(self):
        self.pdf()
        self.assertEqual(self.run_pipeline(), 0)
        page = next((self.cache / "pages").rglob("000002.txt"))
        page.write_text("corrupt")
        self.assertEqual(self.run_pipeline(), 0)
        self.assertIn("First chapter body", page.read_text())

    def test_partial_chapters_are_cleaned_after_changed_split_settings(self):
        self.pdf(bookmarks=False)
        original = split_sections.atomic_write
        writes = 0

        def fail_second_chapter(path, text):
            nonlocal writes
            if path.is_relative_to(self.output) and path.suffix == ".txt":
                writes += 1
                if writes == 2:
                    raise OSError("simulated interrupted publication")
            return original(path, text)

        with patch.object(split_sections, "atomic_write", side_effect=fail_second_chapter):
            self.assertEqual(self.run_pipeline(), 1)
        partial = list(self.output.rglob("*.txt"))
        self.assertEqual(len(partial), 1)
        with patch.object(pymupdf.Page, "get_text", side_effect=AssertionError("re-extracted")):
            self.assertEqual(self.run_pipeline("--min-section-chars", "3000"), 0)
        self.assertFalse(partial[0].exists())
        self.assertEqual(len(list(self.output.rglob("*.txt"))), 1)

    def test_fallback_heading_detection_and_complete_text(self):
        self.pdf(bookmarks=False)
        self.assertEqual(self.run_pipeline(), 0)
        entry = self.state()["nested/book.pdf"]
        self.assertEqual(entry["method"], "chapter")
        self.assertEqual(len(entry["outputs"]), 3)

    def test_split_settings_change_does_not_reextract(self):
        self.pdf(bookmarks=False)
        self.assertEqual(self.run_pipeline(), 0)
        with patch.object(pymupdf.Page, "get_text", side_effect=AssertionError("re-extracted")):
            self.assertEqual(self.run_pipeline("--min-section-chars", "3000"), 0)
        self.assertEqual(len(self.state()["nested/book.pdf"]["outputs"]), 1)

    def test_bad_pdf_does_not_stop_other_books(self):
        (self.source / "bad.pdf").write_bytes(b"Not a PDF")
        self.pdf()
        self.assertEqual(self.run_pipeline(), 1)
        self.assertTrue(self.state()["nested/book.pdf"]["split"])
        self.assertIn("error", self.state()["bad.pdf"])

if __name__ == "__main__":
    unittest.main()

"""The text-layer converter: what it converts, what it caches, what it refuses.

Refusal matters as much as conversion here. The orchestrator only reaches the
OCR fallback when this route reports failure, so a scanned page that produced
an empty ``.tex`` instead of an error would silently lose a whole book.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf

from pdfconv.checkpoint import CheckpointStore
from pdfconv.traditional import runner as R

PROSE = ("The probability that the sum of two fair dice equals seven is one "
         "sixth. This paragraph exists so the converter has real prose to band, "
         "group, escape and emit as LaTeX, rather than a single short line. ")


def write_text_pdf(path: Path, pages: int = 2, bookmarks=None) -> Path:
    """A born-digital PDF: real glyphs, so the text-layer route can read it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    document = pymupdf.open()
    for number in range(pages):
        page = document.new_page()
        page.insert_textbox(pymupdf.Rect(72, 72, 523, 720),
                            f"Page {number + 1}. " + PROSE * 5, fontsize=11)
    if bookmarks:
        document.set_toc(bookmarks)
    document.save(path)
    document.close()
    return path


def write_image_pdf(path: Path, pages: int = 1) -> Path:
    """A scanned PDF: pages with no text layer at all."""
    path.parent.mkdir(parents=True, exist_ok=True)
    document = pymupdf.open()
    for _ in range(pages):
        page = document.new_page()
        page.draw_rect(pymupdf.Rect(100, 100, 400, 400), fill=(0.6, 0.6, 0.6))
    document.save(path)
    document.close()
    return path


class TraditionalRunnerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.store = CheckpointStore(self.root / "tmp", "test", {})

    def entry(self, key="book.pdf", digest="a"):
        return self.store.document(key, {"sha256": digest})

    def convert(self, pdf, **kwargs):
        return R.convert_document(pdf, self.entry(pdf.name), **kwargs)

    def test_converts_a_text_layer_pdf_into_one_fragment_per_page(self):
        pdf = write_text_pdf(self.root / "book.pdf", pages=3)
        result, toc, title, author = self.convert(pdf)
        self.assertEqual(result.page_count, 3)
        self.assertEqual(toc, [])
        self.assertEqual(title, "book")
        self.assertTrue(any("probability" in page for page in result.pages))
        self.assertFalse(result.fatal_issues, result.fatal_issues)

    def test_the_renderer_produces_a_standalone_document(self):
        pdf = write_text_pdf(self.root / "book.pdf")
        result, _, title, author = self.convert(pdf)
        render = R.document_renderer(title, author)
        tex = render("1 Probability", "\n\n".join(result.pages), "book.pdf")
        self.assertIn(r"\documentclass", tex)
        self.assertIn(r"\begin{document}", tex)
        self.assertIn(r"\chapter{1 Probability}", tex)
        self.assertTrue(tex.rstrip().endswith(r"\end{document}"))

    def test_the_renderer_turns_page_separators_into_paragraph_breaks(self):
        render = R.document_renderer("t", "a")
        tex = render("", "one\n\f\ntwo", "book.pdf")
        self.assertNotIn("\f", tex)
        self.assertIn("one\n\ntwo", tex)

    def test_pages_are_cached_and_reused(self):
        pdf = write_text_pdf(self.root / "book.pdf", pages=3)
        first, *_ = self.convert(pdf)
        entry = self.entry(pdf.name)
        self.assertEqual(len(entry.data["pages"]), 3)

        # A second pass must not touch the layout analyser at all.
        with patch("pdfconv.traditional.layout.analyse",
                   side_effect=AssertionError("page reconverted")):
            with pymupdf.open(pdf) as doc:
                second = R.convert_pages(doc, entry)
        self.assertEqual(second.pages, first.pages)

    def test_an_interrupted_run_resumes_only_unfinished_pages_from_disk(self):
        pdf = write_text_pdf(self.root / "book.pdf", pages=3)
        entry = self.entry(pdf.name)
        real = R.V.RangeConverter.page

        def interrupt_after_first_page(converter, doc, index):
            if index == 1:
                raise KeyboardInterrupt()
            return real(converter, doc, index)

        with pymupdf.open(pdf) as doc:
            with patch.object(R.V.RangeConverter, "page", interrupt_after_first_page):
                with self.assertRaises(KeyboardInterrupt):
                    R.convert_pages(doc, entry)

        recovered_store = CheckpointStore(self.root / "tmp", "test", {})
        recovered = recovered_store.document(pdf.name, {"sha256": "a"})
        self.assertEqual(set(recovered.data["pages"]), {"0"})
        self.assertFalse(recovered.data["extracted"])
        first_page = recovered.cached_page(0)
        converted = []

        def track_pages(converter, doc, index):
            converted.append(index)
            return real(converter, doc, index)

        with pymupdf.open(pdf) as doc:
            with patch.object(R.V.RangeConverter, "page", track_pages):
                result = R.convert_pages(doc, recovered)
        self.assertEqual(converted, [1, 2])
        self.assertEqual(result.pages[0], first_page)
        self.assertTrue(recovered.data["extracted"])
        for number, page in enumerate(result.pages, start=1):
            self.assertIn(f"Page {number}.", page)

    def test_a_corrupt_cached_page_is_converted_again(self):
        pdf = write_text_pdf(self.root / "book.pdf", pages=2)
        first, *_ = self.convert(pdf)
        entry = self.entry(pdf.name)
        entry.page_path(1).write_text("tampered", encoding="utf-8")
        with pymupdf.open(pdf) as doc:
            second = R.convert_pages(doc, entry)
        self.assertEqual(second.pages[1], first.pages[1])

    def test_each_page_holds_only_its_own_text(self):
        # The splitter maps a bookmark on page N to where page N's text starts,
        # so a page's slice must not contain a neighbour's prose.
        pdf = write_text_pdf(self.root / "book.pdf", pages=3)
        result, *_ = self.convert(pdf)
        for number, page in enumerate(result.pages, start=1):
            self.assertIn(f"Page {number}.", page)
            for other in (1, 2, 3):
                if other != number:
                    self.assertNotIn(f"Page {other}.", page)

    def test_refuses_a_pdf_without_a_text_layer(self):
        pdf = write_image_pdf(self.root / "scan.pdf", pages=3)
        with self.assertRaisesRegex(R.TraditionalError, "no usable text layer"):
            self.convert(pdf)

    def test_refuses_a_file_that_is_not_a_pdf(self):
        broken = self.root / "broken.pdf"
        broken.write_bytes(b"this is not a PDF at all")
        with self.assertRaises(R.TraditionalError):
            self.convert(broken)

    def test_refuses_a_document_with_no_pages(self):
        # PyMuPDF will not write a zero-page file, so the document it would
        # hand back is faked; the guard exists for PDFs produced elsewhere.
        empty = write_text_pdf(self.root / "empty.pdf")

        class NoPages:
            needs_pass = False

            def __len__(self):
                return 0

            def close(self):
                pass

        with patch.object(pymupdf, "open", return_value=NoPages()), \
             self.assertRaisesRegex(R.TraditionalError, "no pages"):
            self.convert(empty)

    def test_refuses_a_password_protected_pdf(self):
        protected = self.root / "locked.pdf"
        document = pymupdf.open()
        page = document.new_page()
        page.insert_textbox(pymupdf.Rect(72, 72, 523, 720), PROSE * 5, fontsize=11)
        document.save(protected, encryption=pymupdf.PDF_ENCRYPT_AES_256,
                      owner_pw="owner", user_pw="user")
        document.close()
        with self.assertRaisesRegex(R.TraditionalError, "password"):
            self.convert(protected)

    def test_a_page_that_raises_is_recorded_and_the_rest_still_convert(self):
        pdf = write_text_pdf(self.root / "book.pdf", pages=4)
        real = R.V.RangeConverter.page
        calls = {"n": 0}

        def sometimes_fail(self, doc, pno):
            calls["n"] += 1
            if pno == 1:
                raise RuntimeError("simulated layout failure")
            return real(self, doc, pno)

        with patch.object(R.V.RangeConverter, "page", sometimes_fail):
            result, *_ = self.convert(pdf)
        self.assertEqual(result.page_errors, 1)
        self.assertEqual(result.page_count, 4)

    def test_too_many_failing_pages_fails_the_document(self):
        pdf = write_text_pdf(self.root / "book.pdf", pages=4)
        with patch.object(R.V.RangeConverter, "page",
                          side_effect=RuntimeError("simulated layout failure")):
            with self.assertRaisesRegex(R.TraditionalError, "failed to convert"):
                self.convert(pdf)

    def test_the_document_title_comes_from_metadata_then_the_filename(self):
        pdf = write_text_pdf(self.root / "named.pdf")
        with pymupdf.open(pdf) as document:
            self.assertEqual(R.document_title(document, pdf), "named")
            document.set_metadata({"title": "A First Course"})
            self.assertEqual(R.document_title(document, pdf), "A First Course")

    def test_generated_latex_passes_the_projects_own_checker(self):
        pdf = write_text_pdf(self.root / "book.pdf", pages=2)
        result, *_ = self.convert(pdf)
        for issue in R.FATAL_ISSUES:
            self.assertEqual(result.issues.get(issue, 0), 0, issue)

    def test_the_fingerprint_changes_with_the_pdf(self):
        self.assertNotEqual(R.fingerprint("a"), R.fingerprint("b"))
        self.assertEqual(R.fingerprint("a")["converter"], "traditional")


if __name__ == "__main__":
    unittest.main()

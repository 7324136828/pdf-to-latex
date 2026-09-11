"""The recursive walk: discovery, chunking, fallback and error isolation.

The two converters are stubbed. What is under test is the policy around them --
which route runs, when the second is reached, what is skipped, how the chapters
land, and what one failure does to the rest of the run.
"""

import contextlib
import io
import json

import pymupdf
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pdfconv import device as D
from pdfconv import orchestrator as O
from pdfconv.pdf2tex import runner as OCR
from pdfconv.traditional.runner import TraditionalError

#: Long enough that the splitter is working on realistic input.
PAGE_ONE = "Chapter 1 Beginnings\n" + ("Body of the first chapter. " * 20)
PAGE_TWO = "Chapter 2 Endings\n" + ("Body of the second chapter. " * 20)


class StubTraditionalResult:
    def __init__(self, pages):
        self.pages = pages
        self.page_errors = 0

    @property
    def page_count(self):
        return len(self.pages)

    @property
    def characters(self):
        return sum(len(p) for p in self.pages)


class StubPdf2TexPages:
    def __init__(self, pages):
        self.pages = pages
        self.toc = []
        self.device = "cpu"
        self.backend = "cpu"

    @property
    def characters(self):
        return sum(len(p) for p in self.pages)


class StubPdf2TexConverter:
    """Counts how often the OCR model would have been constructed."""

    instances = 0

    def __init__(self, *args, fail_for=(), **kwargs):
        type(self).instances += 1
        self.converted = []
        self.device = args[0] if args else "cpu"
        self.dpi = kwargs.get("dpi", 300)
        self.fail_for = fail_for

    def fingerprint(self, digest):
        return {"sha256": digest, "converter": "pdf2tex", "mode": "math",
                "version": OCR.EXTRACTION_VERSION, "dpi": self.dpi,
                "backend": self.device, "pymupdf": pymupdf.VersionBind}

    def convert(self, pdf, entry):
        self.converted.append(Path(pdf).name)
        if Path(pdf).name in self.fail_for:
            raise RuntimeError("OCR could not read this document either")
        return StubPdf2TexPages([PAGE_ONE, PAGE_TWO])

    def release(self):
        pass


class OrchestratorTestCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source = self.root / "input"
        self.destination = self.root / "output"
        self.source.mkdir()
        StubPdf2TexConverter.instances = 0
        # Never let the host's real GPU decide what these tests observe.
        choice = D.DeviceChoice("cpu", "test", D.GpuInfo(), D.TorchStatus())
        patcher = patch.object(D, "resolve_device", return_value=choice)
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_pdf(self, relative: str, body: bytes = b"%PDF-1.4 stub") -> Path:
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return path

    def options(self, **overrides) -> O.Options:
        base = dict(source=self.source, destination=self.destination,
                    work_dir=self.root / "tmp", model_dir=self.root / "models",
                    device="cpu", min_section_chars=0)
        base.update(overrides)
        return O.Options(**base)

    def run_converter(self, options, traditional, fail_for=()):
        """Run the walk with both converters stubbed; -> (summary, ocr stub, log)."""
        holder = {}

        def factory(*args, **kwargs):
            holder["converter"] = StubPdf2TexConverter(*args, fail_for=fail_for, **kwargs)
            return holder["converter"]

        with patch("pdfconv.traditional.runner.convert_document",
                   side_effect=traditional), \
             patch("pdfconv.pdf2tex.runner.Pdf2TexConverter", side_effect=factory), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            summary = O.Converter(options).run()
        return summary, holder.get("converter"), out.getvalue()

    # -- stub converters -----------------------------------------------------

    @staticmethod
    def traditional_ok(pdf, entry, **kwargs):
        pages = [r"\section{Chapter 1 Beginnings}" + "\n" + PAGE_ONE.split("\n", 1)[1],
                 r"\section{Chapter 2 Endings}" + "\n" + PAGE_TWO.split("\n", 1)[1]]
        return StubTraditionalResult(pages), [], "Book", "Author"

    @staticmethod
    def traditional_fails(pdf, entry, **kwargs):
        raise TraditionalError("no usable text layer")


class DiscoveryTests(OrchestratorTestCase):
    def test_finds_pdfs_recursively_in_a_stable_order(self):
        for name in ("b.pdf", "math/a.pdf", "math/deep/c.pdf"):
            self.make_pdf(name)
        found = [p.relative_to(self.source).as_posix()
                 for p in O.find_pdfs(self.source)]
        self.assertEqual(found, ["b.pdf", "math/a.pdf", "math/deep/c.pdf"])

    def test_extension_matching_ignores_case_and_other_files(self):
        self.make_pdf("upper.PDF")
        self.make_pdf("mixed.Pdf")
        (self.source / "notes.txt").write_text("no", encoding="utf-8")
        found = {p.name for p in O.find_pdfs(self.source)}
        self.assertEqual(found, {"upper.PDF", "mixed.Pdf"})

    def test_skips_apple_double_sidecars_and_the_output_tree(self):
        self.make_pdf("real.pdf")
        self.make_pdf("._real.pdf")
        inside = self.source / "output"
        self.make_pdf("output/generated.pdf")
        found = {p.name for p in O.find_pdfs(self.source, exclude=(inside,))}
        self.assertEqual(found, {"real.pdf"})

    def test_case_aliases_fail_before_any_conversion(self):
        # These can coexist on Linux but would overwrite output on Windows.
        with patch.object(O, "find_pdfs", return_value=[self.source / "book.pdf",
                                                       self.source / "BOOK.PDF"]):
            with self.assertRaisesRegex(RuntimeError, "filename collision"):
                self.run_converter(self.options(), self.traditional_ok)
        self.assertFalse(self.destination.exists())

    def test_sanitized_names_keep_distinct_outputs(self):
        self.make_pdf("book  name.pdf")
        self.make_pdf("book name.pdf")
        summary, _, _ = self.run_converter(self.options(), self.traditional_ok)
        files = [row["file"] for outcome in summary.outcomes for row in outcome.outputs]
        self.assertEqual(len(set(files)), 4)

    def test_each_route_gets_its_own_checkpoint_key(self):
        self.assertEqual(O.entry_key(Path("math/b.pdf"), "traditional"),
                         "math/b.pdf#traditional")
        self.assertNotEqual(O.entry_key(Path("b.pdf"), "traditional"),
                            O.entry_key(Path("b.pdf"), "pdf2tex"))


class ChunkingTests(OrchestratorTestCase):
    def test_one_pdf_becomes_one_file_per_chapter_mirroring_the_tree(self):
        self.make_pdf("papers/physics/paper.pdf")
        summary, _, _ = self.run_converter(self.options(), self.traditional_ok)
        outcome = summary.outcomes[0]
        self.assertEqual(outcome.status, "traditional")
        self.assertEqual(len(outcome.outputs), 2)
        for row in outcome.outputs:
            path = self.destination / row["file"]
            self.assertTrue(path.is_file())
            self.assertEqual(path.parent,
                             self.destination / "papers" / "physics")
            self.assertEqual(path.suffix, ".tex")
        self.assertEqual(summary.chapters, 2)

    def test_chapter_files_are_numbered_and_named_after_the_heading(self):
        self.make_pdf("book.pdf")
        summary, _, _ = self.run_converter(self.options(), self.traditional_ok)
        names = [Path(row["file"]).name for row in summary.outcomes[0].outputs]
        self.assertTrue(names[0].startswith("book_000 - "))
        self.assertTrue(names[1].startswith("book_001 - "))
        self.assertIn("Beginnings", names[0])
        self.assertIn("Endings", names[1])

    def test_the_fallback_chunks_the_same_way(self):
        self.make_pdf("scan.pdf")
        summary, _, _ = self.run_converter(self.options(), self.traditional_fails)
        outcome = summary.outcomes[0]
        self.assertEqual(outcome.status, "pdf2tex")
        self.assertEqual(len(outcome.outputs), 2)
        self.assertTrue(Path(outcome.outputs[0]["file"]).name.startswith("scan_000 - "))

    def test_a_catalog_of_every_chapter_is_written(self):
        self.make_pdf("book.pdf")
        self.run_converter(self.options(), self.traditional_ok)
        catalog = self.destination / "sections_catalog.tsv"
        self.assertTrue(catalog.is_file())
        rows = catalog.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(rows), 3)                 # header plus two chapters
        self.assertIn("traditional", rows[1])
        self.assertEqual(rows[1].split("\t")[0], "book.pdf")


class ConversionPolicyTests(OrchestratorTestCase):
    def test_discard_stops_before_the_next_document(self):
        self.make_pdf("a.pdf")
        self.make_pdf("b.pdf")
        summary, converter, log = self.run_converter(
            self.options(cancel_requested=lambda: True), self.traditional_ok
        )
        self.assertEqual(summary.outcomes, [])
        self.assertIsNone(converter)
        self.assertIn("discarded by user", log)

    def test_traditional_success_does_not_reach_the_fallback(self):
        self.make_pdf("a.pdf")
        summary, converter, log = self.run_converter(self.options(),
                                                     self.traditional_ok)
        self.assertEqual(summary.count("traditional"), 1)
        self.assertIsNone(converter)
        self.assertEqual(StubPdf2TexConverter.instances, 0)
        self.assertIn("traditional: SUCCESS", log)

    def test_traditional_failure_falls_back_to_pdf2tex(self):
        self.make_pdf("a.pdf")
        summary, converter, log = self.run_converter(self.options(),
                                                     self.traditional_fails)
        self.assertEqual(summary.count("pdf2tex"), 1)
        self.assertEqual(converter.converted, ["a.pdf"])
        self.assertIn("traditional: FAILED", log)
        self.assertIn("Falling back to pdf2tex", log)
        self.assertIn("pdf2tex: SUCCESS", log)

    def test_the_ocr_model_is_built_once_for_every_fallback(self):
        for name in ("a.pdf", "b.pdf", "c.pdf"):
            self.make_pdf(name)
        summary, converter, _ = self.run_converter(self.options(),
                                                   self.traditional_fails)
        self.assertEqual(summary.count("pdf2tex"), 3)
        self.assertEqual(StubPdf2TexConverter.instances, 1)
        self.assertEqual(converter.converted, ["a.pdf", "b.pdf", "c.pdf"])

    def test_one_document_failing_both_routes_does_not_stop_the_others(self):
        for name in ("a.pdf", "broken.pdf", "z.pdf"):
            self.make_pdf(name)
        summary, _, log = self.run_converter(self.options(), self.traditional_fails,
                                             fail_for=("broken.pdf",))
        self.assertEqual(summary.count("pdf2tex"), 2)
        self.assertEqual(summary.count("failed"), 1)
        self.assertEqual([o.relative.name for o in summary.failures], ["broken.pdf"])
        self.assertIn("pdf2tex: FAILED", log)
        self.assertIn("Failed with both methods:     1", log)

    def test_a_failure_records_both_reasons(self):
        self.make_pdf("broken.pdf")
        summary, _, _ = self.run_converter(self.options(), self.traditional_fails,
                                           fail_for=("broken.pdf",))
        outcome = summary.failures[0]
        self.assertIn("no usable text layer", outcome.traditional_error)
        self.assertIn("OCR could not read", outcome.pdf2tex_error)
        state = json.loads((self.options().work_dir / "convert_pdfs.json").read_text(
            encoding="utf-8"))["documents"]
        self.assertIn("no usable text layer", state["broken.pdf#traditional"]["error"])
        self.assertIn("OCR could not read", state["broken.pdf#pdf2tex"]["error"])

    def test_a_completed_document_is_skipped_and_force_reprocesses_it(self):
        self.make_pdf("a.pdf")
        summary, _, _ = self.run_converter(self.options(), self.traditional_ok)
        self.assertEqual(summary.count("traditional"), 1)

        summary, _, log = self.run_converter(self.options(), self.traditional_ok)
        self.assertEqual(summary.count("skipped"), 1)
        self.assertIn("already published by traditional", log)

        summary, _, _ = self.run_converter(self.options(force=True),
                                           self.traditional_ok)
        self.assertEqual(summary.count("traditional"), 1)

    def test_a_document_finished_by_the_fallback_is_also_skipped(self):
        self.make_pdf("scan.pdf")
        self.run_converter(self.options(), self.traditional_fails)
        StubPdf2TexConverter.instances = 0
        summary, converter, log = self.run_converter(self.options(),
                                                     self.traditional_fails)
        self.assertEqual(summary.count("skipped"), 1)
        self.assertIn("already published by pdf2tex", log)
        # The model must not be loaded just to discover there is nothing to do.
        self.assertEqual(StubPdf2TexConverter.instances, 0)

    def test_a_deleted_chapter_file_is_produced_again(self):
        self.make_pdf("a.pdf")
        summary, _, _ = self.run_converter(self.options(), self.traditional_ok)
        (self.destination / summary.outcomes[0].outputs[0]["file"]).unlink()
        summary, _, _ = self.run_converter(self.options(), self.traditional_ok)
        self.assertEqual(summary.count("traditional"), 1)

    def test_a_changed_pdf_is_converted_again(self):
        self.make_pdf("a.pdf")
        self.run_converter(self.options(), self.traditional_ok)
        self.make_pdf("a.pdf", body=b"%PDF-1.4 different bytes")
        summary, _, _ = self.run_converter(self.options(), self.traditional_ok)
        self.assertEqual(summary.count("traditional"), 1)

    def test_traditional_only_never_builds_the_ocr_converter(self):
        self.make_pdf("a.pdf")
        summary, converter, _ = self.run_converter(
            self.options(traditional_only=True), self.traditional_fails)
        self.assertEqual(summary.count("failed"), 1)
        self.assertEqual(StubPdf2TexConverter.instances, 0)
        self.assertIsNone(converter)

    def test_pdf2tex_only_skips_the_text_layer_route(self):
        self.make_pdf("a.pdf")

        def unexpected(*args, **kwargs):
            raise AssertionError("the traditional route should not have run")

        summary, converter, _ = self.run_converter(
            self.options(pdf2tex_only=True), unexpected)
        self.assertEqual(summary.count("pdf2tex"), 1)
        self.assertEqual(converter.converted, ["a.pdf"])

    def test_explicit_route_replaces_previous_route_outputs_and_catalog(self):
        self.make_pdf("a.pdf")

        def unsplit_traditional(pdf, entry, **kwargs):
            return StubTraditionalResult([PAGE_ONE, PAGE_TWO]), [], "Book", "Author"

        first, _, _ = self.run_converter(self.options(), unsplit_traditional)
        old_path = self.destination / first.outcomes[0].outputs[0]["file"]
        self.assertIn("Complete Text", old_path.name)
        second, _, _ = self.run_converter(self.options(pdf2tex_only=True),
                                          self.traditional_ok)
        self.assertEqual(second.count("pdf2tex"), 1)
        self.assertFalse(old_path.exists())
        catalog = (self.destination / "sections_catalog.tsv").read_text(encoding="utf-8")
        self.assertEqual(len(catalog.splitlines()), 3)
        self.assertNotIn("traditional", catalog)
        third, converter, _ = self.run_converter(self.options(traditional_only=True),
                                                self.traditional_ok)
        self.assertEqual(third.count("traditional"), 1)
        self.assertIsNone(converter)
        catalog = (self.destination / "sections_catalog.tsv").read_text(encoding="utf-8")
        self.assertEqual(len(catalog.splitlines()), 3)
        self.assertNotIn("pdf2tex", catalog)

    def test_incomplete_publication_is_resumed_even_if_old_outputs_exist(self):
        self.make_pdf("a.pdf")
        self.run_converter(self.options(), self.traditional_ok)
        path = self.options().work_dir / "convert_pdfs.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        entry = state["documents"]["a.pdf#traditional"]
        entry["split"] = False
        entry["pending_outputs"] = entry["outputs"]
        path.write_text(json.dumps(state), encoding="utf-8")
        summary, _, _ = self.run_converter(self.options(), self.traditional_ok)
        self.assertEqual(summary.count("traditional"), 1)
        state = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(state["documents"]["a.pdf#traditional"]["split"])
        self.assertNotIn("pending_outputs", state["documents"]["a.pdf#traditional"])

    def test_changed_extraction_version_invalidates_completed_traditional(self):
        self.make_pdf("a.pdf")
        self.run_converter(self.options(), self.traditional_ok)
        with patch("pdfconv.traditional.runner.EXTRACTION_VERSION", 999):
            summary, _, _ = self.run_converter(self.options(), self.traditional_ok)
        self.assertEqual(summary.count("traditional"), 1)

    def test_changed_ocr_dpi_invalidates_completed_outputs(self):
        self.make_pdf("a.pdf")
        self.run_converter(self.options(pdf2tex_only=True), self.traditional_fails)
        summary, converter, _ = self.run_converter(
            self.options(pdf2tex_only=True, dpi=144), self.traditional_fails)
        self.assertEqual(summary.count("pdf2tex"), 1)
        self.assertEqual(converter.dpi, 144)

    def test_cuda_fallback_uses_shared_chapter_split(self):
        self.make_pdf("a.pdf")
        choice = D.DeviceChoice("cuda", "test CUDA", D.GpuInfo(), D.TorchStatus())
        with patch.object(D, "resolve_device", return_value=choice):
            summary, converter, _ = self.run_converter(self.options(device="cuda"),
                                                       self.traditional_fails)
        self.assertEqual(converter.device, "cuda")
        self.assertEqual(len(summary.outcomes[0].outputs), 2)
        self.assertTrue(all(row["file"].endswith(".tex")
                            for row in summary.outcomes[0].outputs))

    def test_verbose_records_a_traceback(self):
        self.make_pdf("a.pdf")
        summary, _, _ = self.run_converter(self.options(verbose=True),
                                           self.traditional_fails)
        self.assertIn("Traceback", summary.outcomes[0].traditional_error)


if __name__ == "__main__":
    unittest.main()

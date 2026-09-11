"""Walk ``input/`` recursively, convert every PDF, mirror the tree in ``output/``.

Each PDF becomes one LaTeX file per chapter, in a folder mirroring its place
under the input root:

    input/book.pdf         ->  output/book_000 - Front Matter.tex
                               output/book_001 - 1 Probability.tex
    input/math/notes.pdf   ->  output/math/notes_000 - Complete Text.tex

Both converters produce the same thing -- one string per page -- and hand it to
``pdfconv.chapter_split``, so a book is chunked identically whichever route
read it. The text-layer converter runs first because it is exact and costs
seconds; the OCR converter runs only for the documents it could not read,
because it costs minutes per document and needs a GPU to be bearable.

Every converted page is checkpointed, so an interrupted run is continued rather
than repeated. One PDF failing both routes is recorded and the walk goes on -- a
single unreadable file in a library of forty must not decide the run.
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import chapter_split, device as D
from .checkpoint import CheckpointStore, exclusive_lock, valid_file
from .fsutil import file_digest
from .paths import INPUT_DIR, MODEL_DIR, OUTPUT_DIR, WORK_DIR

RULE = "=" * 60

#: The checkpoint entry key is the document plus the route, so a document that
#: fell back to OCR does not throw away the other route's record.
ROUTES = ("traditional", "pdf2tex")


def entry_key(relative: Path, route: str) -> str:
    return f"{relative.as_posix()}#{route}"


def find_pdfs(root: Path, *, exclude: tuple[Path, ...] = ()) -> list[Path]:
    """-> every PDF under ``root``, in a stable order, whatever the case.

    ``rglob("*.pdf")`` is case-sensitive on Linux, and the extension in this
    project's own corpus is not consistent, so the filter is explicit. Output
    and cache folders are skipped: they may legitimately live inside a tree
    someone points ``--input`` at.
    """
    root = root.resolve()
    excluded = tuple(p.resolve() for p in exclude)
    found = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.casefold() != ".pdf":
            continue
        if path.name.startswith("._"):       # macOS AppleDouble resource forks
            continue
        if any(path.is_relative_to(skip) for skip in excluded):
            continue
        found.append(path)
    return sorted(found, key=lambda p: p.relative_to(root).as_posix().casefold())


@dataclass
class FileOutcome:
    """One PDF's journey through the two converters."""

    pdf: Path
    relative: Path
    status: str = "pending"          # skipped | traditional | pdf2tex | failed
    traditional_error: str = ""
    pdf2tex_error: str = ""
    detail: str = ""
    outputs: list[dict] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return self.status == "failed"


@dataclass
class RunSummary:
    """Counts and failures for the whole walk."""

    outcomes: list[FileOutcome] = field(default_factory=list)

    def count(self, status: str) -> int:
        return sum(1 for o in self.outcomes if o.status == status)

    @property
    def chapters(self) -> int:
        return sum(len(o.outputs) for o in self.outcomes)

    @property
    def failures(self) -> list[FileOutcome]:
        return [o for o in self.outcomes if o.failed]


@dataclass
class Options:
    """Everything the walk needs, resolved from the command line."""

    source: Path = INPUT_DIR
    destination: Path = OUTPUT_DIR
    work_dir: Path = WORK_DIR
    model_dir: Path = MODEL_DIR
    device: str = "auto"
    dpi: int = 300
    min_section_chars: int = chapter_split.DEFAULT_MIN_SECTION_CHARS
    force: bool = False
    traditional_only: bool = False
    pdf2tex_only: bool = False
    verbose: bool = False
    cancel_requested: Callable[[], bool] | None = None


class Converter:
    """Runs both routes over one tree, holding the OCR model open between files."""

    def __init__(self, options: Options):
        self.options = options
        self.choice = D.resolve_device(options.device)
        self.settings = chapter_split.settings_for(options.min_section_chars,
                                                   {"format": "latex"})
        self.store: CheckpointStore | None = None
        self._pdf2tex = None

    # -- the OCR converter, created on the first document that needs it -------

    def pdf2tex(self):
        if self._pdf2tex is None:
            from .pdf2tex.runner import Pdf2TexConverter

            self._pdf2tex = Pdf2TexConverter(
                "cuda" if self.choice.is_cuda else "cpu",
                model_dir=self.options.model_dir, dpi=self.options.dpi,
                verbose=self.options.verbose)
        return self._pdf2tex

    # -- one document ---------------------------------------------------------

    def _error(self, error: BaseException) -> str:
        if self.options.verbose:
            return "".join(traceback.format_exception(type(error), error,
                                                      error.__traceback__)).strip()
        return f"{type(error).__name__}: {error}"

    def completed_route(self, relative: Path, digest: str) -> tuple[str, dict] | None:
        """-> the route that already published this document, if any.

        Check the selected route, extraction fingerprint, split settings, and
        output hashes. The resolved CPU/CUDA device lets the OCR fingerprint
        be checked without loading a model merely to discover it is not needed.
        """
        from .traditional.runner import fingerprint as traditional_fingerprint
        from .pdf2tex.runner import fingerprint_matches as ocr_fingerprint_matches

        for route in ROUTES:
            if (route == "traditional" and self.options.pdf2tex_only
                    or route == "pdf2tex" and self.options.traditional_only):
                continue
            data = self.store.peek(entry_key(relative, route))
            if not data or not data.get("split") or not data.get("outputs"):
                continue
            if data.get("error") or data.get("pending_outputs"):
                continue
            if data.get("fingerprint", {}).get("sha256") != digest:
                continue
            if (route == "traditional"
                    and data["fingerprint"] != traditional_fingerprint(digest)):
                continue
            if (route == "pdf2tex" and not ocr_fingerprint_matches(
                    data["fingerprint"], digest, device=self.choice.device,
                    dpi=self.options.dpi)):
                continue
            if data.get("split_settings") != self.settings:
                continue
            if all(valid_file(self.options.destination / row["file"], row["sha256"])
                   for row in data["outputs"]):
                return route, data
        return None

    def _publish(self, entry, relative: Path, pages: list[str], toc: list,
                 render, route: str, detect) -> list[dict]:
        entry.data["converter"] = route
        entry.data["source"] = relative.as_posix()
        split = chapter_split.plan(relative, pages, toc, render=render,
                                   minimum=self.options.min_section_chars,
                                   detect=detect)
        # Both routes publish into the same namespace. Transfer old publication
        # records so switching routes also cleans stale chapters and can resume
        # after an interrupted write, while retaining each route's page cache.
        superseded = []
        for other_route in ROUTES:
            if other_route == route:
                continue
            previous = self.store.peek(entry_key(relative, other_route))
            if previous is None:
                continue
            entry.data.setdefault("pending_outputs", []).extend(
                previous.get("outputs", []) + previous.get("pending_outputs", []))
            previous["split"] = False
            superseded.append(previous)
        outputs = chapter_split.publish(entry, split, self.options.destination,
                                        settings=self.settings)
        for previous in superseded:
            previous["outputs"] = []
            previous.pop("pending_outputs", None)
        self.store.commit()
        chapter_split.write_catalog(self.store.documents(), self.options.destination)
        return outputs

    def _try_traditional(self, outcome: FileOutcome, digest: str) -> bool:
        from .traditional import runner as traditional

        entry = self.store.document(
            entry_key(outcome.relative, "traditional"),
            traditional.fingerprint(digest),
            force=self.options.force)
        convert_args = {"verbose": self.options.verbose}
        if self.options.cancel_requested is not None:
            convert_args["cancel_requested"] = self.options.cancel_requested
        result, toc, title, author = traditional.convert_document(
            outcome.pdf, entry, **convert_args)
        outputs = self._publish(entry, outcome.relative, result.pages, toc,
                                traditional.document_renderer(title, author),
                                "traditional", chapter_split.headings_from_latex)
        outcome.status = "traditional"
        outcome.outputs = outputs
        outcome.detail = (f"{result.page_count} pages, {len(outputs)} chapters, "
                          f"{result.characters} bytes")
        if result.page_errors:
            outcome.detail += f", {result.page_errors} page errors"
        return True

    def _try_pdf2tex(self, outcome: FileOutcome, digest: str) -> bool:
        from .pdf2tex import runner as pdf2tex

        converter = self.pdf2tex()
        entry = self.store.document(
            entry_key(outcome.relative, "pdf2tex"),
            converter.fingerprint(digest), force=self.options.force)
        if self.options.cancel_requested is None:
            result = converter.convert(outcome.pdf, entry)
        else:
            result = converter.convert(
                outcome.pdf, entry,
                cancel_requested=self.options.cancel_requested,
            )
        outputs = self._publish(entry, outcome.relative, result.pages, result.toc,
                                pdf2tex.render, "pdf2tex",
                                chapter_split.headings_from_text)
        outcome.status = "pdf2tex"
        outcome.outputs = outputs
        outcome.detail = (f"{len(result.pages)} pages, {len(outputs)} chapters, "
                          f"{result.characters} bytes, device {result.device} "
                          f"({result.backend})")
        return True

    def _record_failure(self, relative: Path, route: str, message: str) -> None:
        data = self.store.peek(entry_key(relative, route))
        if data is not None:
            data.update(error=message, split=False)
            self.store.commit()

    def convert_one(self, outcome: FileOutcome) -> FileOutcome:
        options = self.options
        digest = file_digest(outcome.pdf)

        if not options.force:
            done = self.completed_route(outcome.relative, digest)
            if done is not None:
                route, data = done
                outcome.status = "skipped"
                outcome.outputs = data["outputs"]
                outcome.detail = (f"{len(data['outputs'])} chapters already "
                                  f"published by {route}; --force reprocesses it")
                return outcome

        if not options.pdf2tex_only:
            try:
                self._try_traditional(outcome, digest)
                return outcome
            except KeyboardInterrupt:
                raise
            except Exception as error:
                if options.cancel_requested and options.cancel_requested():
                    raise
                outcome.traditional_error = self._error(error)
                self._record_failure(outcome.relative, "traditional", outcome.traditional_error)

        if options.traditional_only:
            outcome.status = "failed"
            return outcome

        try:
            self._try_pdf2tex(outcome, digest)
            return outcome
        except KeyboardInterrupt:
            raise
        except Exception as error:
            if options.cancel_requested and options.cancel_requested():
                raise
            outcome.pdf2tex_error = self._error(error)
            self._record_failure(outcome.relative, "pdf2tex", outcome.pdf2tex_error)
            outcome.status = "failed"
            return outcome

    # -- the walk -------------------------------------------------------------

    def run(self) -> RunSummary:
        options = self.options
        source = options.source.resolve()
        destination = options.destination.resolve()
        pdfs = find_pdfs(source, exclude=(destination, options.work_dir))
        prefixes = set()
        for pdf in pdfs:
            relative = pdf.relative_to(source)
            prefix = (relative.parent / chapter_split.flat_section_filename(
                relative.stem, 0, "")).as_posix().casefold()
            if prefix in prefixes:
                raise RuntimeError(
                    f"Output filename collision; rename one of the PDFs: {relative}")
            prefixes.add(prefix)

        print(RULE)
        print("PDF -> LaTeX Conversion")
        print(RULE)
        print()
        print(f"Input:  {source}")
        print(f"Output: {destination}")
        print(f"Cache:  {options.work_dir}")
        print()
        print(f"Detected {len(pdfs)} PDF file{'' if len(pdfs) == 1 else 's'}.")
        print()
        print("Device:")
        for line in D.describe_choice(self.choice):
            print(line)
        if options.traditional_only:
            print("  pdf2tex fallback: disabled (--traditional-only)")
        if options.pdf2tex_only:
            print("  traditional route: disabled (--pdf2tex-only)")
        print()

        summary = RunSummary()
        # One lock for the whole run: two runs sharing a page cache would
        # overwrite each other's pages.
        with exclusive_lock(options.work_dir / "run.lock"):
            self.store = CheckpointStore(
                options.work_dir, "convert_pdfs",
                {"source": str(source), "destination": str(destination)})
            for index, pdf in enumerate(pdfs, start=1):
                if options.cancel_requested and options.cancel_requested():
                    print("Conversion discarded by user.")
                    break
                relative = pdf.relative_to(source)
                outcome = FileOutcome(pdf=pdf, relative=relative)
                print(f"[{index}/{len(pdfs)}] {relative}", flush=True)
                try:
                    self.convert_one(outcome)
                except KeyboardInterrupt:
                    print("\nInterrupted. Re-run the same command to continue "
                          "from the last completed page.")
                    summary.outcomes.append(outcome)
                    self.report(summary, destination)
                    raise
                self._print_outcome(outcome)
                summary.outcomes.append(outcome)
                if options.cancel_requested and options.cancel_requested():
                    print("Conversion discarded by user.")
                    break
            chapter_split.write_catalog(self.store.documents(), destination)

        self.report(summary, destination)
        return summary

    def _print_outcome(self, outcome: FileOutcome) -> None:
        if outcome.status == "skipped":
            print(f"  skipped: {outcome.detail}")
            print(flush=True)
            return
        if outcome.traditional_error:
            print("  traditional: FAILED")
            for line in outcome.traditional_error.splitlines():
                print(f"    {line}")
            if not self.options.traditional_only:
                print("  Falling back to pdf2tex...")
        elif outcome.status == "traditional":
            print(f"  traditional: SUCCESS ({outcome.detail})")
        if outcome.status == "pdf2tex":
            print(f"  pdf2tex: SUCCESS ({outcome.detail})")
        elif outcome.pdf2tex_error:
            print("  pdf2tex: FAILED")
            for line in outcome.pdf2tex_error.splitlines():
                print(f"    {line}")
        for row in outcome.outputs:
            print(f"  output: {Path(row['file'])}")
        print(flush=True)

    def report(self, summary: RunSummary, destination: Path) -> None:
        print(RULE)
        print("Conversion Summary")
        print(RULE)
        print()
        print(f"PDF files discovered:      {len(summary.outcomes):4d}")
        print(f"Already completed/skipped: {summary.count('skipped'):4d}")
        print(f"Traditional successes:     {summary.count('traditional'):4d}")
        print(f"pdf2tex fallback successes:{summary.count('pdf2tex'):4d}")
        print(f"Failed with both methods:  {summary.count('failed'):4d}")
        print(f"Chapter files:             {summary.chapters:4d}")
        print()
        print("Output directory:")
        print(destination)
        print(f"Catalog: {destination / chapter_split.CATALOG_NAME}")
        if summary.failures:
            print()
            print("Failures:")
            for outcome in summary.failures:
                print(f"  {outcome.relative}")
                if outcome.traditional_error:
                    print(f"    traditional: {outcome.traditional_error.splitlines()[-1]}")
                if outcome.pdf2tex_error:
                    print(f"    pdf2tex:     {outcome.pdf2tex_error.splitlines()[-1]}")
            if not self.options.verbose:
                print("  (re-run with --verbose for full tracebacks)")

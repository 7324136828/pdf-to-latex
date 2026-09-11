"""Convert one arbitrary PDF with the text-layer pipeline, a page at a time.

The original ``convert.py`` drives one specific book: it reads chapter ranges
out of that book's outline and writes one ``.tex`` per chapter next to a
``main.tex`` scaffold.  The recursive converter needs the same machinery
applied to any PDF, producing a list of per-page LaTeX fragments that
``pdfconv.chapter_split`` then cuts into chapters -- the same shape the OCR
route produces, so both are chunked identically.

Each page is converted on its own, exactly as ``convert_range(doc, p, p, w)``
would, and cached under its own index.  Independence is what the splitter needs:
it maps a bookmark on page N to the character offset where page N's text starts,
and that is only true if page N's slice holds page N's text.  Carrying the
writer's open paragraph across the boundary would move text to whichever page
happened to close it, and the chapter cuts would drift with it.  Nothing is lost
by flushing: pages are rejoined with a separator the renderer turns into a
paragraph break, so a paragraph spanning a page break ends there either way.

Failure here is expected and useful: the text-layer route cannot read a scanned
page at all, and saying so quickly is what lets the orchestrator fall back to
OCR instead of writing an empty chapter.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pymupdf

from . import check as K
from . import convert as V
from .fontdecode import SourceEncodingError
from ..checkpoint import DocumentEntry

#: A page carrying fewer alphanumerics than this has no useful text layer.
#: The same threshold the OCR pipeline uses to spot an image-only page.
MIN_ALNUM_PER_PAGE = 40

#: How many pages to sample when judging the text layer of a long document.
SAMPLE_PAGES = 12

#: Below this, "conversion succeeded" would be a lie whatever the parser says.
MIN_BODY_CHARS = 200

#: Fraction of pages allowed to fail before the whole document is a failure.
MAX_PAGE_ERROR_RATE = 0.05

#: Recorded in the page cache for a page the layout analyser could not read.
ERROR_METHOD = "error"
PAGE_METHOD = "traditional"

#: Bumped when a change here would convert the same page differently.
EXTRACTION_VERSION = 3

#: Issues from ``check`` that stop LaTeX from compiling at all.  Everything
#: else it reports (unknown commands, stray control characters) is a quality
#: signal, and rejecting a whole book for one of those would send a perfectly
#: good text layer to the OCR fallback.
FATAL_ISSUES = (
    "unbalanced brace",
    "unclosed braces at EOF",
    "stray \\end",
    "mismatched environment",
    "unclosed environment",
    "odd $ count in paragraph",
    "left/right mismatch",
)


class TraditionalError(RuntimeError):
    """The text-layer route cannot convert this PDF; try the fallback."""


@dataclass
class TraditionalResult:
    """The per-page LaTeX this document produced, and how much to trust it."""

    pages: list[str]
    page_errors: int = 0
    issues: Counter = field(default_factory=Counter)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def characters(self) -> int:
        return sum(len(page) for page in self.pages)

    @property
    def fatal_issues(self) -> Counter:
        return Counter({k: v for k, v in self.issues.items()
                        if any(k.startswith(f) for f in FATAL_ISSUES)})


def text_layer_density(doc, sample: int = SAMPLE_PAGES) -> float:
    """-> mean alphanumerics per page over an evenly spaced sample.

    Sampling matters: front matter is often an image even in a born-digital
    book, so judging by page one alone sends good documents to the OCR path.
    """
    count = len(doc)
    if not count:
        return 0.0
    step = max(1, count // sample)
    indexes = list(range(0, count, step))[:sample]
    total = 0
    for index in indexes:
        try:
            total += sum(c.isalnum() for c in doc[index].get_text())
        except Exception:             # a damaged page is worth zero, not a crash
            continue
    return total / len(indexes)


def document_title(doc, pdf: Path) -> str:
    """Prefer the PDF's own title, fall back to the file name."""
    try:
        title = (doc.metadata or {}).get("title") or ""
    except Exception:
        title = ""
    title = re.sub(r"\s+", " ", title).strip()
    return title or pdf.stem


def build_preamble(title: str, author: str, edition: str = "") -> str:
    """Reuse the project's preamble, with this document's metadata in it."""
    return (V.PREAMBLE.replace("@TITLE@", V.esc_head(title))
                      .replace("@AUTHOR@", V.esc_head(author))
                      .replace("@EDITION@", V.esc_head(edition)))


def document_renderer(title: str, author: str):
    """-> a renderer that wraps one chapter as a standalone LaTeX document.

    The counterpart of ``pdf2tex.latex_output.latex_document``: the OCR route
    escapes recognised prose and needs fontspec, while everything here has
    already been escaped by ``emit`` and targets the project's own preamble.
    """
    preamble = build_preamble(title, author)

    def render(heading: str, body: str, source: str) -> str:
        # Page slices are joined with the splitter's separator; the reader wants
        # paragraphs, not form feeds.
        body = body.replace("\n\f\n", "\n\n")
        body = re.sub(r"\n{3,}", "\n\n", body).strip()
        parts = [preamble, "\n\\mainmatter\n"]
        if heading:
            parts.append("\n\\chapter{%s}\n" % V.esc_head(heading))
        parts.append("\n%% Source: %s\n\n" % V.esc_head(source))
        parts.append(body)
        parts.append("\n\n\\end{document}\n")
        return "".join(parts)

    return render


def convert_pages(doc, entry: DocumentEntry, *, verbose: bool = False,
                  cancel_requested: Callable[[], bool] | None = None) -> TraditionalResult:
    """Convert every page, reusing whatever the checkpoint already holds.

    -> one LaTeX fragment per page, in page order.
    """
    total = len(doc)
    pages = [""] * total
    errors = 0

    for index in range(total):
        if cancel_requested and cancel_requested():
            raise InterruptedError("Conversion discarded by user")
        cached = entry.cached_page(index)
        if cached is not None:
            pages[index] = cached
            record = entry.data["pages"].get(str(index), {})
            if record.get("method") == ERROR_METHOD:
                errors += 1
            continue
        entry.begin_extraction()
        writer = V.Writer()
        converter = V.RangeConverter(writer)
        method = PAGE_METHOD
        try:
            converter.page(doc, index)
            converter.finish()
        except SourceEncodingError as error:
            raise TraditionalError(str(error)) from error
        except Exception as error:
            method = ERROR_METHOD
            errors += 1
            if verbose:
                print(f"    page {index + 1}: {type(error).__name__}: {error}")
            # Keep whatever the page produced before it failed rather than
            # discarding a mostly-converted page.
            writer.para = []
        pages[index] = writer.out()
        entry.store_page(index, pages[index], method)
        if verbose and (index + 1) % 50 == 0:
            print(f"    page {index + 1}/{total}", flush=True)
    entry.finish_extraction()
    return TraditionalResult(pages=pages, page_errors=errors)


def convert_document(pdf: Path, entry: DocumentEntry, *,
                     verbose: bool = False,
                     cancel_requested: Callable[[], bool] | None = None
                     ) -> tuple[TraditionalResult, list, str, str]:
    """-> (result, outline, title, author), or raise ``TraditionalError``.

    The outline and metadata come back with the pages because the caller needs
    them to split the document and to write its preamble, and opening the PDF
    twice to ask again would be wasteful.
    """
    try:
        doc = pymupdf.open(pdf)
    except Exception as error:
        raise TraditionalError(f"PyMuPDF cannot open the file: {error}") from error
    try:
        if doc.needs_pass:
            raise TraditionalError("PDF is password protected")
        if not len(doc):
            raise TraditionalError("PDF contains no pages")

        density = text_layer_density(doc)
        if density < MIN_ALNUM_PER_PAGE:
            raise TraditionalError(
                "no usable text layer (%.0f alphanumerics per page, need %d); "
                "the pages are probably scanned images"
                % (density, MIN_ALNUM_PER_PAGE))

        result = convert_pages(
            doc, entry, verbose=verbose, cancel_requested=cancel_requested
        )
        metadata = doc.metadata or {}
        title = document_title(doc, pdf)
        author = re.sub(r"\s+", " ", (metadata.get("author") or "")).strip()
        toc = doc.get_toc()
        page_count = len(doc)
    finally:
        doc.close()

    if result.page_errors > max(1, int(page_count * MAX_PAGE_ERROR_RATE)):
        raise TraditionalError(
            f"{result.page_errors} of {page_count} pages failed to convert")
    body = "\n\n".join(page for page in result.pages if page.strip())
    if len(body.strip()) < MIN_BODY_CHARS:
        raise TraditionalError(
            f"produced only {len(body.strip())} characters of body text")

    result.issues, _ = K.check_text(body)
    fatal = result.fatal_issues
    if fatal:
        raise TraditionalError(
            "generated LaTeX would not compile: "
            + ", ".join("%s=%d" % kv for kv in fatal.most_common(4)))
    return result, toc, title, author


def fingerprint(digest: str) -> dict:
    """What must hold for this document's cached pages to still be valid."""
    return {"sha256": digest, "version": EXTRACTION_VERSION,
            "converter": "traditional", "pymupdf": pymupdf.VersionBind}

"""Cutting a converted document into one file per chapter.

The planning half is pure: pages in, sections out, no filesystem. The
publishing half writes them, and does so in the order that survives being
killed halfway -- intent recorded first, files written second, stale files from
the previous run removed third.

Both converters share this. What differs between them is only how a section
body becomes a standalone LaTeX document, which the caller passes in.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..checkpoint import DocumentEntry, valid_file
from ..fsutil import atomic_write
from .headings import Heading, PAGE_SEPARATOR, headings_from_text
from .naming import flat_section_filename

#: Bumped when a change here would produce different files from the same pages.
SPLITTER_VERSION = 4

#: Heading spacing below which text-based detection is not believed.
DEFAULT_MIN_SECTION_CHARS = 3000

#: Renders one section into a standalone document: (heading, body, source).
Renderer = Callable[[str, str, str], str]

#: Finds chapter boundaries: (pages, toc, joined text, minimum) -> (method, headings).
#: The two converters need different ones -- see chapter_split.headings.
Detector = Callable[[list[str], list, str, int], tuple[str, list[Heading]]]


def build_sections(text: str, headings: list[Heading]) -> list[tuple[str, str]]:
    if not headings:
        return [("Complete Text", text)]

    sections: list[tuple[str, str]] = []
    if headings[0].start > 0:
        sections.append(("Front Matter", text[: headings[0].start]))
    for index, heading in enumerate(headings):
        end = headings[index + 1].start if index + 1 < len(headings) else len(text)
        sections.append((heading.label, text[heading.start:end]))
    return sections


@dataclass
class PlannedSection:
    """One chapter: where it goes, what it says, and how it is identified."""

    number: int
    heading: str
    body: str               # rendered, ready to write
    file: str               # POSIX path relative to the destination root
    digest: str

    def record(self) -> dict:
        return {"file": self.file, "heading": self.heading,
                "characters": len(self.body), "sha256": self.digest}


@dataclass
class SplitPlan:
    method: str
    sections: list[PlannedSection]

    def records(self) -> list[dict]:
        return [section.record() for section in self.sections]


def plan(relative: Path, pages: list[str], toc: list, *, render: Renderer,
         minimum: int = DEFAULT_MIN_SECTION_CHARS, suffix: str = ".tex",
         detect: Detector = headings_from_text) -> SplitPlan:
    """-> where every chapter of this document should go, without writing any.

    ``detect`` decides where the boundaries come from. Both supplied detectors
    try the PDF's bookmarks first -- a book that carries its own outline should
    never be at the mercy of a regular expression -- and differ only in what
    they fall back to.
    """
    text = PAGE_SEPARATOR.join(pages)
    method, headings = detect(pages, toc, text, minimum)
    sections = build_sections(text, headings)
    if "".join(body for _, body in sections) != text:
        raise RuntimeError(f"Chapter splitting lost text: {relative}")

    source = relative.as_posix()
    planned = []
    for number, (heading, body) in enumerate(sections):
        name = str(Path(flat_section_filename(relative.stem, number, heading))
                   .with_suffix(suffix))
        rendered = render(heading, body, source)
        planned.append(PlannedSection(
            number=number, heading=heading, body=rendered,
            file=(relative.parent / name).as_posix(),
            digest=hashlib.sha256(rendered.encode("utf-8")).hexdigest()))
    return SplitPlan(method=method, sections=planned)


def settings_for(minimum: int, extra: dict | None = None) -> dict:
    """The split configuration recorded in the checkpoint.

    Anything that would change the files produced from the same pages belongs
    here, so that changing it invalidates the previous split without touching
    the far more expensive page cache.
    """
    return {"version": SPLITTER_VERSION, "minimum": minimum, **(extra or {})}


def publish(entry: DocumentEntry, plan: SplitPlan, destination: Path, *,
            settings: dict) -> list[dict]:
    """Write a planned split and record it; -> the output records.

    Intent is recorded before the first file is written so an interrupted run
    knows what it was in the middle of, and files identical to what is already
    on disk are left alone.
    """
    outputs = plan.records()
    previous = entry.begin_publication(outputs)
    for section in plan.sections:
        path = destination / section.file
        if not valid_file(path, section.digest):
            atomic_write(path, section.body)
    current = {row["file"] for row in outputs}
    for old in previous:
        if old["file"] in current:
            continue
        # Only remove a stale file still identical to this pipeline's output;
        # anything a person has edited is theirs.
        path = destination / old["file"]
        if valid_file(path, old["sha256"]):
            path.unlink()
    entry.finish_publication(outputs, settings=settings, method=plan.method)
    return outputs

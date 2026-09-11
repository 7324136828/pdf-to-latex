"""Splitting a converted book into one LaTeX file per chapter.

    headings   where chapters begin: PDF bookmarks first, heuristics second
    naming     heading -> a portable filename inside the byte limits
    sections   planning the split, and publishing it without losing work
    catalog    the TSV index of everything published

All of this was inside the OCR command, which was the only caller. Both
converters now produce a list of per-page strings and hand it here, so a book
is chunked the same way whichever route read it.
"""

from .catalog import CATALOG_NAME, write_catalog
from .headings import (PAGE_SEPARATOR, Heading, bookmark_headings,
                       detect_structure, headings_from_latex, headings_from_text,
                       latex_headings, normalize_key)
from .naming import flat_section_filename, safe_filename, truncate_utf8
from .sections import (DEFAULT_MIN_SECTION_CHARS, SPLITTER_VERSION, PlannedSection,
                       SplitPlan, build_sections, plan, publish, settings_for)

__all__ = [
    "CATALOG_NAME", "DEFAULT_MIN_SECTION_CHARS", "PAGE_SEPARATOR",
    "SPLITTER_VERSION", "Heading", "PlannedSection", "SplitPlan",
    "bookmark_headings", "build_sections", "detect_structure",
    "flat_section_filename", "headings_from_latex", "headings_from_text",
    "latex_headings", "normalize_key", "plan", "publish", "safe_filename",
    "settings_for", "truncate_utf8", "write_catalog",
]

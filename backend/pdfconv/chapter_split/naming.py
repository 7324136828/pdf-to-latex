"""Turning a book stem and a heading into a portable output filename.

Moved unchanged from the OCR command. The awkward parts are deliberate: the
254-byte path component limit is a byte limit, not a character one, and two
different books whose names truncate to the same prefix must not collide.
"""

from __future__ import annotations

import hashlib
import re

#: The extension the splitter names files with before the caller substitutes
#: its own; both converters write LaTeX, so both replace it with ".tex".
DEFAULT_SUFFIX = ".txt"


def safe_filename(label: str, limit: int = 100) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", " ", label)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return (cleaned[:limit].rstrip(" .") or "Untitled Section")


def truncate_utf8(value: str, max_bytes: int) -> str:
    """Truncate a filename component without cutting a UTF-8 character."""
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", errors="ignore").rstrip(" .")


def flat_section_filename(book_stem: str, number: int, heading: str) -> str:
    """Build a flat, portable filename below the usual 255-byte limit."""
    book_label = truncate_utf8(safe_filename(book_stem, 180), 160)
    if book_label != book_stem:
        # Truncation/sanitization must not merge distinct books' output names.
        digest = hashlib.sha256(book_stem.encode("utf-8")).hexdigest()[:12]
        book_label = truncate_utf8(book_label, 145) + "_" + digest
    heading_label = truncate_utf8(safe_filename(heading, 100), 65)
    fixed = f"_{number:03d} - "
    filename = f"{book_label}{fixed}{heading_label}{DEFAULT_SUFFIX}"
    return truncate_utf8(filename[:-4], 236) + DEFAULT_SUFFIX

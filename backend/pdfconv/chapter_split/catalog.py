"""The index of everything a run published.

One row per generated chapter, recording which PDF it came from and how its
boundary was found. Moved unchanged from the OCR command.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from ..fsutil import atomic_write

CATALOG_NAME = "sections_catalog.tsv"


def write_catalog(documents: dict, destination: Path) -> Path:
    """Write ``sections_catalog.tsv`` for every document that has been split."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, delimiter="\t", lineterminator="\n")
    writer.writerow(["source_file", "section_file", "detection_method", "heading",
                     "characters", "converter"])
    for source, entry in sorted(documents.items()):
        if not entry.get("split"):
            continue
        source_file = entry.get("source", source)
        converter = entry.get("converter", "")
        for row in entry["outputs"]:
            writer.writerow([source_file, row["file"], entry.get("method", ""),
                             row["heading"], row["characters"], converter])
    path = destination / CATALOG_NAME
    atomic_write(path, buffer.getvalue())
    return path

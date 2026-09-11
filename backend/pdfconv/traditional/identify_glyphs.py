"""Render every maths glyph the PDF uses, so its LaTeX meaning can be checked.

Some of the fonts carry no usable meaning in their encodings.  NewOptr2k is the
worst: it is WinAnsi-encoded with no /Differences, so its glyph names are simply
the character names -- `q`, `Uacute`, `L`, `K`, `Z` -- while the shapes drawn are
infinity, greater-or-equal, approximately-equal, identical-to and not-equal.
The only way to learn the mapping is to look at the glyphs, so this script finds
one occurrence of each and crops it out of the page.

    python3 identify_glyphs.py            # fonts with arbitrary encodings
    python3 identify_glyphs.py --all      # every maths font
    python3 identify_glyphs.py --missing  # only glyphs symbols.py cannot map

Writes PNGs plus an index.txt to build/fontdump/.  Anything listed as UNMAPPED
needs a new entry in symbols.py (NEWOPTR or MINOR).
"""

import os
import sys

import pymupdf

from . import config as C
from . import pagemodel as PM
from . import symbols as S

# fonts whose encoding does not describe what is actually drawn
ARBITRARY = {"NewOptr2k", "MathematicalPiLTStd-1", "MathematicalPiLTStd-3",
             "MathematicalPiLTStd-4", "MathematicalPi-One",
             "MathematicalPiOneOblique", "Allphf", "PearsonMATHPRO01",
             "wasy9", "wasy10", "MSAM10", "MSBM10", "Symbol"}

CONTEXT = 26.0      # points of page either side of the glyph
DPI = 400


def current_mapping(font, text, name):
    """What symbols.py would render this glyph as, or None."""
    if name:
        for table in (S.GLYPH, S.BIG_OPS, S.DELIM_OPEN, S.DELIM_CLOSE):
            if name in table:
                return table[name]
        if name in S.DELIM_EXT:
            return "(delimiter extension)"
    if font == "NewOptr2k" and text in S.NEWOPTR:
        return S.NEWOPTR[text]
    if (font, text) in S.MINOR:
        return S.MINOR[(font, text)]
    if text in S.UNICODE:
        return S.UNICODE[text]
    return None


def main(argv):
    want_all = "--all" in argv
    only_missing = "--missing" in argv
    fonts = PM.MATH_FONTS if want_all else ARBITRARY

    doc = pymupdf.open(C.pdf_path())
    os.makedirs(C.FONTDUMP, exist_ok=True)

    seen = {}
    for pno in range(doc.page_count):
        page = doc[pno]
        for blk in page.get_text("rawdict")["blocks"]:
            if blk.get("type") != 0:
                continue
            for line in blk.get("lines", []):
                for span in line["spans"]:
                    font = span["font"].split("+")[-1]
                    if font not in fonts:
                        continue
                    for ch in span["chars"]:
                        key = (font, ch["c"])
                        if key not in seen:
                            seen[key] = (pno, ch["bbox"])
        if len(seen) > 400:
            break

    rows = []
    for (font, text), (pno, bbox) in sorted(seen.items()):
        if not text.strip():
            continue
        gmap = PM.glyphmap.page_glyphmaps(doc, pno).get(font, ({}, None, 0))[0]
        entry = gmap.get(text)
        name = entry[0] if entry else None
        mapped = current_mapping(font, text, name)
        if only_missing and mapped is not None:
            continue

        safe = "%s_u%04x" % (font.replace(",", "_").replace(" ", ""), ord(text))
        png = os.path.join(C.FONTDUMP, safe + ".png")
        rect = pymupdf.Rect(bbox)
        rect = pymupdf.Rect(rect.x0 - CONTEXT, rect.y0 - 9,
                            rect.x1 + CONTEXT, rect.y1 + 9)
        try:
            doc[pno].get_pixmap(clip=rect, dpi=DPI).save(png)
        except Exception as exc:
            print("  could not render %s: %s" % (safe, exc))
            continue
        rows.append((font, text, name, mapped, pno + 1, os.path.basename(png)))

    index = os.path.join(C.FONTDUMP, "index.txt")
    with open(index, "w") as f:
        f.write("%-26s %-8s %-20s %-24s %6s  %s\n"
                % ("font", "char", "glyph name", "symbols.py gives", "page", "png"))
        f.write("-" * 110 + "\n")
        for font, text, name, mapped, page, png in rows:
            f.write("%-26s %-8s %-20s %-24s %6d  %s\n"
                    % (font, repr(text), name or "-",
                       mapped if mapped is not None else "*** UNMAPPED ***",
                       page, png))
    doc.close()

    unmapped = [r for r in rows if r[3] is None]
    print("rendered %d glyph(s) to %s" % (len(rows), C.FONTDUMP))
    print("index: %s" % index)
    if unmapped:
        print("\n%d glyph(s) have no mapping in symbols.py -- open the PNG and "
              "add an entry:" % len(unmapped))
        for font, text, name, _, page, png in unmapped:
            print("    %-26s %-8s p%-5d %s" % (font, repr(text), page, png))
    else:
        print("every rendered glyph has a mapping in symbols.py")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

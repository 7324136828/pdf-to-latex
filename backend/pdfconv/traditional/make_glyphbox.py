"""Regenerate glyphbox.json: the true vertical extent of every maths glyph.

PyMuPDF reports the font's nominal ascender/descender box for every character,
which is wrong for the extensible glyphs: a \\Bigg delimiter is about three
times taller than the box it is given, and a display integral more than twice.
Trusting those boxes puts a tall delimiter in a horizontal band of its own
instead of the band of the expression it encloses, which tears formulas apart.

The real extents are measured from the outlines in the embedded CFF font
programs.  Values are in font units (1/1000 em), as [y_min, y_max] with y
increasing upwards, keyed by glyph name.

Needs fontTools, which the conversion itself does not:

    python3 -m pip install --user fonttools
    python3 make_glyphbox.py
"""

import io
import json
import sys

import pymupdf

from . import config as C

FONTS = ("MTEX", "MTSY", "MTSYB", "RMTMI", "RMTMIB", "MTMI", "NewOptr2k")


def main():
    try:
        from fontTools.cffLib import CFFFontSet
        from fontTools.pens.boundsPen import BoundsPen
    except ImportError:
        raise SystemExit(
            "fontTools is required to regenerate glyphbox.json:\n"
            "    python3 -m pip install --user fonttools\n"
            "(the conversion itself does not need it -- the checked-in\n"
            "glyphbox.json is enough)")

    doc = pymupdf.open(C.pdf_path())
    # Take the largest subset of each font: subsets differ per page, and the
    # widest one carries the most glyphs.  Outlines are identical across
    # subsets, so any subset gives the same measurements for the glyphs it has.
    best = {}
    for pno in range(doc.page_count):
        for f in doc[pno].get_fonts(full=True):
            xref, basefont = f[0], f[3]
            if basefont not in FONTS:
                continue
            try:
                _, _, _, buf = doc.extract_font(xref)
            except Exception:
                continue
            if not buf:
                continue
            if basefont not in best or len(buf) > len(best[basefont]):
                best[basefont] = buf

    out = {}
    for name, buf in sorted(best.items()):
        try:
            cff = CFFFontSet()
            cff.decompile(io.BytesIO(buf), None)
            td = cff[cff.fontNames[0]]
            charstrings = td.CharStrings
        except Exception as exc:
            print("  %-12s skipped (%s)" % (name, exc))
            continue
        boxes = {}
        for gname in charstrings.keys():
            pen = BoundsPen(None)
            try:
                charstrings[gname].draw(pen)
            except Exception:
                continue
            if pen.bounds:
                _, y0, _, y1 = pen.bounds
                boxes[gname] = [round(y0), round(y1)]
        out[name] = boxes
        tall = sum(1 for v in boxes.values() if v[1] - v[0] > 1200)
        print("  %-12s %3d glyphs, %2d extensible" % (name, len(boxes), tall))

    doc.close()
    if not out:
        raise SystemExit("no maths fonts found in %s" % C.pdf_path())
    with open(C.GLYPHBOX, "w") as f:
        json.dump(out, f, indent=0, sort_keys=True)
    print("wrote %s" % C.GLYPHBOX)


if __name__ == "__main__":
    sys.exit(main())

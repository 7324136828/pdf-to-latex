"""Compare the available sources of this book and say which can be converted.

Counting Unicode maths characters in extracted text is not a fair test here:
neither PDF stores its formulas as ordinary Unicode.  Both put them in special
fonts under private encodings, and the real question is whether that encoding
can be decoded.

    the .txt files      formulas gone -- unusable, nothing to decode
    US 10th ed PDF      formulas present, mapped to Ethiopic/Indic codepoints
    Global Edition PDF  formulas present, in the MathTime fonts (what we use)

Run:  python3 diagnose_source.py
"""

import collections
import glob
import os
import sys
import unicodedata

import pymupdf

from . import config as C

# Structural maths: a probability textbook is full of these.  If the text has
# none at all, the formulas were dropped in extraction, not merely re-encoded.
STRUCTURAL = "∑∏∫√"
GREEK = "λσμθαβγπωδερτφχψΓΔΣΠΩΘΦΛ"
RELATIONS = "≤≥≠≈∞∈∪∩⊂"

# fonts that carry mathematics in one source or the other
MATH_FONT_HINTS = ("MT", "Math", "Cambria", "CM", "wasy", "MSAM", "MSBM",
                   "Symbol", "Optr", "Allphf", "Pearson")


def block_of(ch):
    try:
        return unicodedata.name(ch).split()[0]
    except ValueError:
        return "UNNAMED"


def count(text):
    return (sum(text.count(c) for c in STRUCTURAL),
            sum(text.count(c) for c in GREEK),
            sum(text.count(c) for c in RELATIONS))


def report_txt():
    txts = sorted(glob.glob(os.path.join(C.TXT_DIR, "*.txt")))
    print("\n--- the .txt files -------------------------------------------")
    if not txts:
        print("  none found under %s" % C.TXT_DIR)
        return
    blob = "".join(open(p, errors="replace").read() for p in txts)
    st, gk, rel = count(blob)
    print("  %d files, %d characters" % (len(txts), len(blob)))
    print("  structural (sum/prod/int/sqrt): %d" % st)
    print("  Greek letters:                  %d" % gk)
    print("  relations (<=, >=, infinity):   %d" % rel)
    if st == 0 and gk == 0:
        print("  VERDICT: unusable.  Not one summation, integral, radical or")
        print("           Greek letter in the whole text.  The formulas are")
        print("           absent, so there is nothing here to repair or decode.")
    else:
        print("  VERDICT: carries some mathematics")


def report_pdf(label, path, note=""):
    print("\n--- %s ---" % label)
    if not os.path.exists(path):
        print("  not found: %s" % path)
        return
    doc = pymupdf.open(path)
    fonts = collections.Counter()
    mathchars = collections.Counter()
    plain = []
    for pno in range(0, doc.page_count, 3):
        page = doc[pno]
        plain.append(page.get_text())
        for f in page.get_fonts(full=True):
            fonts[f[3].split("+")[-1]] += 1
        for blk in page.get_text("rawdict")["blocks"]:
            if blk.get("type") != 0:
                continue
            for line in blk.get("lines", []):
                for span in line["spans"]:
                    fname = span["font"].split("+")[-1]
                    if any(h in fname for h in MATH_FONT_HINTS):
                        for ch in span["chars"]:
                            if ch["c"].strip():
                                mathchars[ch["c"]] += 1
    pages = doc.page_count
    doc.close()

    mathfonts = sorted(f for f in fonts if any(h in f for h in MATH_FONT_HINTS))
    total = sum(mathchars.values())
    print("  %s" % path)
    print("  %d pages (every 3rd sampled)" % pages)
    print("  maths fonts: %s" % (", ".join(mathfonts) if mathfonts else "none"))
    print("  glyphs set in those fonts: %d, over %d distinct codepoints"
          % (total, len(mathchars)))
    if mathchars:
        blocks = collections.Counter()
        for c, n in mathchars.items():
            blocks[block_of(c)] += n
        print("  their Unicode blocks: %s"
              % ", ".join("%s=%d" % kv for kv in blocks.most_common(6)))
    st, gk, rel = count("".join(plain))
    print("  of which decode to real maths Unicode: %d structural, %d Greek"
          % (st, gk))
    if note:
        print("  NOTE: %s" % note)


def main():
    print("=" * 70)
    print("Sources of 'A First Course in Probability', and what each can give")
    print("=" * 70)

    report_txt()

    report_pdf(
        "US 10th edition PDF (the .txt files came from this)", C.PDF_US,
        "the formulas ARE here, but its ToUnicode CMap is broken: delimiters\n"
        "        come out as Ethiopic syllables, + and - and = as Malayalam vowel\n"
        "        signs, subscript digits as Oriya letters.  Decodable in principle,\n"
        "        the same way build/ decodes the Global Edition -- but it is a\n"
        "        larger table (~330 codepoints) and was not attempted.")

    try:
        ge = C.pdf_path()
    except SystemExit as exc:
        print("\n--- Global Edition PDF ---\n  %s" % exc)
        return 1
    report_pdf("Global Edition PDF (what build/ converts)", ge,
               "MathTime fonts with per-glyph position data; ~70 codepoints to\n"
               "        decode.  Caveat: Global Edition renumbers some exercises.")

    print("\n" + "=" * 70)
    print("The .txt files are the only source that is genuinely unrecoverable.\n"
          "Both PDFs hold their mathematics; they differ in how much work the\n"
          "encoding takes and in which edition's numbering you end up with.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())

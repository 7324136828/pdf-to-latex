"""Resolve PDF character codes to glyph names for the math fonts in the
Global Edition PDF.  PyMuPDF hands back whatever /ToUnicode says, which for
big operators, radicals and extensible delimiters is either a raw byte or a
presentation-form codepoint.  Both are ambiguous, so we rebuild the real
code -> glyphname mapping from each font's /Encoding /Differences array and
key it by the text PyMuPDF actually emits."""

import re


def base_font(name):
    """Remove the PDF subset tag consistently for resources and text spans."""
    return name.split("+")[-1]

MATH_FONTS = {"MTEX", "MTSY", "MTSYB", "RMTMI", "RMTMIB", "MTMI", "NewOptr2k",
              "Symbol", "MathematicalPiLTStd-1", "MathematicalPiLTStd-3",
              "MathematicalPiLTStd-4", "MathematicalPi-One", "Allphf",
              "PearsonMATHPRO01", "MSBM10", "MSAM10", "wasy9", "wasy10",
              "MathematicalPiOneOblique", "MTMIB", "MTSYN", "SymbolItalic",
              "CambriaMath"}

def is_math_font(name):
    name = base_font(name)
    return (name in MATH_FONTS or bool(re.fullmatch(
        r"(?:CMMI|CMSY|CMEX|MSAM|MSBM)\d+", name)))


_HEX = re.compile(r"<([0-9A-Fa-f]+)>")


def _cmap_ranges(text):
    """Yield (code, unicode_string) pairs from a ToUnicode CMap."""
    for blk in re.findall(r"beginbfchar(.*?)endbfchar", text, re.S):
        toks = _HEX.findall(blk)
        for i in range(0, len(toks) - 1, 2):
            yield int(toks[i], 16), _utf16(toks[i + 1])
    for blk in re.findall(r"beginbfrange(.*?)endbfrange", text, re.S):
        # only the <lo> <hi> <dst> form appears in this file
        for lo, hi, dst in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", blk):
            lo, hi = int(lo, 16), int(hi, 16)
            base = int(dst, 16)
            for k in range(hi - lo + 1):
                yield lo + k, chr(base + k)


def _utf16(h):
    b = bytes.fromhex(h if len(h) % 2 == 0 else "0" + h)
    try:
        return b.decode("utf-16-be")
    except UnicodeDecodeError:
        return b.decode("latin-1")


def _differences(doc, enc_xref):
    obj = doc.xref_object(enc_xref)
    m = re.search(r"/Differences\s*\[(.*?)\]", obj, re.S)
    if not m:
        return {}
    out, code = {}, 0
    for tok in m.group(1).split():
        if re.fullmatch(r"\d+", tok):
            code = int(tok)
        elif tok.startswith("/"):
            out[code] = tok[1:]
            code += 1
    return out


def font_widths(doc, xref):
    """-> the font's /Widths array, indexed from /FirstChar."""
    obj = doc.xref_object(xref)
    m = re.search(r"/Widths\s*\[(.*?)\]", obj, re.S)
    if not m:
        return None, 0
    try:
        w = [float(t) for t in m.group(1).split()]
    except ValueError:
        return None, 0
    fc = re.search(r"/FirstChar\s+(\d+)", obj)
    return w, int(fc.group(1)) if fc else 0


def font_glyphmap(doc, xref):
    """-> {text_pymupdf_emits: (glyphname, code)} for one font object."""
    obj = doc.xref_object(xref)
    diffs = {}
    m = re.search(r"/Encoding\s+(\d+)\s+0\s+R", obj)
    if m:
        diffs = _differences(doc, int(m.group(1)))
    tou = {}
    m = re.search(r"/ToUnicode\s+(\d+)\s+0\s+R", obj)
    if m:
        try:
            tou = dict(_cmap_ranges(doc.xref_stream(int(m.group(1))).decode("latin-1")))
        except Exception:
            tou = {}
    out, collisions = {}, set()
    for code, glyph in diffs.items():
        key = tou.get(code, chr(code))
        if key in out and out[key][0] != glyph:
            collisions.add(key)
        out[key] = (glyph, code)
    # codes with a ToUnicode but no Differences entry (base-encoded fonts)
    for code, uni in tou.items():
        out.setdefault(uni, (None, code))
    return out, collisions


def page_glyphmaps(doc, pno):
    """-> {basefont: ({text: (glyphname, code)}, widths, firstchar)} per page."""
    maps = {}
    for f in doc[pno].get_fonts(full=True):
        xref, basefont = f[0], base_font(f[3])
        if is_math_font(basefont):
            gm, _ = font_glyphmap(doc, xref)
            widths, first = font_widths(doc, xref)
            maps[basefont] = (gm, widths, first)
    return maps

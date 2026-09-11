"""Turn a PDF page into a flat list of positioned glyphs plus the vector
rules (fraction bars, radical overbars, table borders) that carry structure."""

import json
import os
from dataclasses import dataclass, field
from . import glyphmap
from .fontdecode import page_replacements
from . import symbols as S

# True vertical extents (font units) of the maths glyphs, measured from the
# embedded CFF outlines.  PyMuPDF reports the font's nominal ascender/descender
# box for every glyph, which badly understates a \Bigg delimiter or a display
# integral -- they then fall into a band of their own instead of banding with
# the expression they enclose.
with open(os.path.join(os.path.dirname(__file__), "glyphbox.json")) as _f:
    GLYPH_BOX = json.load(_f)

# NewOptr2k is StandardEncoding, so its CFF glyph names are the character names
_NEWOPTR_NAME = {"\u00da": "Uacute", "\u2026": "ellipsis", "*": "asterisk",
                 "#": "numbersign", "3": "three", ".": "period",
                 "(": "parenleft", ")": "parenright", ";": "semicolon"}

S = S  # re-exported for layout's structure tests

MATH_FONTS = S_MF = glyphmap.MATH_FONTS

ITALIC_FONTS = {"TimesTenLTStd-Italic", "TimesTen-Italic", "TimesNewRoman,Italic",
                "TimesTenLTStd-BoldItalic"}
ROMAN_FONTS = {"TimesTenLTStd-Roman", "TimesTen-Roman", "TimesLTPro-Roman"}
BOLD_FONTS = {"TimesTenLTStd-Bold", "TimesTenLTStd-BoldItalic"}
HEAD_FONTS = {"GoudySansPro-Bold", "GoudySansPro-Medium", "GoudySansPro-BoldItalic",
              "GoudySansPro-MediumItalic", "GillSansMTPro-Medium",
              "GillSansMTPro-Bold", "GillSansMTPro-MediumItalic"}
MONO_FONTS = {"CourierNew", "CMTT10"}


@dataclass
class Glyph:
    text: str            # what PyMuPDF emitted
    name: str | None     # resolved glyph name, when the font encoding gave one
    font: str
    size: float
    x0: float
    y0: float
    x1: float
    y1: float
    ox: float            # baseline origin
    oy: float
    flags: int = 0      # PyMuPDF font style flags, independent of font family

    @property
    def cx(self):
        return (self.x0 + self.x1) / 2

    @property
    def is_math_font(self):
        return glyphmap.is_math_font(self.font)

    @property
    def is_italic(self):
        return (self.font in ITALIC_FONTS or bool(self.flags & 2)
                or "Italic" in self.font or "Oblique" in self.font)

    @property
    def is_bold(self):
        return self.font in BOLD_FONTS or bool(self.flags & 16) or "Bold" in self.font

    @property
    def is_roman(self):
        return not (self.is_math_font or self.is_italic or self.is_bold)

    @property
    def is_head(self):
        return self.font in HEAD_FONTS

    def latex_atom(self):
        """Best-effort single-token LaTeX for this glyph, ignoring position."""
        if self.name:
            if self.name in S.GLYPH:
                return S.GLYPH[self.name]
            if self.name in S.BIG_OPS:
                return S.BIG_OPS[self.name]
            if self.name in S.DELIM_OPEN:
                return S.DELIM_OPEN[self.name]
            if self.name in S.DELIM_CLOSE:
                return S.DELIM_CLOSE[self.name]
        if self.font == "NewOptr2k" and self.text in S.NEWOPTR:
            return S.NEWOPTR[self.text]
        key = (self.font, self.text)
        if key in S.MINOR:
            return S.MINOR[key]
        if self.text in S.UNICODE:
            return S.UNICODE[self.text]
        if self.name in S.DELIM_EXT:
            return ""          # extension piece of a tall delimiter
        if len(self.text) == 1 and ord(self.text) < 0x20:
            return ""          # an unmapped symbol-font code
        if self.text == "$":
            return r"\$"
        return self.text


@dataclass
class Rule:
    """A thin filled/stroked line: fraction bar, radical bar or table border."""
    x0: float
    y0: float
    x1: float
    y1: float
    width: float

    @property
    def horizontal(self):
        return abs(self.y1 - self.y0) <= 1.2 and (self.x1 - self.x0) > 1.5

    @property
    def vertical(self):
        return abs(self.x1 - self.x0) <= 1.2 and (self.y1 - self.y0) > 1.5

    @property
    def y(self):
        return (self.y0 + self.y1) / 2

    @property
    def length(self):
        return self.x1 - self.x0


BLANKS = " \u00a0\t\r\n"


def is_blank(g):
    """True only for real spaces.

    str.strip() must not be used for this: Python counts U+001C..U+001F as
    whitespace, and those are exactly the codes MTEX uses for big operators and
    extensible delimiters, so a naive strip() deletes every summation sign on
    the page.  A resolved glyph name always means a real glyph.
    """
    if g.name is not None:
        return False
    return g.text.strip(BLANKS) == ""


LIGATURES = {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st"}


def read_page(doc, pno):
    """-> (glyphs, rules) for one page, glyph names resolved per-page."""
    page = doc[pno]
    maps = glyphmap.page_glyphmaps(doc, pno)
    replacements = page_replacements(doc, pno)
    glyphs = []
    for blk in page.get_text("rawdict")["blocks"]:
        if blk.get("type") != 0:
            continue
        for line in blk.get("lines", []):
            for span in line["spans"]:
                font = glyphmap.base_font(span["font"])
                gmap, widths, firstchar = maps.get(font, ({}, None, 0))
                for ch in span["chars"]:
                    c = ch["c"]
                    if len(c) == 1:
                        c = replacements.get((font, ord(c), round(ch["origin"][0], 2),
                                              round(ch["origin"][1], 2)), c)
                    if c in LIGATURES:
                        # expand ligature into its letters, sharing the bbox
                        bb = ch["bbox"]
                        w = (bb[2] - bb[0]) / len(LIGATURES[c])
                        for k, sub in enumerate(LIGATURES[c]):
                            glyphs.append(Glyph(sub, None, font, span["size"],
                                                bb[0] + k * w, bb[1],
                                                bb[0] + (k + 1) * w, bb[3],
                                                ch["origin"][0] + k * w, ch["origin"][1],
                                                span.get("flags", 0)))
                        continue
                    bb = ch["bbox"]
                    entry = gmap.get(c)
                    name = None
                    if entry is not None:
                        name, code = entry
                        # PyMuPDF injects synthetic spaces wherever the text is
                        # advanced by a gap, and they inherit the current font;
                        # decoded through the maths encoding they masquerade as
                        # real glyphs (a stray radical, a stray product sign).
                        # A genuine glyph advances by its own /Widths entry.
                        if c == " " and widths is not None and span["size"] > 0:
                            k = code - firstchar
                            if 0 <= k < len(widths):
                                want = widths[k] * span["size"] / 1000.0
                                got = bb[2] - bb[0]
                                if want > 0.1 and abs(got - want) > max(0.35, want * 0.10):
                                    name = None
                                    c = " "
                    y0, y1 = bb[1], bb[3]
                    box = GLYPH_BOX.get(font)
                    if box is not None:
                        key = name or _NEWOPTR_NAME.get(c, c)
                        ext = box.get(key)
                        if ext is not None:
                            oy = ch["origin"][1]
                            sz = span["size"] / 1000.0
                            y0, y1 = oy - ext[1] * sz, oy - ext[0] * sz
                    glyphs.append(Glyph(c, name, font, span["size"],
                                        bb[0], y0, bb[2], y1,
                                        ch["origin"][0], ch["origin"][1], span.get("flags", 0)))
    rules = []
    for d in page.get_drawings():
        r = d["rect"]
        w = d.get("width") or 0.5
        # a filled rectangle thinner than ~2pt is also a rule
        h = r.y1 - r.y0
        wd = r.x1 - r.x0
        if d["type"] in ("s", "f", "fs") and (h <= 2.0 or wd <= 2.0):
            rules.append(Rule(r.x0, r.y0, r.x1, r.y1, w))
    return glyphs, rules


def frac_rules(rules):
    """Horizontal rules short enough to be fraction/radical bars, not borders."""
    return [r for r in rules if r.horizontal and r.length < 260]


TALL_MARKS = ("big", "Big", "bigg", "Bigg", "tp", "bt", "ex", "mid")


def is_tall(g):
    """A big delimiter, big operator or radical -- something that spans rows."""
    n = g.name
    if not n:
        return False
    if n in S.BIG_OPS or n in S.RADICALS or n in S.DELIM_EXT:
        return True
    if n in S.DELIM_OPEN or n in S.DELIM_CLOSE:
        return any(n.endswith(m) for m in TALL_MARKS)
    return False


def tall_spans(glyphs, rules):
    """x-intervals covered by built-up maths, merged where they overlap.

    An opening delimiter must claim everything it encloses, not just its own
    stem, or the numerator of an inline binomial is left behind when the line
    is cut up.
    """
    spans = []
    tall = sorted([g for g in glyphs if is_tall(g)], key=lambda g: g.x0)
    stack = []
    for g in tall:
        n = g.name
        opening = n in S.DELIM_OPEN and any(n.endswith(m) for m in TALL_MARKS)
        closing = n in S.DELIM_CLOSE and any(n.endswith(m) for m in TALL_MARKS)
        if opening:
            stack.append(g)
        elif closing and stack:
            o = stack.pop()
            spans.append([o.x0 - 1.0, g.x1 + 1.0])
        else:
            spans.append([g.x0 - 1.0, g.x1 + 1.0])
    for g in stack:
        spans.append([g.x0 - 1.0, g.x1 + 1.0])
    for r in rules:
        spans.append([r.x0 - 1.0, r.x1 + 1.0])
    if not spans:
        return []
    spans.sort()
    out = [spans[0]]
    for a, b in spans[1:]:
        if a <= out[-1][1] + 2.0:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out

"""Recover missing PDF Unicode mappings only from matching font outlines.

MuPDF's rawdict substitutes the glyph ID for unmapped characters. Those IDs
can look like real letters in unrelated languages. texttrace exposes the
missing mapping as U+FFFD, so it is the authority for detecting this case.
Installed font cmaps and OpenType variants can recover the original character,
but only after the embedded and reference glyph outlines match exactly.
"""
from functools import lru_cache
from io import BytesIO
import os
from pathlib import Path
import re

from .glyphmap import base_font


class SourceEncodingError(RuntimeError):
    """The text layer contains a glyph that cannot be decoded reliably."""


@lru_cache(maxsize=16)
def _references(family, extra_dirs):
    from fontTools.ttLib import TTCollection, TTFont

    stem = re.sub(r"(?:[-,].*|PSMT|MT)$", "", family).casefold()
    roots = [Path(p) for p in extra_dirs.split(os.pathsep) if p]
    roots += [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",
              Path("/System/Library/Fonts"), Path("/Library/Fonts"),
              Path("/usr/share/fonts"), Path.home() / ".local/share/fonts"]
    result = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.suffix.casefold() not in (".ttf", ".ttc", ".otf"):
                continue
            if not path.stem.casefold().startswith(stem):
                continue
            try:
                fonts = (TTCollection(path).fonts if path.suffix.casefold() == ".ttc"
                         else [TTFont(path)])
                for font in fonts:
                    if "glyf" in font and "cmap" in font:
                        result.append((font, _unicode_by_glyph(font)))
            except (OSError, ValueError):
                continue
    return result


def _unicode_by_glyph(font):
    """Map encoded glyphs plus size/script alternates to Unicode."""
    direct = {name: chr(code) for code, name in (font.getBestCmap() or {}).items()}
    variants = []
    if "GSUB" in font:
        for lookup in font["GSUB"].table.LookupList.Lookup:
            for sub in lookup.SubTable:
                sub = getattr(sub, "ExtSubTable", sub)
                variants.extend(getattr(sub, "mapping", {}).items())
                for base, names in getattr(sub, "alternates", {}).items():
                    variants.extend((base, name) for name in names)
    if "MATH" in font:
        table = font["MATH"].table.MathVariants
        for axis in ("Vert", "Horiz"):
            coverage = getattr(table, axis + "GlyphCoverage", None)
            if coverage:
                for base, construction in zip(coverage.glyphs,
                        getattr(table, axis + "GlyphConstruction")):
                    variants.extend((base, row.VariantGlyph)
                                    for row in construction.MathGlyphVariantRecord)
    known = {name: {char} for name, char in direct.items()}
    for _ in range(8):
        changed = False
        for base, target in variants:
            if not isinstance(target, str) or target in direct or base not in known:
                continue
            previous = known.setdefault(target, set())
            size = len(previous)
            previous.update(known[base])
            changed |= len(previous) != size
        if not changed:
            break
    return {font.getGlyphID(name): next(iter(chars))
            for name, chars in known.items() if len(chars) == 1}


def _matching_character(embedded, reference, mapping, gid):
    if (gid not in mapping or gid >= embedded["maxp"].numGlyphs
            or embedded["head"].unitsPerEm != reference["head"].unitsPerEm):
        return None
    source_name = embedded.getGlyphName(gid)
    target_name = reference.getGlyphName(gid)
    source = embedded["glyf"][source_name].getCoordinates(embedded["glyf"])
    target = reference["glyf"][target_name].getCoordinates(reference["glyf"])
    if not source[0] or not all(a == b for a, b in zip(source, target)):
        return None
    if embedded["hmtx"][source_name] != reference["hmtx"][target_name]:
        return None
    return mapping[gid]


def page_replacements(doc, pno):
    missing = [(base_font(span["font"]), gid, origin)
               for span in doc[pno].get_texttrace()
               for code, gid, origin, _ in span["chars"] if code == 0xfffd]
    if not missing:
        return {}
    try:
        from fontTools.ttLib import TTFont
    except ImportError as error:
        raise SourceEncodingError("Unmapped PDF glyphs require fonttools or OCR") from error
    state = getattr(doc, "_pdfconv_font_recovery", None)
    if state is None:
        state = {"fonts": {}, "characters": {}}
        doc._pdfconv_font_recovery = state
    replacements = {}
    resources = doc[pno].get_fonts(full=True)
    for family, gid, origin in missing:
        key = (family, gid)
        if key not in state["characters"]:
            recovered = set()
            for row in resources:
                if base_font(row[3]) != family or row[1] != "ttf":
                    continue
                xref = row[0]
                if xref not in state["fonts"]:
                    state["fonts"][xref] = TTFont(BytesIO(doc.extract_font(xref)[3]))
                embedded = state["fonts"][xref]
                if "glyf" not in embedded:
                    continue
                for reference, mapping in _references(
                        family, os.environ.get("PDFCONV_FONT_DIR", "")):
                    char = _matching_character(embedded, reference, mapping, gid)
                    if char:
                        recovered.add(char)
            if len(recovered) != 1:
                raise SourceEncodingError(
                    f"page {pno + 1}: {family} glyph {gid} has no reliable Unicode "
                    "mapping; provide its matching font in PDFCONV_FONT_DIR or use OCR")
            state["characters"][key] = recovered.pop()
        replacements[(family, gid, round(origin[0], 2), round(origin[1], 2))] = (
            state["characters"][key])
    return replacements

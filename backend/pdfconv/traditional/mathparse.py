"""Reconstruct LaTeX from positioned glyphs.

The PDF stores maths as absolutely-placed glyphs plus vector rules; all the
structure (fractions, limits, radicals, scripts) lives in the geometry.  This
module recovers it with a recursive layout analysis:

  * fraction bars split their x-range into a numerator and a denominator
  * big operators claim whatever sits directly above/below them as limits
  * radical signs claim the run under their overbar
  * a stacked pair inside big parentheses with no bar is a binomial
  * a big left brace with stacked rows is a cases block
  * anything left is read left to right, with size + baseline giving scripts
"""

import re
from . import symbols as S
from . import pagemodel as PM
from .pagemodel import Glyph, Rule, is_blank

SCRIPT_RATIO = 0.86      # a glyph this much smaller than the base is a script
BASELINE_EPS = 0.08      # fraction of font size that counts as "same baseline"
# a subscript in this book drops only ~1.75pt at 10pt, so the tolerance has
# to stay well under that or every subscript is read as a baseline glyph


# --------------------------------------------------------------------------
# atoms
# --------------------------------------------------------------------------
class Atom:
    """A laid-out piece of maths with an x-range, a baseline and a size."""

    def __init__(self, latex, x0, x1, oy, size, kind="ord", y0=None, y1=None):
        self.latex = latex
        self.x0, self.x1 = x0, x1
        self.oy = oy
        self.size = size
        self.kind = kind
        self.y0 = oy - size if y0 is None else y0
        self.y1 = oy + size * 0.25 if y1 is None else y1

    def __repr__(self):
        return f"<{self.kind} {self.latex!r}>"


def _span(gs):
    return min(g.x0 for g in gs), max(g.x1 for g in gs)


def _dominant_size(items):
    if not items:
        return 10.0
    return max(i.size for i in items)


# --------------------------------------------------------------------------
# main entry
# --------------------------------------------------------------------------
def build(glyphs, rules, depth=0):
    """-> LaTeX for one sub-expression, always a single row."""
    glyphs = [g for g in glyphs if not is_blank(g)]
    if not glyphs:
        return ""
    if depth > 24:
        return _flat(glyphs)
    return _scripts(_atomise(glyphs, rules, depth))


def build_rows(glyphs, rules, depth=0):
    """-> list of LaTeX strings, one per line of a display equation."""
    glyphs = [g for g in glyphs if not is_blank(g)]
    if not glyphs:
        return []
    if depth > 24:
        return [_flat(glyphs)]
    out = []
    for rgs, rrs in _display_rows(glyphs, rules):
        atoms = _atomise(rgs, rrs, depth)
        if not atoms:
            continue
        for row in _atom_rows(atoms):
            tex = _scripts(row)
            if tex:
                out.append(tex)
    return out


def _display_rows(glyphs, rules):
    """Cut a display block into its equation lines before anything is parsed.

    Two stacked lines of a derivation are one band, and a fraction bar on the
    lower line would otherwise take its numerator from the line above.  The
    lines are found from the glyphs that are *not* part of any built-up
    structure -- the relation signs and operators sitting on each baseline.
    """
    spans = PM.tall_spans(glyphs, rules)

    def in_span(x):
        return any(a <= x <= b for a, b in spans)

    size = max(g.size for g in glyphs)
    free = [g for g in glyphs if not in_span(g.cx) and g.size >= size * 0.86]
    if len(free) < 2:
        return [(glyphs, rules)]
    ys = sorted(g.oy for g in free)
    centres = [[ys[0]]]
    for y in ys[1:]:
        if y - centres[-1][-1] > size * 1.45:
            centres.append([y])
        else:
            centres[-1].append(y)
    cs = [_median(c) for c in centres]
    if len(cs) < 2:
        return [(glyphs, rules)]

    def row_of(y):
        return min(range(len(cs)), key=lambda i: abs(y - cs[i]))

    rows = [[] for _ in cs]
    rrules = [[] for _ in cs]
    claimed = set()
    for a, b in spans:
        cluster = [g for g in glyphs if a <= g.cx <= b]
        if not cluster:
            continue
        mid = (min(g.y0 for g in cluster) + max(g.y1 for g in cluster)) / 2
        k = row_of(mid)
        for g in cluster:
            claimed.add(id(g))
            rows[k].append(g)
    for g in glyphs:
        if id(g) not in claimed:
            rows[row_of(g.oy)].append(g)
    for r in rules:
        rrules[row_of(r.y)].append(r)
    return [(sorted(rw, key=lambda g: g.ox), rr)
            for rw, rr in zip(rows, rrules) if rw]


def _atom_rows(atoms):
    """Split atoms into equation lines.

    Fractions and operator limits have already been folded into single atoms,
    so what is left on each line shares one baseline; successive display lines
    sit far further apart than any script offset.
    """
    base = _dominant_size(atoms)
    # a display operator or a fraction carries its own origin -- a display
    # integral sits a line and a half above the text baseline -- so neither can
    # be used to find where the equation's lines are
    main = [a for a in atoms if a.size >= base * SCRIPT_RATIO
            and a.kind not in ("frac", "op", "tall")]
    if not main:
        return [atoms]
    ys = sorted(a.oy for a in main)
    thresh = max(base * 1.35, 6.0)
    centres = [[ys[0]]]
    for y in ys[1:]:
        if y - centres[-1][-1] > thresh:
            centres.append([y])
        else:
            centres[-1].append(y)
    if len(centres) < 2:
        return [atoms]
    cs = [_median(c) for c in centres]
    rows = [[] for _ in cs]
    for a in atoms:
        k = min(range(len(cs)), key=lambda i: abs(a.oy - cs[i]))
        rows[k].append(a)
    for r in rows:
        r.sort(key=lambda a: a.x0)
    return [r for r in rows if r]


def _flat(glyphs):
    return "".join(g.latex_atom() for g in sorted(glyphs, key=lambda g: g.ox))


# --------------------------------------------------------------------------
# stage 1: pull out vertical structures, leaving a flat list of atoms
# --------------------------------------------------------------------------
def _atomise(glyphs, rules, depth):
    used_g, used_r = set(), set()
    atoms = []

    bars = sorted([r for r in rules if r.horizontal], key=lambda r: -r.length)

    # --- fractions and radical overbars ------------------------------------
    for r in bars:
        if id(r) in used_r:
            continue
        tol = 1.6
        inside = [g for g in glyphs if id(g) not in used_g
                  and g.cx >= r.x0 - tol and g.cx <= r.x1 + tol]
        above = [g for g in inside if g.oy < r.y - 0.8]
        below = [g for g in inside if g.oy > r.y + 0.8]
        if not above and not below:
            continue
        sub_rules = [q for q in rules if q is not r and id(q) not in used_r
                     and q.x0 >= r.x0 - tol and q.x1 <= r.x1 + tol]
        if above and below:
            used_r.add(id(r))
            for g in above + below:
                used_g.add(id(g))
            for q in sub_rules:
                used_r.add(id(q))
            num = build(above, [q for q in sub_rules if q.y < r.y], depth + 1)
            den = build(below, [q for q in sub_rules if q.y > r.y], depth + 1)
            atoms.append(Atom(r"\frac{%s}{%s}" % (num or "{}", den or "{}"),
                              r.x0, r.x1, r.y, _dominant_size(above + below) or 10,
                              "frac",
                              min(g.y0 for g in above + below),
                              max(g.y1 for g in above + below)))
        # a bar with content only underneath is handled with its radical below

    # --- radicals ----------------------------------------------------------
    for g in list(glyphs):
        if id(g) in used_g or not g.name or g.name not in S.RADICALS:
            continue
        if g.name in ("radicalvertex", "radicalbt", "radicaltp") and g.name != "radicaltp":
            used_g.add(id(g))
            continue
        bar = None
        for r in rules:
            if id(r) in used_r and r.horizontal:
                continue
            if not r.horizontal:
                continue
            if abs(r.x0 - g.x1) < 6 and r.y <= g.y0 + 3:
                if bar is None or r.length > bar.length:
                    bar = r
        used_g.add(id(g))
        if bar is None:
            atoms.append(Atom(r"\sqrt{}", g.x0, g.x1, g.oy, g.size, "ord"))
            continue
        used_r.add(id(bar))
        tol = 1.2
        arg = [h for h in glyphs if id(h) not in used_g
               and h.cx >= bar.x0 - tol and h.cx <= bar.x1 + tol and h.oy > bar.y]
        for h in arg:
            used_g.add(id(h))
        inner_rules = [q for q in rules if id(q) not in used_r
                       and q.x0 >= bar.x0 - tol and q.x1 <= bar.x1 + tol and q.y > bar.y]
        for q in inner_rules:
            used_r.add(id(q))
        body = build(arg, inner_rules, depth + 1)
        atoms.append(Atom(r"\sqrt{%s}" % (body or "{}"),
                          g.x0, bar.x1, arg[0].oy if arg else g.oy,
                          _dominant_size(arg) or g.size, "ord"))

    # --- big operators with limits -----------------------------------------
    for g in list(glyphs):
        if id(g) in used_g or not g.name or g.name not in S.BIG_OPS:
            continue
        used_g.add(id(g))
        op = S.BIG_OPS[g.name]
        # limits sit in (roughly) the operator's own column
        pad = 1.0 + g.size * 0.55
        col = [h for h in glyphs if id(h) not in used_g
               and h.cx > g.x0 - pad and h.cx < g.x1 + pad]
        near = g.size * 2.0
        upper = [h for h in col if h.y1 <= g.y0 + 1.5 and g.y0 - h.y1 < near]
        lower = [h for h in col if h.y0 >= g.y1 - 1.5 and h.y0 - g.y1 < near]
        if op == r"\int":
            # integral limits hang to the upper/lower right instead
            pad = 1.0 + g.size * 0.9
            col = [h for h in glyphs if id(h) not in used_g
                   and h.cx > g.x0 and h.cx < g.x1 + pad]
            upper = [h for h in col if h.y1 <= g.y0 + g.size * 0.35
                     and g.y0 - h.y1 < near]
            lower = [h for h in col if h.y0 >= g.y1 - g.size * 0.35
                     and h.y0 - g.y1 < near]
        for h in upper + lower:
            used_g.add(id(h))
        piece = op
        if lower:
            piece += "_{%s}" % build(lower, _rules_in(rules, lower, used_r), depth + 1)
        if upper:
            piece += "^{%s}" % build(upper, _rules_in(rules, upper, used_r), depth + 1)
        x0, x1 = g.x0, g.x1
        for h in upper + lower:
            x0, x1 = min(x0, h.x0), max(x1, h.x1)
        y0 = min([g.y0] + [h.y0 for h in upper + lower])
        y1 = max([g.y1] + [h.y1 for h in upper + lower])
        atoms.append(Atom(piece, x0, x1, g.oy, g.size, "op", y0, y1))

    # --- extensible delimiter pieces carry no content ----------------------
    for g in glyphs:
        if id(g) not in used_g and g.name in S.DELIM_EXT:
            used_g.add(id(g))

    # --- words set in upright roman are text, not a product of variables ---
    atoms += _words(glyphs, used_g)

    # --- stacked constructs inside big delimiters --------------------------
    atoms += _stacks(glyphs, rules, used_g, used_r, depth)

    # --- everything else ---------------------------------------------------
    for g in sorted(glyphs, key=lambda g: g.ox):
        if id(g) in used_g:
            continue
        used_g.add(id(g))
        atoms.append(Atom(g.latex_atom(), g.x0, g.x1, g.oy, g.size,
                          "tall" if PM.is_tall(g) else _kind(g), g.y0, g.y1))

    atoms.sort(key=lambda a: (a.x0, a.oy))
    return atoms


WORD_MIN = 3


def _words(glyphs, used_g):
    """Fold runs of upright roman letters into \\text{...}.

    A display line often carries real words -- "if", "otherwise", "and so on".
    Read glyph by glyph they become a product of italic variables with the
    spaces dropped, so they are kept together as text instead.
    """
    out, run = [], []
    for g in sorted((h for h in glyphs if id(h) not in used_g),
                    key=lambda h: h.ox):
        letter = g.text.isalpha() and g.is_roman and g.name is None
        if letter or (is_blank(g) and run):
            run.append(g)
            continue
        out.extend(_emit_word(run, used_g))
        run = []
    out.extend(_emit_word(run, used_g))
    return out


def _emit_word(run, used_g):
    while run and is_blank(run[-1]):
        run.pop()
    letters = [g for g in run if not is_blank(g)]
    if len(letters) < WORD_MIN:
        return []
    # blanks were dropped before parsing, so word breaks are read back off the
    # gaps between letters
    letters.sort(key=lambda g: g.ox)
    parts = [letters[0].text]
    for a, b in zip(letters, letters[1:]):
        if b.x0 - a.x1 > a.size * 0.13:
            parts.append(" ")
        parts.append(b.text)
    txt = "".join(parts)
    if not any(c in "aeiouyAEIOUY" for c in txt):
        return []
    for g in run:
        used_g.add(id(g))
    letters.sort(key=lambda g: g.ox)
    return [Atom("\\text{%s}" % txt,
                 min(g.x0 for g in letters), max(g.x1 for g in letters),
                 letters[0].oy, max(g.size for g in letters), "ord",
                 min(g.y0 for g in letters), max(g.y1 for g in letters))]


def _rules_in(rules, gs, used_r):
    if not gs:
        return []
    x0, x1 = _span(gs)
    y0 = min(g.y0 for g in gs)
    y1 = max(g.y1 for g in gs)
    out = []
    for r in rules:
        if id(r) in used_r:
            continue
        if r.x0 >= x0 - 2 and r.x1 <= x1 + 2 and y0 - 2 <= r.y <= y1 + 2:
            out.append(r)
            used_r.add(id(r))
    return out


def _kind(g):
    a = g.latex_atom()
    if g.name in S.DELIM_OPEN or a in ("(", "[", r"\{", r"\langle"):
        return "open"
    if g.name in S.DELIM_CLOSE or a in (")", "]", r"\}", r"\rangle"):
        return "close"
    if a in ("+", "-", "=", r"\times", r"\cdot", r"\leq", r"\geq", r"\neq",
             r"\approx", r"\equiv", r"\to", r"\in", r"\subset", r"\supset",
             r"\cup", r"\cap", r"\pm", "<", ">", r"\mid", ","):
        return "bin"
    return "ord"


# --------------------------------------------------------------------------
# stage 1b: binomials and cases
# --------------------------------------------------------------------------
def _stacks(glyphs, rules, used_g, used_r, depth):
    """Detect \binom{}{} (stacked pair in big parens, no bar) and cases."""
    out = []
    opens = [g for g in glyphs if id(g) not in used_g and g.name in S.DELIM_OPEN]
    for g in sorted(opens, key=lambda g: g.x0):
        if id(g) in used_g:
            continue
        # find the matching closer at the same height
        closes = [h for h in glyphs if id(h) not in used_g
                  and h.name in S.DELIM_CLOSE and h.x0 > g.x1
                  and abs(h.y0 - g.y0) < 3 and abs(h.y1 - g.y1) < 3]
        if not closes:
            continue
        close = min(closes, key=lambda h: h.x0)
        inner = [h for h in glyphs if id(h) not in used_g and h is not g
                 and h.x0 >= g.x1 - 1 and h.x1 <= close.x0 + 1
                 and h.y0 >= g.y0 - 2 and h.y1 <= g.y1 + 2]
        if not inner:
            continue
        inner_rules = [r for r in rules if id(r) not in used_r
                       and r.x0 >= g.x1 - 2 and r.x1 <= close.x0 + 2
                       and g.y0 - 2 <= r.y <= g.y1 + 2]
        if inner_rules:
            continue                       # a bar means it is a real fraction
        rows = _rows(inner)
        if len(rows) != 2:
            continue
        opener = S.DELIM_OPEN.get(g.name, "(")
        if opener != "(":
            continue                       # only round parens make a binomial
        used_g.add(id(g))
        used_g.add(id(close))
        for h in inner:
            used_g.add(id(h))
        top = build(rows[0], [], depth + 1)
        bot = build(rows[1], [], depth + 1)
        out.append(Atom(r"\binom{%s}{%s}" % (top or "{}", bot or "{}"),
                        g.x0, close.x1, (g.y0 + g.y1) / 2,
                        _dominant_size(inner) or g.size, "ord"))
    return out


def _rows(gs, gap=1.6):
    """Split glyphs into baseline rows, top to bottom."""
    if not gs:
        return []
    order = sorted(gs, key=lambda g: g.oy)
    rows, cur = [], [order[0]]
    for g in order[1:]:
        if g.oy - cur[-1].oy > max(gap, 0.45 * g.size):
            rows.append(cur)
            cur = [g]
        else:
            cur.append(g)
    rows.append(cur)
    return [sorted(r, key=lambda g: g.ox) for r in rows]


# --------------------------------------------------------------------------
# stage 2: left-to-right pass assigning superscripts and subscripts
# --------------------------------------------------------------------------
def _scripts(atoms):
    if not atoms:
        return ""
    base_size = _dominant_size(atoms)
    base_atoms = [a for a in atoms if a.size >= base_size * SCRIPT_RATIO
                  and a.kind not in ("frac", "op", "tall")]
    base_oy = _median([a.oy for a in base_atoms]) if base_atoms else atoms[0].oy

    atoms = [a for a in atoms if a.latex != ""]
    if not atoms:
        return ""
    out = []
    i = 0
    n = len(atoms)
    while i < n:
        a = atoms[i]
        eps = max(0.7, base_size * BASELINE_EPS)
        # a fraction, a big operator or a radical sits off the baseline by
        # construction; only a genuinely small glyph is a script
        if a.kind not in ("frac", "op", "tall") \
                and a.size < base_size * SCRIPT_RATIO and abs(a.oy - base_oy) > eps:
            up = a.oy < base_oy
            run = [a]
            j = i + 1
            while j < n:
                b = atoms[j]
                if b.size < base_size * SCRIPT_RATIO and abs(b.oy - base_oy) > eps \
                        and (b.oy < base_oy) == up and abs(b.oy - a.oy) < max(1.5, base_size * 0.45):
                    run.append(b)
                    j += 1
                else:
                    break
            body = _scripts(run)
            if out and out[-1] == " ":
                out.pop()
            out.append(("^{%s}" if up else "_{%s}") % body)
            i = j
            continue
        out.append(a.latex)
        i += 1
        if i < n:
            out.append(_glue(a, atoms[i]))
    return _tidy("".join(out))


def _glue(a, b):
    """A space only where LaTeX would otherwise mis-tokenise."""
    if a.latex.endswith("\\") or not a.latex or not b.latex:
        return ""
    if re.search(r"\\[A-Za-z]+$", a.latex) and re.match(r"[A-Za-z]", b.latex):
        return " "
    if b.x0 - a.x1 > max(2.5, a.size * 0.42):
        return r"\ "
    return ""


def _median(v):
    v = sorted(v)
    return v[len(v) // 2] if v else 0.0


# --------------------------------------------------------------------------
# clean-up
# --------------------------------------------------------------------------
_FUNC = sorted(S.FUNCTION_NAMES, key=len, reverse=True)


def _tidy(s):
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"(?:\\cdot\s*){3,}", r"\\cdots ", s)
    s = re.sub(r"(?:\\ldots\s*){2,}", r"\\ldots ", s)
    s = s.replace(r"\ \ ", r"\ ")
    # upright function names
    for f in ("arcsin", "arccos", "arctan", "sinh", "cosh", "tanh",
              "sin", "cos", "tan", "log", "ln", "exp", "max", "min",
              "lim", "det", "gcd"):
        s = re.sub(r"(?<![\\A-Za-z])" + f + r"(?![A-Za-z])", "\\\\" + f, s)
    for f in ("Var", "Cov", "Corr", "SD"):
        s = re.sub(r"(?<![\\A-Za-z])" + f + r"(?![A-Za-z])",
                   r"\\operatorname{%s}" % f, s)
    s = re.sub(r"\{\}\^", "^", s)
    s = re.sub(r"(?:\\ )+([_^])", r"\1", s)
    s = re.sub(r"\s*([_^])\s*", r"\1", s)
    s = _fix_scripts(s)
    # collapse single-token braces:  x^{2} -> x^2
    s = re.sub(r"([_^])\{([A-Za-z0-9])\}", r"\1\2", s)
    return _balance(s.strip())


def _balance(s):
    """Guarantee brace balance.

    Where the page geometry defeats the parser the result can be malformed, and
    a single stray brace would stop the whole document compiling.  Any excess
    is closed (or dropped) so the damage stays inside one formula.
    """
    depth = 0
    out = []
    for i, ch in enumerate(s):
        if ch == "{" and (i == 0 or s[i - 1] != "\\"):
            depth += 1
        elif ch == "}" and (i == 0 or s[i - 1] != "\\"):
            if depth == 0:
                continue          # unmatched closer: drop it
            depth -= 1
        out.append(ch)
    return "".join(out) + "}" * depth


def _arg_end(s, i):
    """Index just past the argument of the script marker at s[i]."""
    j = i + 1
    while j < len(s) and s[j] == " ":
        j += 1
    if j >= len(s):
        return j
    if s[j] == "{":
        depth = 0
        while j < len(s):
            if s[j] == "\\":       # \{ and \} are literal, not delimiters
                j += 2
                continue
            if s[j] == "{":
                depth += 1
            elif s[j] == "}":
                depth -= 1
                if depth == 0:
                    return j + 1
            j += 1
        return j
    if s[j] == "\\":
        j += 1
        while j < len(s) and s[j].isalpha():
            j += 1
        return j
    return j + 1


def _strip_braces(a):
    """Unwrap a script argument: drop outer braces and any leading marker.

    A nested build can hand back a body that itself starts with `_` or `^`
    (an operator limit whose own sub-parts were read as scripts), which would
    become `_{_i=1}` -- a double subscript.
    """
    a = a.strip()
    while True:
        if a.startswith("{") and a.endswith("}"):
            inner, depth, ok = a[1:-1], 0, True
            for ch in inner:
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth < 0:
                        ok = False
                        break
            if ok:
                a = inner.strip()
                continue
        if a[:1] in ("_", "^"):
            a = a[1:].strip()
            continue
        return a


def _cat(a, b):
    """Concatenate two script bodies without welding a command to a letter."""
    if a and b and re.search(r"\\[A-Za-z]+$", a) and re.match(r"[A-Za-z]", b):
        return a + " " + b
    return a + b


def _fix_scripts(s):
    """Repair `x_a_b` and `x^a^b`, which are hard errors in LaTeX.

    Two scripts of the same kind on one base come from a limit or an index that
    the layout pass read in two pieces, so they are joined; a third script of a
    kind already used starts a fresh (empty) base instead.
    """
    out = []
    used = set()
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "\\":
            # an escape sequence: \_ and \^ are literal text, not scripts
            j = i + 1
            if j < len(s) and s[j].isalpha():
                while j < len(s) and s[j].isalpha():
                    j += 1
            else:
                j = min(j + 1, len(s))
            out.extend(s[i:j])
            used = set()
            i = j
            continue
        if ch in "_^":
            end = _arg_end(s, i)
            arg = s[i + 1:end]
            if ch in used:
                # same kind twice: merge into the script already emitted
                for k in range(len(out) - 1, -1, -1):
                    if out[k][0] == ch:
                        out[k] = (ch, _cat(out[k][1], _strip_braces(arg)))
                        break
                else:
                    out.append((ch, _strip_braces(arg)))
            else:
                used.add(ch)
                out.append((ch, _strip_braces(arg)))
            i = end
            continue
        if ch == " " and out and isinstance(out[-1], tuple):
            i += 1
            continue
        out.append(ch)
        if not ch.isspace():
            used = set()
        i += 1
    return "".join(c if isinstance(c, str) else "%s{%s}" % c for c in out)

"""Turn analysed pages into LaTeX.

Classification is driven by the book's own typography, which is consistent
throughout:

  Goudy Sans 16.9   section head ("1.4 Combinations", "*2.6 ..." if optional)
  Goudy Sans 13.9   Summary / Problems / Theoretical Exercises / Self-Test
  Goudy Sans 10.5   the title of a boxed statement, and figure captions
  Goudy Sans 10     "Example", a proposition number, a table number
  Times bold        run-in headings ("Solution", "Proof of ...")
  Times bold italic  "Axiom 1", "Definition"

Inside a text line the maths is set in italic and in the MathTime fonts, so an
inline formula is a run of those glyphs; italic used for emphasis is told apart
by being a real word rather than single letters.
"""

import re
import statistics
from . import pagemodel as PM
from . import layout as L
from . import mathparse as MP
from . import symbols as S

SECTION = 16.0
SUBSECTION = 13.0
BOXTITLE = 10.4


# --------------------------------------------------------------------------
# inline runs: split a text group into prose and maths
# --------------------------------------------------------------------------
MATH_PUNCT = set("0123456789+-=<>()[]|/*,.:;!'")
OPEN_CLOSE = set("()[]{}")


def _mathy(g):
    if g.is_math_font:
        return True
    if g.is_italic:
        return True
    return False


def _runs(glyphs):
    """-> list of (kind, [glyphs]) with kind in {'text', 'math'}."""
    gs = [g for g in glyphs if not PM.is_blank(g) or True]
    marks = []
    for g in gs:
        if PM.is_blank(g):
            marks.append(None)
        else:
            marks.append(_mathy(g))

    # digits, operators and brackets join an adjacent maths run
    for _ in range(3):
        changed = False
        for i, g in enumerate(gs):
            if marks[i] is not False:
                continue
            if g.text not in MATH_PUNCT:
                continue
            prev = _prev_mark(marks, i)
            nxt = _next_mark(marks, i)
            if prev or nxt:
                marks[i] = True
                changed = True
        if not changed:
            break

    # a space between two maths glyphs stays inside the formula
    for i, g in enumerate(gs):
        if marks[i] is None:
            if _prev_mark(marks, i) and _next_mark(marks, i):
                marks[i] = True

    out = []
    for g, m in zip(gs, marks):
        if not m and (g.is_bold or g.is_head) and not g.is_italic:
            kind = "bold"
        else:
            kind = "math" if m else "text"
        if out and out[-1][0] == kind:
            out[-1][1].append(g)
        else:
            out.append((kind, [g]))
    return _demote_emphasis(out)


def _prev_mark(marks, i):
    for k in range(i - 1, -1, -1):
        if marks[k] is not None:
            return marks[k]
    return False


def _next_mark(marks, i):
    for k in range(i + 1, len(marks)):
        if marks[k] is not None:
            return marks[k]
    return False


WORD_RE = re.compile(r"[A-Za-z]{3,}")


def _demote_emphasis(runs):
    """Italic words are emphasis, not variables."""
    out = []
    for kind, gs in runs:
        if kind != "math":
            if kind == "bold" and all(PM.is_blank(g) for g in gs):
                kind = "text"
            out.append((kind, gs))
            continue
        core = list(gs)
        while core and (core[-1].text in ".,;:)!?" or PM.is_blank(core[-1])):
            core.pop()
        while core and PM.is_blank(core[0]):
            core.pop(0)
        if core and all(g.is_italic or PM.is_blank(g) for g in core):
            txt = "".join(g.text for g in core)
            words = [w for w in txt.split() if WORD_RE.fullmatch(w)]
            if words and len(words) * 2 >= len(txt.split()):
                tail = gs[len(core):]
                out.append(("emph", core))
                if tail:
                    out.append(("text", tail))
                continue
        out.append((kind, gs))
    return out


# --------------------------------------------------------------------------
# text escaping
# --------------------------------------------------------------------------
MERGE_MATH = re.compile(r"\$(\s*)\$")


CONTROL_WORD_END = re.compile(r"\\[A-Za-z]+$")


def _merge_adjacent_math(s):
    """`$a$ $b$` is one formula that a line break happened to split.

    Closing one formula and opening the next with nothing between them puts the
    two bodies in contact, and a control word will then swallow whatever letter
    follows it: `$\leq$$x$` becomes the undefined `\leqx` rather than
    `\leq x`.  A space is the smallest thing that keeps the tokens apart.
    """
    def join(match):
        if match.group(1):
            return r"\ "
        if (CONTROL_WORD_END.search(s[:match.start()])
                and s[match.end():match.end() + 1].isalpha()):
            return " "
        return ""

    return MERGE_MATH.sub(join, s)


CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def esc(t):
    t = CTRL.sub("", t)
    out = []
    for ch in t:
        if ch in S.TEXT_ESCAPE:
            out.append(S.TEXT_ESCAPE[ch])
        elif ch in S.UNICODE and ch not in "-'":
            out.append("$%s$" % S.UNICODE[ch])
        else:
            out.append(ch)
    s = "".join(out)
    s = s.replace("“", "``").replace("”", "''")
    s = s.replace("‘", "`").replace("’", "'")
    s = s.replace("–", "--").replace("—", "---")
    s = re.sub(r"[ \t]+", " ", s)
    return _merge_adjacent_math(s)


def line_latex(group):
    """One visual line -> LaTeX with inline maths."""
    parts = []
    for kind, gs in _runs(group.glyphs):
        if kind == "text":
            parts.append(esc("".join(g.text for g in gs)))
        elif kind == "bold":
            txt = "".join(g.text for g in gs)
            lead = " " if txt[:1] == " " else ""
            tail = " " if txt[-1:] == " " else ""
            body = esc(txt.strip())
            if body:
                parts.append("%s\\textbf{%s}%s" % (lead, body, tail or " "))
        elif kind == "emph":
            txt = "".join(g.text for g in gs)
            lead = " " if txt[:1] == " " else ""
            tail = " " if txt[-1:] == " " else ""
            parts.append("%s\\emph{%s}%s" % (lead, esc(txt.strip()), tail))
        else:
            rules = [r for r in group.rules
                     if gs[0].x0 - 2 <= r.x0 and r.x1 <= gs[-1].x1 + 2]
            tex = MP.build(gs, rules)
            if not tex.strip():
                continue
            lead = " " if gs[0].text == " " else ""
            tail = " " if gs[-1].text == " " else ""
            parts.append("%s$%s$%s" % (lead, tex, tail))
    # two inline formulas that ended up adjacent are one formula
    s = _merge_adjacent_math("".join(parts))
    return re.sub(r"[ ]{2,}", " ", s).strip()


def centre_is_math(group):
    return group.math_ratio() >= 0.35 or bool(group.rules)


def center_rows_latex(group):
    """A short inset block of plain text: its rows, for one center environment."""
    rows = []
    for row in _center_rows(group):
        txt = "".join(g.text for g in row).strip()
        if txt:
            rows.append(esc(re.sub(r"\s+", " ", txt)))
    return " \\\\\n  ".join(rows)


def _center_rows(group):
    rows, cur = [], []
    for g in sorted(group.glyphs, key=lambda g: (g.oy, g.ox)):
        if cur and g.oy - cur[-1].oy > group.size * 0.5:
            rows.append(cur)
            cur = []
        cur.append(g)
    if cur:
        rows.append(cur)
    return rows


def display_latex(group):
    rows = MP.build_rows(group.glyphs, group.rules)
    rows = [r for r in rows if r.strip()]
    if not rows:
        return ""
    if len(rows) == 1:
        return "\\[\n  %s\n\\]" % rows[0]
    body = " \\\\\n  ".join(rows)
    return "\\begin{align*}\n  %s\n\\end{align*}" % body


# --------------------------------------------------------------------------
# classification
# --------------------------------------------------------------------------
LABEL_RE = re.compile(r"^(Example|Proposition|Theorem|Corollary|Definition|Lemma|"
                      r"Axiom|Remark|Table|Figure)\b", re.I)
RUNIN_RE = re.compile(r"^(Solution|Proof|Note|Remark|Hint)\b", re.I)
ITEM_RE = re.compile(r"^\(?([a-z])[.)]\s")
NUM_RE = re.compile(r"^(\d+)\.\s")


def classify(g, bs, region_left):
    txt = g.text().strip()
    size = g.size
    if not txt:
        return "blank"
    if g.margin:
        return "margin"
    if g.head_ratio() > 0.5:
        if size >= SECTION:
            return "section"
        if size >= SUBSECTION:
            return "subsection"
        if size >= BOXTITLE:
            return "boxtitle"
        return "label"
    if g.math_ratio() >= 0.55 and g.x0 > g.measure_left + 8:
        return "display"
    if g.rules and g.math_ratio() >= 0.4:
        return "display"
    # a short, inset block between paragraphs is a centred display, not prose:
    # a small array of outcomes, a stack of equations, a boxed result
    measure = g.measure_right - g.measure_left
    inset_l = g.x0 - g.measure_left
    inset_r = g.measure_right - g.x1
    if measure > 100 and inset_l > 18 and inset_r > 18 and g.width < measure * 0.72:
        return "center"
    return "text"


# --------------------------------------------------------------------------
# paragraph assembly
# --------------------------------------------------------------------------
MATH_SPAN = re.compile(r"(?<!\\)\$(.*?)(?<!\\)\$", re.S)


def sanitize(text):
    """Last-pass repair of assembled LaTeX.

    Formulas are merged and joined after they were built, so a defect can only
    be seen once the paragraph is whole: a script marker that ended up doubled
    across a join, or an odd number of `$` left by a line the parser could not
    read.  Neither may reach the file -- both stop the document compiling.
    """
    # `_` and `^` only ever reach the file inside maths -- in text they are
    # escaped -- so the repair can run over the whole paragraph.  That matters
    # where the page defeated the parser badly enough to scramble the `$`
    # pairing, which is exactly where the malformed scripts turn up.
    text = MP._fix_scripts(text)
    text = MATH_SPAN.sub(lambda m: "$%s$" % MP._balance(m.group(1)), text)
    if len(re.findall(r"(?<!\\)\$", text)) % 2:
        # drop the unmatched opener rather than closing an empty formula
        k = text.rfind("$")
        while k > 0 and text[k - 1] == "\\":
            k = text.rfind("$", 0, k)
        if k >= 0:
            text = text[:k] + text[k + 1:]
    text = re.sub(r"(?<!\\)\$\s*(?<!\\)\$", "", text)
    return MP._balance(text)


def join_lines(lines):
    """Join visual lines into a paragraph, undoing end-of-line hyphenation."""
    out = ""
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        if not out:
            out = ln
            continue
        if out.endswith("-") and not out.endswith("--") and ln[:1].islower():
            out = out[:-1] + ln
        else:
            out = out + " " + ln
    return _merge_adjacent_math(out)

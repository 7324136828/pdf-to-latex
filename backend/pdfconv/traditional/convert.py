"""Drive the conversion: walk the PDF, emit one .tex file per chapter."""

import os
import re
import sys
import pymupdf
from . import config as C
from . import layout as L
from . import emit as E

OUT = C.OUT
EXTRA = C.EXTRA

CHAP_RE = re.compile(r"^(\d+)\s+(.*)$")


def outline(doc):
    """-> [(level, title, page0)] and the chapter start pages."""
    toc = [(lvl, t.replace("\n", " ").strip(), p - 1) for lvl, t, p in doc.get_toc()]
    chapters = []
    for lvl, title, p in toc:
        if lvl == 1:
            m = CHAP_RE.match(title)
            if m:
                chapters.append((int(m.group(1)), m.group(2).strip(), p))
    return toc, chapters


def toc_page(toc, title):
    for lvl, t, p in toc:
        if t == title:
            return p
    return None


class Writer:
    def __init__(self):
        self.buf = []
        self.para = []

    def flush(self):
        if self.para:
            txt = E.sanitize(E.join_lines(self.para))
            if txt:
                self.buf.append(txt)
                self.buf.append("")
            self.para = []

    def text(self, line, new_para):
        if new_para:
            self.flush()
        self.para.append(line)

    def block(self, s):
        self.flush()
        if s and s.strip():
            self.buf.append(E.sanitize(s))
            self.buf.append("")

    def out(self):
        self.flush()
        s = "\n".join(self.buf)
        return re.sub(r"\n{3,}", "\n\n", s)


def esc_head(t):
    return E.esc(t).strip()


class RangeConverter:
    """convert_range's state, held on an object so a page can be converted alone.

    convert_range retains paragraphs, centred runs, and pending margin labels
    across adjacent pages. The checkpointed runner instead finishes a fresh
    converter for each page, keeping cached text on its source page so bookmark
    offsets remain correct when the shared splitter assembles chapters.
    """

    def __init__(self, w):
        self.w = w
        self.pending_label = []
        self.centre_run = []

    def flush_centre(self):
        if not self.centre_run:
            return
        rows = [r for r in (E.center_rows_latex(x) for x in self.centre_run) if r]
        self.centre_run.clear()
        if rows:
            self.w.block("\\begin{center}\n  %s\n\\end{center}"
                         % " \\\\\n  ".join(rows))

    def page(self, doc, pno):
        """Convert one page into the writer, carrying state in and out."""
        w = self.w
        bs, groups = L.analyse(doc, pno)
        for g in groups:
            kind = E.classify(g, bs, g.region_left)
            if kind == "blank":
                continue
            if kind != "center":
                self.flush_centre()
            txt = g.text().strip()

            if kind == "section":
                w.flush()
                star = txt.startswith("*")
                t = txt.lstrip("*")
                m = re.match(r"^(\d+\.\d+)\s*(.*)$", t)
                title = m.group(2) if m else t
                w.block("\\section{%s}" % esc_head(title))
                continue
            if kind == "subsection":
                w.flush()
                w.block("\\subsection*{%s}" % esc_head(txt))
                continue
            if kind == "boxtitle":
                w.flush()
                w.block("\\paragraph{%s}" % esc_head(txt))
                continue
            if kind in ("label", "margin"):
                self.flush_centre()
                self.pending_label.append(txt)
                continue
            if kind == "display":
                w.block(E.display_latex(g))
                continue
            if kind == "center":
                if E.centre_is_math(g):
                    w.block(E.display_latex(g))
                else:
                    self.centre_run.append(g)
                continue

            line = E.line_latex(g)
            if self.pending_label:
                lab = re.sub(r"\s+", " ", " ".join(self.pending_label)).strip()
                self.pending_label = []
                w.flush()
                if lab:
                    w.block("\\subsection*{%s}" % esc_head(lab))
            if not line:
                continue
            indent = g.x0 > g.region_left + 6
            w.text(line, indent)

    def finish(self):
        """Commit whatever the last page left open."""
        self.flush_centre()
        self.w.flush()


def convert_range(doc, first, last, w):
    converter = RangeConverter(w)
    for pno in range(first, last + 1):
        converter.page(doc, pno)
    converter.finish()


PREAMBLE = r"""\documentclass[11pt,openany]{book}

\usepackage[T1]{fontenc}
\usepackage[utf8]{inputenc}
\usepackage{lmodern}
\usepackage[margin=1in]{geometry}
\usepackage{amsmath}
\usepackage{amssymb}
\usepackage{amsthm}
\usepackage{textcomp}
\usepackage{enumitem}
\usepackage{graphicx}
\usepackage[hidelinks]{hyperref}

\setcounter{secnumdepth}{2}
\setcounter{tocdepth}{2}

\newcommand{\figureplaceholder}[1]{%
  \begin{center}\fbox{\parbox{0.8\linewidth}{\centering\itshape #1}}\end{center}}

\title{@TITLE@}
\author{@AUTHOR@}
\date{@EDITION@}

\begin{document}
\frontmatter
\maketitle
\tableofcontents
"""


def write_scaffold(chapters):
    with open(os.path.join(OUT, "main.tex"), "w") as f:
        # plain substitution, not %-formatting: the preamble contains literal
        # per-cent signs (LaTeX comments)
        preamble = (PREAMBLE.replace("@TITLE@", C.TITLE)
                            .replace("@AUTHOR@", C.AUTHOR)
                            .replace("@EDITION@", C.EDITION))
        f.write(preamble)
        f.write("\n\\include{preface}\n")
        f.write("\n\\mainmatter\n")
        for num, title, _ in chapters:
            f.write("\\include{chapter%02d}\n" % num)
        f.write("\n\\appendix\n\\include{answers}\n\\include{solutions}\n")
        f.write("\n\\end{document}\n")


def main():
    doc = pymupdf.open(C.pdf_path())
    toc, chapters = outline(doc)
    os.makedirs(OUT, exist_ok=True)
    args = sys.argv[1:]
    want = {int(a) for a in args} if args else None
    write_scaffold(chapters)
    # the last chapter runs up to the back matter, wherever that starts
    tail = toc_page(toc, C.LAST_CHAPTER_STOP)
    if tail is None:
        tail = doc.page_count
    ends = {}
    for i, (num, title, p) in enumerate(chapters):
        end = chapters[i + 1][2] - 1 if i + 1 < len(chapters) else tail - 1
        ends[num] = (title, p, end)
    if not want:
        for slug, heading, start_title, stop_title in EXTRA:
            p = toc_page(toc, start_title)
            q = toc_page(toc, stop_title)
            if p is None or q is None:
                print("skipped %s (not in outline)" % slug)
                continue
            w = Writer()
            w.block("\\chapter{%s}" % esc_head(heading))
            convert_range(doc, p, q - 1, w)
            path = os.path.join(OUT, "%s.tex" % slug)
            with open(path, "w") as f:
                f.write(w.out())
            print("wrote %s  (pages %d-%d, %d bytes)"
                  % (path, p + 1, q, os.path.getsize(path)))

    for num, (title, p, end) in ends.items():
        if want and num not in want:
            continue
        w = Writer()
        w.block("\\chapter{%s}" % esc_head(title))
        convert_range(doc, p, end, w)
        path = os.path.join(OUT, "chapter%02d.tex" % num)
        with open(path, "w") as f:
            f.write(w.out())
        print("wrote %s  (pages %d-%d, %d bytes)" % (path, p + 1, end + 1,
                                                     os.path.getsize(path)))
    doc.close()


if __name__ == "__main__":
    main()

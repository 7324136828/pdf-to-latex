"""Static checks on the generated LaTeX.

There is no TeX installation on this machine, so the output is verified by
parsing it: brace and dollar balance, environment nesting, \\left/\\right
pairing, empty or malformed maths, and any control character that leaked
through from the PDF's symbol fonts.
"""

import collections
import glob
import os
import re
import sys

from . import config as C

CMD = re.compile(r"\\([A-Za-z]+)")
ENV_BEGIN = re.compile(r"\\begin\{([^}]*)\}")
ENV_END = re.compile(r"\\end\{([^}]*)\}")

KNOWN = set("""
chapter section subsection paragraph textbf emph text operatorname
frac binom sqrt sum prod int bigcup bigcap cdot cdots ldots vdots ddots
leq geq neq approx equiv infty to in notin subset supset subseteq supseteq
cup cap emptyset partial nabla sim backsim cong times div pm mp
alpha beta gamma delta epsilon varepsilon zeta eta theta iota kappa lambda
mu nu xi pi rho sigma tau upsilon phi varphi chi psi omega
Gamma Delta Theta Lambda Xi Pi Sigma Upsilon Phi Psi Omega
ell Re Im left right big Big bigg Bigg langle rangle mid setminus
sin cos tan arcsin arccos arctan sinh cosh tanh log ln exp max min sup inf
lim det gcd bmod pmod
begin end item label ref cite footnote
quad qquad hspace vspace medskip bigskip smallskip noindent indent
textasciitilde textasciicircum textbackslash textcent copyright square
blacksquare neg forall exists Rightarrow Longleftrightarrow leftrightarrow
leftarrow rightarrow circ bullet star ast dots
figureplaceholder documentclass usepackage newcommand setcounter
frontmatter mainmatter backmatter appendix
title author date maketitle tableofcontents include input
parbox fbox centering itshape linewidth not
""".split())


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
            if s[j] == "\\":
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


def check_file(path):
    with open(path, encoding="utf-8") as f:
        return check_text(f.read())


def check_text(src):
    """-> (issue counts, up to four examples each) for one LaTeX document."""
    issues = collections.Counter()
    examples = collections.defaultdict(list)

    def note(kind, line, text):
        issues[kind] += 1
        if len(examples[kind]) < 4:
            examples[kind].append((line, text.strip()[:150]))

    # control characters that never got decoded
    for m in re.finditer(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", src):
        line = src.count("\n", 0, m.start()) + 1
        note("undecoded control char", line,
             src[max(0, m.start() - 60):m.start() + 60])

    depth = 0
    for i, ch in enumerate(src):
        if ch == "{" and (i == 0 or src[i - 1] != "\\"):
            depth += 1
        elif ch == "}" and (i == 0 or src[i - 1] != "\\"):
            depth -= 1
            if depth < 0:
                note("unbalanced brace", src.count("\n", 0, i) + 1, src[i - 60:i + 20])
                depth = 0
    if depth:
        note("unclosed braces at EOF", src.count("\n") + 1, "depth=%d" % depth)

    stack = []
    for m in re.finditer(r"\\(begin|end)\{([^}]*)\}", src):
        line = src.count("\n", 0, m.start()) + 1
        if m.group(1) == "begin":
            stack.append((m.group(2), line))
        else:
            if not stack:
                note("stray \\end", line, m.group(0))
            elif stack[-1][0] != m.group(2):
                note("mismatched environment", line,
                     "begin{%s} ... end{%s}" % (stack[-1][0], m.group(2)))
                stack.pop()
            else:
                stack.pop()
    for name, line in stack:
        note("unclosed environment", line, name)

    pos = 0
    for para in src.split("\n\n"):
        n = len(re.findall(r"(?<!\\)\$", para))
        if n % 2:
            note("odd $ count in paragraph", src.count("\n", 0, pos) + 1, para)
        pos += len(para) + 2

    for m in re.finditer(r"\$[ \t]*\$", src):
        note("empty maths", src.count("\n", 0, m.start()) + 1, m.group(0))

    # Only the *same* marker twice on one base is an error; _a^b is fine.
    # Script arguments nest, so the argument has to be scanned with a real
    # brace matcher rather than a regex.
    i, used = 0, set()
    while i < len(src):
        ch = src[i]
        if ch == "\\":
            i += 2
            used = set()
            continue
        if ch in "_^":
            if ch in used:
                note("double %s" % ("subscript" if ch == "_" else "superscript"),
                     src.count("\n", 0, i) + 1, src[max(0, i - 40):i + 20])
            used.add(ch)
            i = _arg_end(src, i)
            continue
        if not ch.isspace():
            used = set()
        i += 1

    nl = len(re.findall(r"\\left(?![A-Za-z])", src))
    nr = len(re.findall(r"\\right(?![A-Za-z])", src))
    if nl != nr:
        note("left/right mismatch", 0, "left=%d right=%d" % (nl, nr))

    for m in CMD.finditer(src):
        if m.group(1) not in KNOWN:
            note("unknown command \\%s" % m.group(1),
                 src.count("\n", 0, m.start()) + 1,
                 src[max(0, m.start() - 50):m.start() + 50])

    return issues, examples


def main():
    paths = sorted(glob.glob(sys.argv[1] if len(sys.argv) > 1
                             else os.path.join(C.OUT, "*.tex")))
    total = collections.Counter()
    allex = collections.defaultdict(list)
    for p in paths:
        iss, ex = check_file(p)
        if iss:
            print("%-16s %s" % (os.path.basename(p),
                                ", ".join("%s=%d" % kv for kv in iss.most_common(6))))
        total.update(iss)
        for k, v in ex.items():
            allex[k].extend((os.path.basename(p), ln, t) for ln, t in v)
    print("\n===== totals =====")
    for k, v in total.most_common(30):
        print("%6d  %s" % (v, k))
        for f, ln, t in allex[k][:2]:
            print("          %s:%s  %s" % (f, ln, t.replace("\n", " / ")))


if __name__ == "__main__":
    main()

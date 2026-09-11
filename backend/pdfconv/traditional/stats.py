"""Measure the converted LaTeX: size, constructs recovered, prose accuracy.

There is no TeX here to compile against and no ground truth to diff against, so
quality is estimated two ways:

  * how much mathematical structure came through (fractions, binomials,
    integrals ...) -- a count that collapses if the parser regresses;
  * how much prose came through intact.  The failure mode that matters is two
    columns interleaving, and that leaves a signature ordinary English never
    has: a case flip inside a word ("iiffXX"), or a long run with no vowel.

    python3 stats.py
    python3 stats.py --list-garbled     # show the suspect words
"""

import collections
import glob
import os
import re
import sys

from . import config as C

CASEFLIP = re.compile(r"[a-z][A-Z]")
NOVOWEL = re.compile(r"^[^aeiouyAEIOUY]{5,}$")
WORD = re.compile(r"[A-Za-z]{3,}")

CONSTRUCTS = [
    (r"\\\[", "display equations"),
    (r"\\begin\{align\*\}", "aligned blocks"),
    (r"\\begin\{center\}", "centred blocks"),
    (r"\\frac", "fractions"),
    (r"\\binom", "binomial coefficients"),
    (r"\\int", "integrals"),
    (r"\\sum", "summations"),
    (r"\\prod", "products"),
    (r"\\sqrt", "radicals"),
    (r"\\text\{", "text inside maths"),
    (r"\\chapter", "chapters"),
    (r"\\section", "sections"),
    (r"\\subsection", "subsections"),
    (r"\\emph", "emphasis"),
    (r"\\textbf", "bold run-ins"),
]


def strip_markup(s):
    s = re.sub(r"\$[^$]*\$", " ", s)          # inline maths
    s = re.sub(r"\\\[.*?\\\]", " ", s, flags=re.S)
    s = re.sub(r"\\begin\{align\*\}.*?\\end\{align\*\}", " ", s, flags=re.S)
    s = re.sub(r"\\[A-Za-z]+", " ", s)        # commands
    return s


def main(argv):
    paths = sorted(p for p in glob.glob(os.path.join(C.OUT, "*.tex"))
                   if os.path.basename(p) != "main.tex")
    if not paths:
        raise SystemExit("no .tex files in %s -- run convert.py first" % C.OUT)

    total_words = total_bad = 0
    garbled = collections.Counter()
    print("%-18s %8s %8s %8s" % ("file", "bytes", "words", "garbled"))
    print("-" * 46)
    for p in paths:
        src = open(p).read()
        prose = strip_markup(src)
        words = WORD.findall(prose)
        bad = [w for w in words
               if CASEFLIP.search(w) or NOVOWEL.match(w) or len(w) > 22]
        garbled.update(bad)
        total_words += len(words)
        total_bad += len(bad)
        print("%-18s %8d %8d %8d" % (os.path.basename(p), os.path.getsize(p),
                                     len(words), len(bad)))

    allsrc = "".join(open(p).read() for p in paths)
    total_bytes = sum(os.path.getsize(p) for p in paths)
    pct = 100.0 * total_bad / max(1, total_words)
    print("-" * 46)
    print("%-18s %8d %8d %8d  (%.2f%%)"
          % ("TOTAL", total_bytes, total_words, total_bad, pct))

    print("\nconstructs recovered")
    for pat, name in CONSTRUCTS:
        print("  %-24s %6d" % (name, len(re.findall(pat, allsrc))))
    inline = len(re.findall(r"(?<!\\)\$", allsrc)) // 2
    print("  %-24s %6d" % ("inline formulas", inline))

    if "--list-garbled" in argv:
        print("\nsuspect words (most common first)")
        for w, n in garbled.most_common(60):
            print("  %4d  %s" % (n, w))
    else:
        print("\nmost common suspect words: %s"
              % ", ".join(w for w, _ in garbled.most_common(12)))
        print("(many are proper nouns; --list-garbled shows them all)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

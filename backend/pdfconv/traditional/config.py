"""Paths and constants for the conversion.

Everything the pipeline touches is resolved here so no script carries an
absolute path of its own.  Each value can be overridden with an environment
variable, which is what makes the pipeline runnable on another machine or
against a different copy of the book.
"""

import os
import glob

from ..paths import INPUT_DIR, OUTPUT_DIR, PROJECT_ROOT

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = str(PROJECT_ROOT)


def _env(name, default):
    return os.environ.get(name, default)


# --- the source PDF --------------------------------------------------------
# The Global Edition, whose maths is real text in the MathTime fonts.  The
# other copy of this book on the machine (the 848-page one) has no maths in its
# text layer at all -- see diagnose_source.py, which demonstrates that.
PDF_GLOB = _env(
    "ROSS_PDF",
    os.path.join(str(INPUT_DIR),
                 "a-first-course-in-probability-global-edition-*.pdf"),
)

# The US 10th edition PDF and the .txt files extracted from it.  Not used for
# the conversion; kept so diagnose_source.py can show why.
PDF_US = _env(
    "ROSS_PDF_US",
    os.path.join(str(INPUT_DIR), "a_first_course_in_probability.pdf"),
)
TXT_DIR = _env("ROSS_TXT", os.path.join(PROJECT,
                                        "A First Course in Probability (Ross)"))

# --- outputs ---------------------------------------------------------------
OUT = _env("ROSS_OUT", os.path.join(str(OUTPUT_DIR), "book"))
GLYPHBOX = os.path.join(HERE, "glyphbox.json")
FONTDUMP = _env("ROSS_FONTDUMP", os.path.join(HERE, "fontdump"))

# --- document structure ----------------------------------------------------
# Front and back matter, located by their outline titles rather than by page
# number: (output name, heading, first outline entry, first entry after it).
EXTRA = [
    ("preface", "Preface", "Preface", "1 Combinatorial Analysis"),
    ("answers", "Answers to Selected Problems", "Answers to Selected Problems",
     "Solutions to Self-Test Problems and Exercises"),
    ("solutions", "Solutions to Self-Test Problems and Exercises",
     "Solutions to Self-Test Problems and Exercises", "Index"),
]

# The chapters stop where the back matter starts.
LAST_CHAPTER_STOP = "Answers to Selected Problems"

TITLE = _env("ROSS_TITLE", "A First Course in Probability")
AUTHOR = _env("ROSS_AUTHOR", "Sheldon Ross")
EDITION = _env("ROSS_EDITION", "Tenth Edition (Global Edition)")


def pdf_path():
    """-> the source PDF, resolving a glob and failing loudly if it is missing."""
    if os.path.isfile(PDF_GLOB):
        return PDF_GLOB
    hits = sorted(glob.glob(PDF_GLOB))
    if not hits:
        raise SystemExit(
            "source PDF not found: %s\n"
            "Set ROSS_PDF to the Global Edition PDF, e.g.\n"
            "  ROSS_PDF=/path/to/book.pdf python3 pipeline.py" % PDF_GLOB)
    return hits[0]

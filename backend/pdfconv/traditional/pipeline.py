"""Run the whole conversion end to end.

    python -m pdfconv.traditional.pipeline                 # convert, validate, measure
    python -m pdfconv.traditional.pipeline --diagnose      # also re-check the source
    python -m pdfconv.traditional.pipeline --glyphbox      # regenerate glyphbox.json
    python -m pdfconv.traditional.pipeline --chapters 1 5  # only those chapters

The stages, in order:

  diagnose_source   which copy of the book has usable mathematics       (optional)
  make_glyphbox     measure true glyph extents from the embedded fonts  (optional)
  identify_glyphs   render glyphs whose encoding lies about them        (optional)
  convert           PDF -> LaTeX
  check             parse the LaTeX for anything that would not compile
  stats             size, constructs recovered, prose accuracy
"""

import os
import subprocess
import sys
import time

from . import config as C

PACKAGE = __package__ or "pdfconv.traditional"


def run(script, args=(), required=True):
    """Run one stage in its own interpreter so a crash cannot poison the rest."""
    label = script
    module = "%s.%s" % (PACKAGE, script)
    print("\n" + "=" * 70)
    print(">>> %s %s" % (label, " ".join(args)))
    print("=" * 70)
    sys.stdout.flush()          # keep our output in step with the child's
    t0 = time.time()
    rc = subprocess.call([sys.executable, "-m", module] + list(args))
    dt = time.time() - t0
    if rc != 0:
        print("\n%s exited %d after %.1fs" % (label, rc, dt))
        if required:
            raise SystemExit(rc)
    else:
        print("\n%s finished in %.1fs" % (label, dt))
    return rc


def main(argv):
    chapters = []
    if "--chapters" in argv:
        i = argv.index("--chapters")
        chapters = [a for a in argv[i + 1:] if a.isdigit()]

    try:
        pdf = C.pdf_path()
    except SystemExit as exc:
        print(exc)
        return 1
    print("source : %s" % pdf)
    print("output : %s" % C.OUT)
    sys.stdout.flush()

    if "--diagnose" in argv:
        run("diagnose_source", required=False)
    if "--glyphbox" in argv:
        run("make_glyphbox", required=False)
    if "--identify" in argv:
        run("identify_glyphs", required=False)

    run("convert", chapters)

    if chapters:
        print("\npartial run (chapters %s): skipping validation and stats,\n"
              "which only mean anything for the whole book." % ", ".join(chapters))
        return 0

    rc = run("check", required=False)
    run("stats", required=False)

    print("\n" + "=" * 70)
    print("done -- LaTeX in %s" % C.OUT)
    print("compile with:  cd %s && pdflatex main && pdflatex main" % C.OUT)
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

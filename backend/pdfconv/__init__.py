"""Recursive PDF -> LaTeX conversion.

Two converters live under this package, both migrated from the original
top-level ``traditional/`` and ``code/`` folders:

``pdfconv.traditional``
    Text-layer conversion built on PyMuPDF.  It reconstructs mathematics from
    the PDF's own glyphs, so it is exact when the PDF has a usable text layer
    and useless when it does not.  Tried first.

``pdfconv.pdf2tex``
    Formula-aware OCR (Pix2Text) that reads rendered page images.  Slower, and
    it needs models, but it works on scanned pages.  Used as the fallback.

``pdfconv.orchestrator`` walks ``input/``, runs the two in that order and
mirrors the tree into ``output/``.
"""

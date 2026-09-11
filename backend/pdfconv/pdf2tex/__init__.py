"""Formula-aware OCR PDF -> LaTeX conversion (migrated from ``code/code``).

``main`` keeps the original recursive command and uses the shared
``pdfconv.checkpoint`` and ``pdfconv.chapter_split`` packages. ``ocr`` extracts
pages; ``math_ocr`` holds the Pix2Text backend, extended with a
CUDA path for NVIDIA GPUs alongside the original CoreML and CPU paths.
``runner`` adapts the pipeline to one PDF at a time so the orchestrator can
reuse a single loaded model across every fallback.
"""

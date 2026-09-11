"""Text-layer PDF -> LaTeX conversion (migrated from ``traditional/build``).

The stage modules keep their original names and behaviour:

    pagemodel   positioned glyphs and vector rules for one page
    layout      banding, column cuts, grouping into logical lines
    mathparse   built-up mathematics (fractions, radicals, scripts) -> LaTeX
    emit        classification and LaTeX for one group
    convert     the whole-book driver kept from the original checkout
    check       static validation of generated LaTeX
    stats       size and recovered-construct measurements

``runner`` is the generic per-PDF entry point used by the recursive
orchestrator; ``pipeline`` remains the original whole-book command.
"""

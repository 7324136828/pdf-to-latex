"""Serialize escaped OCR prose and LaTeX equations into standalone documents."""

import re


_ESCAPES = {
    "\\": r"\textbackslash{}",
    "{": r"\{",
    "}": r"\}",
    "%": r"\%",
    "&": r"\&",
    "$": r"\$",
    "_": r"\_",
    "#": r"\#",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def escape_text(text: str) -> str:
    """Escape prose once, retaining Unicode, whitespace, and page separators.

    Equation output must bypass this function so its LaTeX syntax survives.
    """
    return "".join(_ESCAPES.get(character, character) for character in text)


def latex_document(body: str, source: str, heading: str) -> str:
    """Wrap escaped prose and raw math in a XeLaTeX/LuaLaTeX document.

    ``source`` and ``heading`` are plain text. Only page separators and invalid
    control characters are changed in ``body``; equation syntax is preserved.
    """
    body = _CONTROL_CHARACTERS.sub("", body.replace("\n\f\n", "\n\\clearpage\n"))
    source = escape_text(_CONTROL_CHARACTERS.sub("", source))
    heading = escape_text(_CONTROL_CHARACTERS.sub("", heading))
    preamble = r"""% Compile this UTF-8 document with XeLaTeX or LuaLaTeX.
\documentclass[11pt]{article}
\usepackage{fontspec}
\usepackage{amsmath,amssymb,mathtools,bm}
\usepackage[margin=1in]{geometry}
\setlength{\parindent}{0pt}
\setlength{\parskip}{0.5em}
\begin{document}
"""
    return (
        preamble
        + "\\section*{" + heading + "}\n"
        + "{\\small Source: " + source + "\\par}\n\\medskip\n"
        + body
        + "\n\\end{document}\n"
    )

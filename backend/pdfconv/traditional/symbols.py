"""Glyph -> LaTeX translation tables.

Three sources feed in:
  * glyph names recovered from /Encoding /Differences (MTEX, MTSY, RMTMI, ...)
  * Unicode that PyMuPDF resolved on its own
  * NewOptr2k, whose glyph names are arbitrary and were identified by
    rendering each one; the font is WinAnsi-encoded with no /Differences and
    a single subset (xref 2564) reused document-wide, so this map is fixed.
"""

# --- NewOptr2k: names are meaningless, values verified by rendering ---------
NEWOPTR = {
    "q": r"\infty",
    "Ú": r"\geq",          # Uacute
    "…": r"\leq",          # ellipsis
    "L": r"\approx",
    "K": r"\equiv",
    "Z": r"\neq",
    "*": r"\times",
    "#": r"\vdots",
    "3": r"\Longleftrightarrow",
    ";": r"\pm",
    ".": r"\blacksquare",
    "(": r"\subset",
    ")": r"\supset",
}

# --- MTEX / MTSY / RMTMI glyph names ---------------------------------------
BIG_OPS = {
    "summationdisplay": r"\sum", "summationtext": r"\sum",
    "productdisplay": r"\prod", "producttext": r"\prod",
    "integraldisplay": r"\int", "integraltext": r"\int",
    "uniondisplay": r"\bigcup", "uniontext": r"\bigcup",
    "intersectiondisplay": r"\bigcap", "intersectiontext": r"\bigcap",
}

RADICALS = {"radicalbig", "radicalBig", "radicalbigg", "radicalBigg",
            "radicaltp", "radicalvertex", "radicalbt", "radical"}

# extensible delimiter pieces that carry no content of their own
# A tall delimiter is drawn as top + extension + bottom pieces.  Only the top
# piece is turned into a delimiter; the rest carry no content of their own.
DELIM_EXT = {"parenleftex", "parenrightex", "bracketleftex", "bracketrightex",
             "braceex", "vextendsingle", "vextenddouble",
             "bracehtipdownleft", "bracehtipdownright",
             "bracehtipupleft", "bracehtipupright",
             "parenleftbt", "parenrightbt",
             "bracketleftbt", "bracketrightbt",
             "braceleftbt", "bracerightbt",
             "braceleftmid", "bracerightmid"}

DELIM_OPEN = {
    "parenleftbig": "(", "parenleftBig": "(", "parenleftbigg": "(", "parenleftBigg": "(",
    "parenlefttp": "(",
    "bracketleftbig": "[", "bracketleftBig": "[", "bracketleftbigg": "[",
    "bracketleftBigg": "[", "bracketlefttp": "[",
    "braceleftbig": r"\{", "braceleftBig": r"\{", "braceleftbigg": r"\{",
    "braceleftBigg": r"\{", "bracelefttp": r"\{",
    "angbracketleft": r"\langle",
}
DELIM_CLOSE = {
    "parenrightbig": ")", "parenrightBig": ")", "parenrightbigg": ")", "parenrightBigg": ")",
    "parenrighttp": ")",
    "bracketrightbig": "]", "bracketrightBig": "]", "bracketrightbigg": "]",
    "bracketrightBigg": "]", "bracketrighttp": "]",
    "bracerightbig": r"\}", "bracerightBig": r"\}", "bracerightbigg": r"\}",
    "bracerightBigg": r"\}", "bracerighttp": r"\}",
    "angbracketright": r"\rangle",
}

GLYPH = {
    "union": r"\cup", "intersection": r"\cap", "element": r"\in",
    "emptyset": r"\emptyset", "arrowright": r"\to", "asteriskmath": "*",
    "periodcentered": r"\cdot", "bullet": r"\bullet", "openbullet": r"\circ",
    "prime": "'", "radical": r"\sqrt", "minus": "-", "plus": "+",
    "equal": "=", "bar": r"\mid", "backslash": r"\setminus",
    "braceleft": r"\{", "braceright": r"\}", "semicolon": ";",
    "negationslash": r"\!\not",
    "slashbig": "/", "slashBig": "/", "slashBigg": "/",
    "less": "<", "greater": ">", "slash": "/", "period": ".",
    "parenleft": "(", "parenright": ")",
    "alpha": r"\alpha", "beta": r"\beta", "gamma": r"\gamma",
    "epsilon": r"\varepsilon", "epsilon1": r"\epsilon", "zeta": r"\zeta",
    "theta": r"\theta", "lambda": r"\lambda", "mu": r"\mu", "nu": r"\nu",
    "xi": r"\xi", "pi": r"\pi", "rho": r"\rho", "sigma": r"\sigma",
    "phi": r"\phi", "chi": r"\chi", "lscript": r"\ell",
    "Theta": r"\Theta", "Theta1": r"\Theta", "Gamma1": r"\Gamma",
    "Phi1": r"\Phi", "Psi1": r"\Psi", "Omega1": r"\Omega",
}

# --- plain Unicode that PyMuPDF already resolved ---------------------------
UNICODE = {
    "−": "-", "×": r"\times", "÷": r"\div", "±": r"\pm",
    "≤": r"\leq", "≥": r"\geq", "≠": r"\neq", "≈": r"\approx",
    "≡": r"\equiv", "∞": r"\infty", "→": r"\to",
    "∈": r"\in", "∉": r"\notin", "⊂": r"\subset", "⊃": r"\supset",
    "⊆": r"\subseteq", "⊇": r"\supseteq",
    "∪": r"\cup", "∩": r"\cap", "∅": r"\emptyset",
    "√": r"\sqrt", "∑": r"\sum", "∏": r"\prod", "∫": r"\int",
    "∂": r"\partial", "∇": r"\nabla",
    "∼": r"\sim", "∽": r"\backsim", "≅": r"\cong",
    "…": r"\ldots", "⋯": r"\cdots", "⋮": r"\vdots", "⋱": r"\ddots",
    "·": r"\cdot", "•": r"\bullet", "∘": r"\circ", "∗": "*",
    "′": "'", "″": "''",
    "←": r"\leftarrow", "↔": r"\leftrightarrow",
    "⇒": r"\Rightarrow", "⇔": r"\Longleftrightarrow",
    "¬": r"\neg", "∀": r"\forall", "∃": r"\exists",
    "¢": r"\text{\textcent}", "°": r"^\circ",
    "Θ": r"\Theta", "Ω": r"\Omega", "Φ": r"\Phi",
    "λ": r"\lambda", "μ": r"\mu", "σ": r"\sigma", "π": r"\pi",
    "α": r"\alpha", "β": r"\beta", "γ": r"\gamma", "δ": r"\delta",
    "ε": r"\varepsilon", "ζ": r"\zeta", "η": r"\eta", "θ": r"\theta",
    "ν": r"\nu", "ξ": r"\xi", "ρ": r"\rho", "τ": r"\tau",
    "φ": r"\phi", "χ": r"\chi", "ψ": r"\psi", "ω": r"\omega",
    "Γ": r"\Gamma", "Δ": r"\Delta", "Σ": r"\Sigma", "Π": r"\Pi",
    "ℓ": r"\ell", "ℜ": r"\Re", "ℑ": r"\Im",
}

# --- one-off figure-label fonts -------------------------------------------
# Every entry here was read off a rendering of the glyph in place; run
# `python3 identify_glyphs.py` to regenerate those crops and re-check.  The
# encodings of these fonts say nothing about what is drawn, so guessing from
# the character name gets it wrong more often than not.
MINOR = {
    ("MathematicalPiLTStd-1", "5"): "=",
    ("MathematicalPiLTStd-1", "2"): r"\geq",
    ("MathematicalPiLTStd-1", "<"): r"\cup",
    ("MathematicalPiLTStd-1", "U"): r"\theta",
    ("MathematicalPiLTStd-1", "3"): r"\times",
    ("MathematicalPiLTStd-1", "1"): "+",
    ("MathematicalPiLTStd-1", "#"): r"\leq",
    ("MathematicalPiLTStd-4", "<"): r"\cup",
    ("MathematicalPiLTStd-3", ","): r"\cap",
    ("Allphf", "*"): r"\Longleftrightarrow",
    ("Allphf", "%"): r"\leftrightarrow",
    ("PearsonMATHPRO01", "m"): r"\mu",
    ("PearsonMATHPRO01", "s"): r"\sigma",
    ("PearsonMATHPRO01", "a"): r"\alpha",
    ("PearsonMATHPRO01", "b"): r"\beta",
    ("PearsonMATHPRO01", "u"): r"\mu",
    ("MSBM10", "∼"): r"\sim",
    ("MSAM10", "∽"): r"\sim",
    ("wasy9", "¢"): r"\text{\textcent}",
    ("wasy10", "\x02"): r"\square",
    ("MathematicalPi-One", "\x02"): r"\infty",
    ("MathematicalPiOneOblique", "u"): r"\mu",
    ("Symbol", "∂"): r"\partial",
    ("Symbol", "©"): r"\copyright",
}

# TeX-reserved characters appearing as literal text
TEXT_ESCAPE = {
    "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_",
    "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}", "\\": r"\textbackslash{}",
}

# Multi-letter operator names that should be upright in math mode
FUNCTION_NAMES = [
    "arcsin", "arccos", "arctan", "sinh", "cosh", "tanh", "sin", "cos", "tan",
    "log", "ln", "exp", "max", "min", "sup", "inf", "lim", "det", "gcd",
    "Var", "Cov", "Corr", "SD", "E", "P",
]

"""LaTeX serialization checks independent of OCR models or a TeX install."""

import unittest

from pdfconv.pdf2tex.latex_output import escape_text, latex_document


class LatexOutputTests(unittest.TestCase):
    def test_prose_special_characters_are_escaped_once(self):
        prose = "\\{}%&$_#~^"
        self.assertEqual(
            escape_text(prose),
            r"\textbackslash{}\{\}\%\&\$\_\#\textasciitilde{}\textasciicircum{}",
        )

    def test_unicode_whitespace_and_page_separators_survive_escaping(self):
        prose = "Résumé — α ≥ β\n\t次の章\n\f\nNext page"
        self.assertEqual(escape_text(prose), prose)

    def test_equation_tokens_are_preserved_verbatim(self):
        body = (
            escape_text("A 5% rate & an equation:\n")
            + r"\(a_i = \frac{x^2}{\sqrt{y}}\)"
            + "\n"
            + r"\[\begin{aligned}x &= y + 1 \\ z &= \sum_{i=0}^{n} i\end{aligned}\]"
        )
        document = latex_document(body, "book.pdf", "Chapter 1")
        self.assertIn("\\medskip\n" + body + "\n\\end{document}", document)
        self.assertIn(r"\usepackage{fontspec}", document)
        self.assertIn(r"\usepackage{amsmath,amssymb,mathtools,bm}", document)

    def test_metadata_cannot_become_latex_commands(self):
        source = "nested/file_1 & 2%.pdf"
        heading = r"Chapter {1}: \input{other} $cash#"
        document = latex_document("Body", source, heading)
        self.assertIn(r"Source: nested/file\_1 \& 2\%.pdf", document)
        self.assertIn(
            r"\section*{Chapter \{1\}: \textbackslash{}input\{other\} \$cash\#}",
            document,
        )
        self.assertNotIn(r"\input{other}", document)

    def test_page_separators_become_page_breaks(self):
        document = latex_document("Page one\n\f\nPage two\n\f\nPage three", "x", "y")
        self.assertIn("Page one\n\\clearpage\nPage two\n\\clearpage\nPage three", document)
        self.assertNotIn("\f", document)

    def test_invalid_controls_are_removed_without_changing_math_or_whitespace(self):
        body = "Text\x00\x01\x0b\x7f\x85\n\t" + r"\(x_1^2\)"
        document = latex_document(body, "book\x00.pdf", "Chapter\x7f 1")
        self.assertIn("Text\n\t" + r"\(x_1^2\)", document)
        self.assertIn("Source: book.pdf", document)
        self.assertIn(r"\section*{Chapter 1}", document)
        for character in ("\x00", "\x01", "\x0b", "\x7f", "\x85"):
            self.assertNotIn(character, document)


if __name__ == "__main__":
    unittest.main()

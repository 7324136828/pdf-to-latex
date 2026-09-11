"""Chapter splitting: finding the boundaries, naming the files, publishing them.

Both converters hand the same shape to this package -- one string per page --
so these cases are written against that shape rather than against either route.
"""

import csv
import tempfile
import unittest
from pathlib import Path

from pdfconv import chapter_split
from pdfconv.checkpoint import CheckpointStore


def plain(heading: str, body: str, source: str) -> str:
    """The simplest possible renderer, so tests see the body unchanged."""
    return body


class HeadingTests(unittest.TestCase):
    def test_bookmarks_pick_the_chapter_level_and_keep_all_text(self):
        toc = [[1, "Book", 1], [2, "Chapter 1", 1], [3, "1.1 Detail", 1],
               [2, "Chapter 2", 2], [2, "Chapter 3", 2]]
        headings = chapter_split.bookmark_headings(toc, ["First", "Second"])
        self.assertEqual([h.label for h in headings], ["Chapter 1", "Chapter 2"])
        text = "First" + chapter_split.PAGE_SEPARATOR + "Second"
        sections = chapter_split.build_sections(text, headings)
        self.assertEqual("".join(body for _, body in sections), text)

    def test_text_without_structure_becomes_one_section(self):
        self.assertEqual(chapter_split.detect_structure("Unstructured text", 0),
                         ("complete text", []))
        self.assertEqual(chapter_split.build_sections("body", []),
                         [("Complete Text", "body")])

    def test_headings_after_the_first_page_keep_a_front_matter_section(self):
        pages = ["Front matter prose.", "Chapter 1 Beginnings\nBody.",
                 "Chapter 2 After\nMore."]
        text = chapter_split.PAGE_SEPARATOR.join(pages)
        headings = chapter_split.bookmark_headings(
            [[1, "Chapter 1 Beginnings", 2], [1, "Chapter 2 After", 3]], pages)
        sections = chapter_split.build_sections(text, headings)
        self.assertEqual(sections[0][0], "Front Matter")
        self.assertEqual("".join(body for _, body in sections), text)


class LatexHeadingTests(unittest.TestCase):
    def test_emitted_headings_keep_punctuation_and_plain_labels(self):
        pages = [r"\section{Research \& Development}" + "\nFirst body.",
                 r"\section*{Sets \{A\} and 50\% gains}" + "\nSecond body."]
        split = chapter_split.plan(Path("book.pdf"), pages, [], render=plain,
                                   detect=chapter_split.headings_from_latex)
        self.assertEqual(split.method, "LaTeX headings")
        self.assertEqual([section.heading for section in split.sections],
                         ["Research & Development", "Sets {A} and 50% gains"])
        self.assertEqual("".join(section.body for section in split.sections),
                         chapter_split.PAGE_SEPARATOR.join(pages))

    def test_explicit_headings_do_not_use_prose_title_filters(self):
        text = "\\section{DNA}\r\nBody.\r\n  \\section{e-commerce}\r\nMore."
        headings = chapter_split.latex_headings(text)
        self.assertEqual([heading.label for heading in headings], ["DNA", "e-commerce"])
        self.assertEqual(headings[1].start, text.index("  \\section"))

    def test_escape_decoding_is_one_pass(self):
        text = r"\section{A \textbackslash{}textasciitilde\{\} B \$ C \_ D}"
        headings = chapter_split.latex_headings(text)
        self.assertEqual(headings[0].label, r"A \textasciitilde{} B $ C _ D")

    def test_chapters_take_precedence_over_their_sections(self):
        text = ("\\chapter{Beginnings}\n\\section{First}\nBody.\n"
                "\\chapter{Endings}\n\\section{Last}\nMore.")
        self.assertEqual([heading.label for heading in chapter_split.latex_headings(text)],
                         ["Beginnings", "Endings"])

    def test_bookmarks_take_precedence_over_emitted_sections(self):
        pages = ["\\section{First detail}\nBody.", "\\section{Last detail}\nMore."]
        split = chapter_split.plan(
            Path("book.pdf"), pages, [[1, "Chapter 1", 1], [1, "Chapter 2", 2]],
            render=plain, detect=chapter_split.headings_from_latex)
        self.assertEqual(split.method, "PDF bookmarks")
        self.assertEqual([section.heading for section in split.sections],
                         ["Chapter 1", "Chapter 2"])

    def test_latex_fragments_never_fall_back_to_prose_heuristics(self):
        pages = ["Chapter 1 Accidental prose\n" + r"4 $Ea$. What are the odds \end{center}",
                 "Chapter 2 Accidental prose\n" + r"8 P(X<=x) = \frac{1}{2}"]
        split = chapter_split.plan(Path("book.pdf"), pages, [], render=plain,
                                   minimum=0, detect=chapter_split.headings_from_latex)
        self.assertEqual(split.method, "complete text")
        self.assertEqual(len(split.sections), 1)
        self.assertEqual(split.sections[0].body, chapter_split.PAGE_SEPARATOR.join(pages))

    def test_inline_commands_and_subsections_are_not_chapter_boundaries(self):
        text = (r"Ordinary prose with \section{Mention}" + "\n"
                + r"\subsection{Detail}" + "\n" + r"% \section{Comment}" + "\n"
                + r"\section{Fragment}\end{center}")
        self.assertEqual(chapter_split.latex_headings(text), [])

    def test_non_latin_headings_remain_distinct(self):
        text = "\\section{\u7b2c\u4e00\u7ae0}\nBody.\n\\section{\u7b2c\u4e8c\u7ae0}\nMore."
        headings = chapter_split.latex_headings(text)
        self.assertEqual(len(headings), 2)
        self.assertNotEqual(headings[0].key, headings[1].key)


class NamingTests(unittest.TestCase):
    def test_long_unicode_filenames_are_distinct_and_portable(self):
        first = chapter_split.flat_section_filename("書" * 100 + "one", 0, "章" * 100)
        second = chapter_split.flat_section_filename("書" * 100 + "two", 0, "章" * 100)
        self.assertLessEqual(len(first.encode()), 240)
        self.assertNotEqual(first, second)

    def test_path_separators_and_control_characters_are_removed(self):
        name = chapter_split.safe_filename("a/b\\c:d*e?\x01f")
        self.assertNotIn("/", name)
        self.assertNotIn("\\", name)
        self.assertNotIn("\x01", name)

    def test_an_empty_heading_still_names_a_file(self):
        self.assertIn("Untitled Section", chapter_split.safe_filename("   "))


class PlanTests(unittest.TestCase):
    def test_the_plan_mirrors_the_input_folder_and_numbers_sections(self):
        pages = ["Chapter 1 One\nBody one.", "Chapter 2 Two\nBody two."]
        split = chapter_split.plan(Path("math/deep/book.pdf"), pages,
                                   [[1, "Chapter 1 One", 1], [1, "Chapter 2 Two", 2]],
                                   render=plain)
        self.assertEqual(split.method, "PDF bookmarks")
        self.assertEqual([s.heading for s in split.sections],
                         ["Chapter 1 One", "Chapter 2 Two"])
        for section in split.sections:
            self.assertEqual(Path(section.file).parent, Path("math/deep"))
            self.assertTrue(section.file.endswith(".tex"))
        self.assertIn("_000 - ", split.sections[0].file)
        self.assertIn("_001 - ", split.sections[1].file)

    def test_splitting_never_loses_text(self):
        pages = ["Chapter 1 One\nBody one.", "Chapter 2 Two\nBody two."]
        split = chapter_split.plan(Path("book.pdf"), pages, [], render=plain,
                                   minimum=0)
        joined = "".join(s.body for s in split.sections)
        self.assertEqual(joined, chapter_split.PAGE_SEPARATOR.join(pages))

    def test_the_renderer_receives_heading_body_and_source(self):
        seen = []

        def record(heading, body, source):
            seen.append((heading, source))
            return f"[{heading}]{body}"

        split = chapter_split.plan(Path("sub/book.pdf"), ["Only page."], [],
                                   render=record)
        self.assertEqual(seen, [("Complete Text", "sub/book.pdf")])
        self.assertTrue(split.sections[0].body.startswith("[Complete Text]"))


class PublishTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.destination = self.root / "output"
        self.store = CheckpointStore(self.root / "tmp", "test", {})
        self.settings = chapter_split.settings_for(0)

    def entry(self, key="book.pdf"):
        return self.store.document(key, {"sha256": "x"})

    def publish(self, pages, toc=(), key="book.pdf"):
        entry = self.entry(key)
        split = chapter_split.plan(Path(key), list(pages), list(toc),
                                   render=plain, minimum=0)
        outputs = chapter_split.publish(entry, split, self.destination,
                                        settings=self.settings)
        return entry, outputs

    def test_published_files_exist_and_are_recorded(self):
        entry, outputs = self.publish(["Chapter 1 One\nBody.",
                                       "Chapter 2 Two\nMore."])
        self.assertEqual(len(outputs), 2)
        for row in outputs:
            self.assertTrue((self.destination / row["file"]).is_file())
        self.assertTrue(entry.split_done)
        self.assertTrue(entry.publication_matches(self.settings, self.destination))

    def test_republishing_with_fewer_sections_removes_the_stale_files(self):
        _, first = self.publish(["Chapter 1 One\nBody.", "Chapter 2 Two\nMore."])
        stale = self.destination / first[-1]["file"]
        self.assertTrue(stale.is_file())

        _, second = self.publish(["Just one page of prose with no headings."])
        self.assertEqual(len(second), 1)
        self.assertFalse(stale.exists())

    def test_a_hand_edited_output_is_never_deleted(self):
        _, first = self.publish(["Chapter 1 One\nBody.", "Chapter 2 Two\nMore."])
        edited = self.destination / first[-1]["file"]
        edited.write_text("someone changed this", encoding="utf-8")

        self.publish(["Just one page of prose with no headings."])
        self.assertTrue(edited.is_file())
        self.assertEqual(edited.read_text(encoding="utf-8"), "someone changed this")

    def test_publication_does_not_match_after_an_output_is_deleted(self):
        entry, outputs = self.publish(["Chapter 1 One\nBody."])
        self.assertTrue(entry.publication_matches(self.settings, self.destination))
        (self.destination / outputs[0]["file"]).unlink()
        self.assertFalse(entry.publication_matches(self.settings, self.destination))

    def test_changed_split_settings_do_not_match_a_previous_publication(self):
        entry, _ = self.publish(["Chapter 1 One\nBody."])
        other = chapter_split.settings_for(3000)
        self.assertFalse(entry.publication_matches(other, self.destination))

    def test_the_catalog_lists_every_published_chapter(self):
        self.publish(["Chapter 1 One\nBody.", "Chapter 2 Two\nMore."])
        path = chapter_split.write_catalog(self.store.documents(), self.destination)
        rows = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(rows[0].split("\t")[0], "source_file")
        self.assertEqual(len(rows), 3)          # header plus two sections

    def test_catalog_uses_source_path_instead_of_route_checkpoint_key(self):
        entry, _ = self.publish(["Body."], key="sub/book.pdf#traditional")
        entry.data["source"] = "sub/book.pdf"
        entry.data["converter"] = "traditional"
        path = chapter_split.write_catalog(self.store.documents(), self.destination)
        with path.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream, delimiter="\t"))
        self.assertEqual(rows[0]["source_file"], "sub/book.pdf")
        self.assertEqual(rows[0]["converter"], "traditional")


if __name__ == "__main__":
    unittest.main()

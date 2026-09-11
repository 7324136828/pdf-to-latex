"""Heading detection: where one chapter ends and the next begins.

Moved unchanged from the OCR command, which was the only caller; both
converters now split their output the same way. Two sources of structure are
tried in order -- the PDF's own bookmarks, then heuristics over the recognised
text -- because a book with a usable outline should never be at the mercy of a
regular expression.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Pages are joined with this before splitting, so a heading's character offset
#: can be traced back to the page it was found on.
PAGE_SEPARATOR = "\n\f\n"


NUMBER_WORDS = (
    "one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    "thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty"
)
IDENTIFIER = rf"(?:\d{{1,3}}|[IVXLCDM]{{1,12}}|{NUMBER_WORDS})"
CHAPTER_RE = re.compile(
    rf"^\s*(chapter|chap\.?)\s+({IDENTIFIER})\b"
    rf"(?:\s*[:.\-–—]\s*|\s+)?(.*?)\s*$",
    re.IGNORECASE,
)
DIVISION_RE = re.compile(
    rf"^\s*(book|part)\s+({IDENTIFIER})\b"
    rf"(?:\s*[:.\-–—]\s*|\s+)?(.*?)\s*$",
    re.IGNORECASE,
)
NUMBERED_RE = re.compile(
    r"^\s*(\d{1,3})(?:[.)]|[ \t]{1,4})"
    r"([A-Za-z][A-Za-z0-9 ,:'\"&/()\-–—]{2,110})\s*$"
)
ROMAN_SECTION_RE = re.compile(r"^\s*([IVXLCDM]{1,10})\.\s+(.{2,120}?)\s*$")
IDENTIFIER_ONLY_RE = re.compile(rf"^\s*({IDENTIFIER})[.]?\s*$", re.IGNORECASE)
FIRST_SECTION_RE = re.compile(r"^\s*(\d{1,3})\.1(?:\D|$)")

@dataclass(frozen=True)
class Heading:
    start: int
    label: str
    key: str
    kind: str
    number: int | None = None


def normalize_key(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", label.casefold()).strip()


def acceptable_line(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and len(stripped) <= 150 and "..." not in stripped


def identifier_number(identifier: str) -> int | None:
    value = identifier.casefold().rstrip(".")
    words = NUMBER_WORDS.split("|")
    if value in words:
        return words.index(value) + 1
    if value.isdigit():
        return int(value)
    roman_values = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
    total = 0
    previous = 0
    for character in reversed(value):
        current = roman_values.get(character)
        if current is None:
            return None
        if current < previous:
            total -= current
        else:
            total += current
            previous = current
    return total or None


def heading_like_tail(tail: str) -> bool:
    tail = tail.strip()
    if not tail:
        return True
    if len(tail.split()) > 14:
        return False
    if not tail[0].isalnum() or (tail[0].isalpha() and not tail[0].isupper()):
        return False
    first_word = re.sub(r"[^a-z]", "", tail.casefold().split()[0])
    return first_word not in {
        "we", "this", "that", "is", "was", "has", "will",
        "presented", "presents", "describes", "discusses", "contains", "continues",
    }


#: LaTeX that a chapter title never contains.  These heuristics were written
#: against OCR prose, and the text-layer converter feeds them LaTeX instead: a
#: line like "4 $Ea$. What is the odds ... \end{center}" otherwise reads as a
#: numbered heading and becomes a filename.
LATEX_MARKUP_RE = re.compile(r"\\[A-Za-z]+|\\[\\{}$&#_%]|[${}]|\^\{|_\{")


def plausible_title(title: str) -> bool:
    """Return whether text has the shape of a chapter title, not prose/data."""
    title = " ".join(title.split())
    letters = [character for character in title if character.isalpha()]
    first_letter = next((character for character in title if character.isalpha()), "")
    normalized = title.casefold()
    return (
        1 <= len(title.split()) <= 18
        and len(title) <= 150
        and len(letters) >= 4
        and bool(first_letter)
        and first_letter.isupper()
        and not title.rstrip().endswith((".", ",", ";", ":"))
        and "..." not in title
        and not LATEX_MARKUP_RE.search(title)
        and not re.match(r"^\d+(?:\.\d+)+\b", title)
        and not normalized.startswith(
            (
                "answers to ",
                "chapter preview",
                "contents",
                "example ",
                "exercise ",
                "figure ",
                "october ",
                "source:",
                "table ",
            )
        )
    )


def preceding_title(
    lines: list[str], section_index: int, max_lookback: int = 25
) -> tuple[int, str] | None:
    """Find the nearest short title block before an ``N.1`` section."""
    cursor = section_index - 1
    lower_bound = max(-1, section_index - max_lookback - 1)
    while cursor > lower_bound:
        while cursor > lower_bound and not lines[cursor].strip():
            cursor -= 1
        if cursor <= lower_bound:
            break

        block_end = cursor
        while cursor > lower_bound and lines[cursor].strip():
            cursor -= 1
        block_start = cursor + 1
        block = [lines[index].strip() for index in range(block_start, block_end + 1)]

        # OCR sometimes attaches the chapter number to the preceding paragraph,
        # leaving the title as the final one or two lines of that same block.
        for count in range(1, min(3, len(block)) + 1):
            title = " ".join(block[-count:])
            if plausible_title(title):
                return block_end - count + 1, title
    return None


def collect_headings(text: str) -> dict[str, list[Heading]]:
    candidates: dict[str, list[Heading]] = {
        "chapter": [],
        "chapter block": [],
        "division": [],
        "numbered section": [],
    }
    lines = text.splitlines(keepends=True)
    offsets: list[int] = []
    offset = 0
    for line_with_ending in lines:
        offsets.append(offset)
        line = line_with_ending.rstrip("\r\n")
        if acceptable_line(line):
            match = CHAPTER_RE.match(line)
            if match and heading_like_tail(match.group(3)):
                label = " ".join(line.split())
                identifier = match.group(2)
                candidates["chapter"].append(
                    Heading(
                        offset,
                        label,
                        f"chapter {identifier.casefold()}",
                        "chapter",
                        identifier_number(identifier),
                    )
                )
            else:
                match = DIVISION_RE.match(line)
                if match and heading_like_tail(match.group(3)):
                    label = " ".join(line.split())
                    identifier = match.group(2)
                    candidates["division"].append(
                        Heading(
                            offset,
                            label,
                            f"division {identifier.casefold()}",
                            "division",
                            identifier_number(identifier),
                        )
                    )
                else:
                    match = NUMBERED_RE.match(line)
                    if match and not match.group(2).rstrip().endswith((".", ";", ",")):
                        number = int(match.group(1))
                        label = " ".join(line.split())
                        candidates["numbered section"].append(
                            Heading(
                                offset,
                                label,
                                f"section {number}",
                                "numbered section",
                                number,
                            )
                        )
        offset += len(line_with_ending)

    # Handle layouts where "Chapter" and its number occupy separate lines,
    # standalone numbered headings, and Roman-numeral literary chapters.
    for index, line_with_ending in enumerate(lines):
        line = line_with_ending.rstrip("\r\n")
        stripped = line.strip()
        if stripped.casefold() in {"chapter", "chap."} and index + 1 < len(lines):
            identifier_match = IDENTIFIER_ONLY_RE.match(lines[index + 1].rstrip("\r\n"))
            if identifier_match:
                identifier = identifier_match.group(1)
                number = identifier_number(identifier)
                label = f"Chapter {identifier}"
                candidates["chapter"].append(
                    Heading(
                        offsets[index],
                        label,
                        f"chapter {identifier.casefold()}",
                        "chapter",
                        number,
                    )
                )

        roman_match = ROMAN_SECTION_RE.match(line)
        if (
            roman_match
            and normalize_key(roman_match.group(2)) != "title"
            and heading_like_tail(roman_match.group(2))
        ):
            number = identifier_number(roman_match.group(1))
            label = " ".join(line.split())
            candidates["numbered section"].append(
                Heading(
                    offsets[index],
                    label,
                    f"section {number}",
                    "numbered section",
                    number,
                )
            )

        # A common PDF-to-text layout puts a chapter number on its own line,
        # followed by a title line. Requiring surrounding blank
        # lines keeps table values and page furniture out of this candidate set.
        if (
            stripped.isdigit()
            and 1 <= int(stripped) <= 200
            and index + 1 < len(lines)
            and (index == 0 or not lines[index - 1].strip())
        ):
            title_index = index + 1
            while (
                title_index < len(lines)
                and not lines[title_index].strip()
                and title_index <= index + 3
            ):
                title_index += 1
            title = lines[title_index].strip() if title_index < len(lines) else ""
            if plausible_title(title) and (
                title_index + 1 == len(lines) or not lines[title_index + 1].strip()
            ):
                number = int(stripped)
                label = f"{number} {title}"
                candidates["chapter block"].append(
                    Heading(
                        offsets[index],
                        label,
                        f"chapter block {number}",
                        "chapter block",
                        number,
                    )
                )

        # Some extracted books lose the large chapter number but retain a
        # title immediately before section N.1. Capture that opening title and
        # use N.1 as the chapter number. This also works with wrapped titles.
        first_section_match = FIRST_SECTION_RE.match(line)
        if first_section_match:
            number = int(first_section_match.group(1))
            title_match = preceding_title(lines, index)
            if 1 <= number <= 200 and title_match:
                start_index, title = title_match
                title = re.sub(rf"^{number}\s+", "", title).strip()
                extra_number = re.match(r"^\d{1,3}\s+(.+)$", title)
                if extra_number and plausible_title(extra_number.group(1)):
                    title = extra_number.group(1)
                candidates["chapter block"].append(
                    Heading(
                        offsets[start_index],
                        f"{number} {title}",
                        f"chapter block {number}",
                        "chapter block",
                        number,
                    )
                )

    for group in candidates.values():
        group.sort(key=lambda heading: heading.start)
    return candidates


def remove_likely_toc_entries(headings: list[Heading], text_length: int) -> list[Heading]:
    """Remove a dense early heading cluster when its entries recur later."""
    if len(headings) < 6:
        return headings

    early_limit = min(int(text_length * 0.15), 150_000)
    runs: list[tuple[int, int]] = []
    run_start = 0
    for index in range(1, len(headings)):
        if (
            headings[index].start - headings[index - 1].start >= 2_000
            or headings[index].start > early_limit
        ):
            if index - run_start >= 5:
                runs.append((run_start, index))
            run_start = index
    if len(headings) - run_start >= 5 and headings[-1].start <= early_limit:
        runs.append((run_start, len(headings)))

    to_remove: set[int] = set()
    for start, end in runs:
        later_keys = {heading.key for heading in headings[end:]}
        repeated = sum(headings[index].key in later_keys for index in range(start, end))
        if repeated >= (end - start) * 0.6:
            to_remove.update(range(start, end))
            break
    return [heading for index, heading in enumerate(headings) if index not in to_remove]


def collapse_repeated_headers(headings: list[Heading]) -> list[Heading]:
    result: list[Heading] = []
    for heading in headings:
        if result and heading.key == result[-1].key:
            if heading_quality(heading) > heading_quality(result[-1]):
                result[-1] = heading
            continue
        result.append(heading)
    return result


def heading_quality(heading: Heading) -> int:
    letters = [character for character in heading.label if character.isalpha()]
    score = 0
    title = re.sub(r"^\s*\d{1,3}\s+", "", heading.label).strip()
    digit_count = sum(character.isdigit() for character in title)
    if not digit_count:
        score += 4
    else:
        score -= min(digit_count, 6)
    if 1 <= len(title.split()) <= 12:
        score += 2
    if any(symbol in title for symbol in ("=", "<", ">", "→")):
        score -= 4
    if (
        heading.kind != "chapter block"
        and letters
        and all(character.isupper() for character in letters)
    ):
        score += 5
    match = CHAPTER_RE.match(heading.label) or DIVISION_RE.match(heading.label)
    if match and not match.group(3).strip():
        score += 3
    if heading.label.startswith(("Chapter", "Book", "Part")):
        score += 1
    return score


def strongest_number_sequence(
    headings: list[Heading], minimum_spacing: int
) -> list[Heading]:
    """Find the best sequential structural run using layout-quality signals."""
    # Each state is (length, score, path indexes). Length is the primary goal;
    # typography and realistic spacing choose among duplicate page headers.
    states: list[tuple[int, int, list[int]]] = []
    for index, heading in enumerate(headings):
        best_state = (1, heading_quality(heading), [index])
        if heading.number is not None:
            for previous_index, previous in enumerate(headings[:index]):
                if previous.number != heading.number - 1:
                    continue
                length, score, path = states[previous_index]
                distance = heading.start - previous.start
                if distance < minimum_spacing:
                    continue
                spacing_score = 4
                candidate = (
                    length + 1,
                    score + heading_quality(heading) + spacing_score,
                    path + [index],
                )
                if candidate[:2] > best_state[:2]:
                    best_state = candidate
        states.append(best_state)

    if not states:
        return []
    best = max(states, key=lambda state: state[:2])
    return [headings[index] for index in best[2]]


def detect_structure(text: str, minimum: int) -> tuple[str, list[Heading]]:
    groups = collect_headings(text)
    # Chapter-opening blocks are more reliable than literal "Chapter N"
    # references, which frequently occur in ordinary prose and appendices.
    for method in ("chapter block", "chapter", "division"):
        headings = collapse_repeated_headers(
            remove_likely_toc_entries(groups[method], len(text))
        )
        headings = strongest_number_sequence(headings, minimum)
        if len(headings) >= 2:
            return method, headings

    numbered = collapse_repeated_headers(
        remove_likely_toc_entries(groups["numbered section"], len(text))
    )
    numbered = strongest_number_sequence(numbered, minimum)
    numeric_values = [heading.number for heading in numbered if heading.number is not None]
    if len(numbered) >= 3 and numeric_values and min(numeric_values) <= 2:
        return "numbered section", numbered

    return "complete text", []

def bookmark_headings(toc: list, pages: list[str]) -> list[Heading]:
    """Use the shallowest outline level; prefer explicit chapters at any level."""
    valid = [(level, str(title).strip(), page) for level, title, page in toc
             if str(title).strip() and 1 <= page <= len(pages)]
    if not valid:
        return []
    explicit = [row for row in valid if CHAPTER_RE.match(row[1])]
    if len({row[2] for row in explicit}) >= 2:
        level = min(row[0] for row in explicit)
    else:
        level = min(row[0] for row in valid)
        # A single book-title wrapper can contain the actual chapter outline.
        while sum(row[0] == level for row in valid) == 1:
            deeper = [row[0] for row in valid if row[0] > level]
            if not deeper:
                return []
            level = min(deeper)
    offsets = []
    offset = 0
    for page in pages:
        offsets.append(offset)
        offset += len(page) + len(PAGE_SEPARATOR)
    by_page = {}
    for depth, title, page in valid:
        if depth == level:
            by_page.setdefault(page, title)
    if len(by_page) < 2:
        return []
    return [Heading(offsets[page - 1], title, normalize_key(title), "PDF bookmarks")
            for page, title in sorted(by_page.items())]



# --------------------------------------------------------------------------
# Structure from markup, for the text-layer converter
# --------------------------------------------------------------------------
# The heuristics above read OCR prose. The text-layer converter emits LaTeX,
# where the same patterns match mathematics and stray markup: a line reading
# "8 P(X<=x) = ..." in a book's own symbol font is not chapter eight. It does
# not need prose heuristics -- `emit` has already classified the page's type
# as a heading and written it as \section.

LATEX_HEADING_RE = re.compile(
    r"^[ \t]*\\(chapter|section)\*?\{([^\r\n]+)\}[ \t]*\r?$", re.MULTILINE)
LATEX_TEXT_ESCAPES = {
    r"\&": "&", r"\%": "%", r"\$": "$", r"\#": "#", r"\_": "_",
    r"\{": "{", r"\}": "}", r"\textasciitilde{}": "~",
    r"\textasciicircum{}": "^", r"\textbackslash{}": "\\",
}
LATEX_TEXT_ESCAPE_RE = re.compile(
    "|".join(re.escape(value) for value in LATEX_TEXT_ESCAPES))


def _balanced_latex_argument(argument: str) -> bool:
    """Reject matches extending past the heading's closing brace."""
    depth = 0
    escaped = False
    for character in argument:
        if escaped:
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0 and not escaped


def latex_headings(text: str) -> list[Heading]:
    r"""Find the outermost emitted \chapter or \section boundaries.

    Escaped punctuation belongs in the plain heading used for filenames and
    renderer metadata. The original LaTeX body and its offsets remain intact.
    OCR title heuristics do not apply to these explicit structural commands:
    short, lowercase, and punctuation-bearing headings are all valid.
    """
    found: dict[str, list[Heading]] = {"chapter": [], "section": []}
    for match in LATEX_HEADING_RE.finditer(text):
        if not _balanced_latex_argument(match.group(2)):
            continue
        label = LATEX_TEXT_ESCAPE_RE.sub(
            lambda escaped: LATEX_TEXT_ESCAPES[escaped.group()], match.group(2))
        label = " ".join(label.split())
        if not label:
            continue
        found[match.group(1)].append(
            Heading(match.start(), label, normalize_key(label) or label.casefold(),
                    "LaTeX headings"))
    return collapse_repeated_headers(found["chapter"] or found["section"])


def headings_from_text(pages: list[str], toc: list, text: str,
                       minimum: int) -> tuple[str, list[Heading]]:
    """Structure for recognised prose: bookmarks, else heading heuristics."""
    headings = bookmark_headings(toc, pages)
    if headings:
        return "PDF bookmarks", headings
    return detect_structure(text, minimum)


def headings_from_latex(pages: list[str], toc: list, text: str,
                        minimum: int) -> tuple[str, list[Heading]]:
    r"""Structure for generated LaTeX: bookmarks, else the emitted \section marks.

    Deliberately no fall-through to ``detect_structure``: a book with neither
    bookmarks nor headings the converter recognised is better published whole
    than cut at whatever a prose heuristic mistook for a chapter.
    """
    headings = bookmark_headings(toc, pages)
    if headings:
        return "PDF bookmarks", headings
    headings = latex_headings(text)
    if len(headings) >= 2:
        return "LaTeX headings", headings
    return "complete text", []

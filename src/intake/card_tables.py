"""Question cards: one Word table per question, read by its layout, not guessed.

Some documents (the Charter NPS tracker, GBK programming files) put every
question in its own table with the same shape:

    ┌──────────────────────────────────────────────────────────────┐
    │ INTERNET WIFI TYPE          ← short name                      │
    │ S5c.                        ← the label, alone on its line    │
    │ What type of Internet service do you have …?                  │
    │ Please select one.                                            │
    ├───────────┬──────────────────────────────────────────────────┤
    │ 1 2 3 97  │ DSL Internet / Fiber / Cable / Other, specify     │ ← codes | answers
    ├───────────┴──────────────────────────────────────────────────┤
    │ Ask for brand selected at S5a.  Allow one response.          │ ← scripter notes
    └──────────────────────────────────────────────────────────────┘

The label is never at the start of a line the paragraph reader looks at, so
the whole document segmented into 14 unlabelled chunks and every question was
left to a model. Each card is rewritten here into the plain lines the
paragraph reader already reads exactly — "S5c. question", "1. answer",
"[PN: note]" — so codes, types, routing and order instructions all go through
the one tested path. Nothing is inferred: a row whose shape is not one of the
layouts below is passed through as written, for a person to see.
"""

from __future__ import annotations

import re

# "S5c.", "QGENDER.", "KP2." — the label on a line of its own.
_LABEL = re.compile(r"^([A-Za-z][A-Za-z0-9_]{0,24})\.$")
# "CSTCLASSIFICATION" — a label the document forgot the full stop on: one
# all-caps token, accepted only when the line after it asks the question.
_BARE_LABEL = re.compile(r"^([A-Z][A-Z0-9_]{3,24})$")
_CODE = re.compile(r"^\d{1,4}$")
# "[3]" closing a scale cell, "[99] I plan to drop this service" opening a line.
_BRACKET_CODE = re.compile(r"^\[(\d{1,4})\]\s*(.*)$")
# "Please select one.", "Select all that apply.", "Enter your 5-digit ZIP code below."
_INSTRUCTION = re.compile(r"^\s*(?:please\s+)?(?:select|choose|enter|type|write|make|rate|"
                          r"check|tick|click|record|indicate)\b", re.I)
import contextvars  # noqa: E402

#: Codes the card's own notes mention ("anchor punch 98 and 99"), for the one
#: card being read.
_NOTE_CODES: contextvars.ContextVar[frozenset[str]] = contextvars.ContextVar(
    "note_codes", default=frozenset())
_ENTRY_BOX = re.compile(r"_\s*_|_{3,}")
_GUIDANCE = re.compile(r"^\s*\(\s*(?:please|note|e\.g\.|i\.e\.|do not|don.t|excluding|"
                       r"not including)[^)]*\)\s*$", re.I)
# "[INSERT BRAND SELECTED AT S5]" — answers taken from another question.
_INSERT_FROM = re.compile(
    r"\[\s*INSERT\b[^\]]*?\bSELECTED\s+(?:AT|IN)\s+([A-Z]{1,6}\d+[A-Za-z]?)\s*\]", re.I)
# A line in another language: Spanish/French/German marks, or a non-Latin script.
OTHER_LANGUAGE = re.compile(r"[¿¡ñçßœ]|[à-öø-ÿ]|[^\x00-\u024f\s\d\W]", re.I)
# "1 – Very Dissatisfied": the scale point repeated in front of its label.
_POINT_PREFIX = re.compile(r"^\d{1,2}\s*[–-]\s*")

#: A document is read as cards only when it has at least this many: one
#: labelled table in a prose document is a quota grid or an example.
MIN_CARDS = 3


def label_of(cells: list[list[str]]) -> str | None:
    """The card's label, if this table is a question card.

    `cells` is the table's rows, each a list of its DISTINCT cells (merged
    cells once), each cell a list of its non-empty lines.
    """
    if len(cells) < 2 or len(cells[0]) != 1:
        return None
    head = [x.strip() for x in cells[0][0]]
    for k, line in enumerate(head[:3]):
        if m := _LABEL.match(line):
            return m.group(1)
        if k and (m := _BARE_LABEL.match(line)) and k + 1 < len(head) and "?" in head[k + 1]:
            return m.group(1)
    return None


def split_cards(cells: list[list[list[str]]]) -> list[list[list[list[str]]]]:
    """One table, several cards: C3a's notes row is followed, in the SAME table,
    by C3b's head ("T-MOBILE ROUTER - <6M TENURE / C3b. / Think about …").
    Read as one card, C3b's wording became answers of C3a."""
    starts = [0] + [k for k in range(1, len(cells))
                    if len(cells[k]) == 1 and label_of(cells[k:k + 2] if k + 1 < len(cells)
                                                      else [cells[k], [[]]])]
    return [cells[a:b] for a, b in zip(starts, starts[1:] + [len(cells)], strict=True)]


def _scale_point(cell: list[str]) -> tuple[int, str] | None:
    """`["1 – Very Dissatisfied", "[1]"]` -> (1, "Very Dissatisfied")."""
    if not cell or not (m := _BRACKET_CODE.match(cell[-1].strip())) or m.group(2):
        return None
    words = " ".join(x.strip() for x in cell[:-1])
    code = int(m.group(1))
    # "1 – Very Dissatisfied [1]": the point repeats the code, drop it. "0 - Not
    # At All Likely [1]" (an NPS scale coded 1-11): the point IS the answer —
    # dropped, a 0 and a 1 became indistinguishable in every NPS calculation.
    point = _POINT_PREFIX.match(words)
    if point and int(re.match(r"\d+", point.group(0)).group(0)) == code:
        words = words[point.end():]
    return code, words.strip()


def _column(cell: list[str], position: int) -> tuple[int, str] | None:
    """A grid column heading: its stated code, or — only when NO heading in
    the row states one ("I recommended this service") — its position."""
    if point := _scale_point(cell):
        return point
    if cell and not any(_BRACKET_CODE.match(x.strip()) for x in cell):
        return position, " ".join(x.strip() for x in cell)
    return None


def _answer_row(row: list[list[str]]) -> list[str] | None:
    """One answer row in any of the card layouts, as paragraph lines."""
    if len(row) == 1:
        out = []
        for line in row[0]:
            m = _BRACKET_CODE.match(line.strip())
            out.append(f"{m.group(1)}. {m.group(2)}" if m and m.group(2) else line)
        return out
    if len(row) == 2:
        codes, labels = row
        # "98 99" typed into one line of the code box, over two answers.
        codes = [c for line in codes for c in line.split()]
        if len(labels) == len(codes) + 1:
            # "9899": 98 and 99 run together. Split only when the notes name both
            # ("anchor punch 98 and 99") — then it is read, not guessed.
            for k, c in enumerate(codes):
                a, b = c[: len(c) // 2], c[len(c) // 2:]
                if len(c) == 4 and {a, b} <= _NOTE_CODES.get():
                    codes[k:k + 1] = [a, b]
                    break
        if len(labels) > len(codes):
            # "(please do not include on-demand streaming services …)" under
            # "Other": guidance for the line above, not an answer of its own.
            labels = [x for x in labels if not _GUIDANCE.match(x)] or labels
        if codes and all(_CODE.match(c.strip()) for c in codes):
            if len(codes) == len(labels):
                return [f"{c.strip()}. {a.strip()}" for c, a in zip(codes, labels, strict=True)]
            if len(codes) == 1:
                box = [x for x in labels if _ENTRY_BOX.search(x)]
                rest = [x for x in labels if x not in box]
                if box and rest:
                    # "99 | ZIP code: _ _ _ _ _ / Prefer not to answer": the
                    # underscores are where the respondent types; 99 is the other.
                    return [f"[{b.strip()}]" for b in box] + [f"{codes[0].strip()}. {' '.join(rest)}"]  # noqa: E501
                return [f"{codes[0].strip()}. {' '.join(x.strip() for x in labels)}"]
        if not codes:
            # "WRITE IN RESPONSE" is the box the respondent types in, not an answer.
            return [f"[{x.strip()}]" for x in labels]
        if labels:
            # The code box does not line up with the answers ("1 2 3 4 9899"
            # over six answers). The answers are certain, the codes are not:
            # keep the answers and say so, rather than pair them by guess.
            return [*labels, f"[PN: codes as written ({' '.join(codes)}) do not match "
                             f"the {len(labels)} answers — check the codes]"]
        return None
    points = [_scale_point(c) for c in row]
    if all(points):
        return [f"{code}. {label}" for code, label in points]
    return None


Grid = tuple[list[tuple[int, str]], list[tuple[int, str]]]


def _grid(rows: list[list[list[str]]]) -> Grid | None:
    """(rows, columns) of a grid card, each with the code the card states.

    Header: two empty cells then one column heading each. Body: a code, the
    item, then empty cells (one box per column for the respondent to tick).
    """
    head, body = rows[0], rows[1:]
    if len(head) < 4 or any(head[:2]) or not body:
        return None
    stated = any(_scale_point(c) for c in head[2:])
    cols = [_column(c, n) if not stated else _scale_point(c)
            for n, c in enumerate(head[2:], 1)]
    if not all(cols):
        return None
    items = []
    for row in body:
        code = row[0][0].strip() if len(row) == len(head) and len(row[0]) == 1 else ""
        if not _CODE.match(code) or any(row[2:]):
            return None
        if row[1]:
            items.append((int(code), " ".join(x.strip() for x in row[1])))
    # "1 | [INSERT PARENT BRANDS FROM KP1]" then rows 2 and 3 left blank: places
    # for the 2nd and 3rd brand, not missing rows.
    return (items, cols) if items else None


def card_lines(cells: list[list[list[str]]]) -> tuple[list[str], Grid | None]:
    """A question card as the paragraph lines the prose reader takes, plus its
    grid (rows, columns) when it is one — handed over as structure, because a
    scale written back out as text would have its codes guessed from position."""
    label = label_of(cells)
    head = [x.strip() for x in cells[0][0]]
    at = next(i for i, x in enumerate(head[:3]) if x in (f"{label}.", label))
    # The short name above the label ("INTERNET WIFI TYPE") is the variable's
    # name in the data, not wording a respondent sees.
    rest = head[at + 1:]
    # The label is carried on the block (DocumentBlock.label), not the text:
    # "QGENDER. Are you?" is no label the text reader knows, and left in it
    # became part of the question wording. The head is read as an analyst reads
    # it: ONE question line (the first that asks — a "[IF 1 ENTERED AT S7a]"
    # condition may come first), the instruction ("Please select one provider.")
    # as an instruction, an explanation for the respondent as part of the
    # wording, the question again in another language or as an alternative for
    # another condition as a note. Left as plain lines, the explanation and the
    # instruction became answers 1 and 2 once the answers carried their codes.
    asking = next((k for k, x in enumerate(rest) if "?" in x and not x.startswith("[")),
                  next((k for k, x in enumerate(rest) if not x.startswith("[")), None))
    if asking is None:
        lines = [f"{label}."]
    else:
        question, extra = rest[asking], []
        for k, x in enumerate(rest):
            if k == asking:
                continue
            if x.startswith("[") or _INSTRUCTION.match(x):
                extra.append(x)
            elif x.rstrip().endswith("?"):
                kind = "also worded as" if OTHER_LANGUAGE.search(x) else "alternative wording"
                extra.append(f"[PN: {kind}: {x}]")
            elif x.endswith(":") and x[:-1].isupper():
                extra.append(f"[PN: {x}]")  # "ISSUES:" introduces the answers
            elif k > asking:
                question = f"{question} {x}"
            else:
                extra.append(x)
        lines = [question, *extra]
    # A closing single-cell row is the scripter's notes.
    has_notes = len(cells) > 2 and len(cells[-1]) == 1
    middle = cells[1:-1] if has_notes else cells[1:]
    notes = cells[-1][0] if has_notes else []
    _NOTE_CODES.set(frozenset(c for n in notes for c in re.findall(r"\b\d{2,3}\b", n)))
    # "1 | [INSERT BRAND SELECTED AT S5]" on every answer row: not answers, the
    # statement that this question's answers ARE the ones chosen at S5. Kept as
    # rows, the brackets were dropped as directives and "1. 2. 3." remained.
    inserted = {m.group(1) for row in middle for cell in row for line in cell
                if (m := _INSERT_FROM.search(line))}
    if len(inserted) == 1 and middle and all(
            len(row) == 2 and all(_INSERT_FROM.search(x) for x in row[1]) for row in middle):
        source = inserted.pop()
        lines.append(f"[PN: MASK: show codes selected at {source}]")
        middle = []
    # The same answer row twice (a row the document repeats) is one list.
    middle = [row for n, row in enumerate(middle) if n == 0 or row != middle[n - 1]]
    grid = _grid(middle) if middle and len(middle[0]) > 2 else None
    if not grid:
        for row in middle:
            read = _answer_row(row)
            # A shape none of the layouts covers: as written, for a person.
            lines += read if read is not None else [" | ".join(" ".join(c) for c in row)]
    lines += [f"[PN: {n.strip()}]" for n in notes if n.strip()]
    return lines, grid


def plain_lines(cells: list[list[list[str]]]) -> list[str]:
    """A table in a card document that is not a question: a section heading
    ("INTERNET SCREENER QUESTIONS: QS5-QS5e"), the intro, a tracking variable.
    Its first row is read as written and later rows as scripter notes, which
    is how the cards themselves use a closing row."""
    if any(len(row) != 1 for row in cells):
        return []
    first, *others = cells
    head = list(first[0])
    if head and head[0].isupper() and (len(head) > 1 or others):
        # "CS OTHER ISSUE INTERACTION JOURNEY INSTRUCTIONS CSO1-CSO4": a title.
        # Bare, it ran into the answer above it.
        head[0] = f"[SECTION: {head[0]}]"
    return [*head, *(f"[PN: {x.strip()}]" for row in others for x in row[0] if x.strip())]


#: A table that says it is a screen: its title or its note.
SCREEN = re.compile(r"\b(?:intro(?:duction)?\s+text|transition\s+(?:text|screen)|end\s+screen|"
                    r"intermediate\s+text\s+only)\b", re.I)

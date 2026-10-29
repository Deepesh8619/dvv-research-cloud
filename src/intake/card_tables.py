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
paragraph reader already reads exactly — "S5c. question", "1<TAB>answer",
"[PN: note]" — so codes, types, routing and order instructions all go through
the one tested path. Nothing is inferred: a row whose shape is not one of the
layouts below is passed through as written, for a person to see.
"""

from __future__ import annotations

import re

# "S5c.", "QGENDER.", "KP2." — the label on a line of its own.
_LABEL = re.compile(r"^([A-Za-z][A-Za-z0-9_]{0,24})\.$")
_CODE = re.compile(r"^\d{1,4}$")
# "[3]" closing a scale cell, "[99] I plan to drop this service" opening a line.
_BRACKET_CODE = re.compile(r"^\[(\d{1,4})\]\s*(.*)$")
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
    for line in cells[0][0][:3]:
        if m := _LABEL.match(line.strip()):
            return m.group(1)
    return None


def _scale_point(cell: list[str]) -> tuple[int, str] | None:
    """`["1 – Very Dissatisfied", "[1]"]` -> (1, "Very Dissatisfied")."""
    if not cell or not (m := _BRACKET_CODE.match(cell[-1].strip())) or m.group(2):
        return None
    words = " ".join(x.strip() for x in cell[:-1])
    return int(m.group(1)), _POINT_PREFIX.sub("", words).strip()


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
            out.append(f"{m.group(1)}\t{m.group(2)}" if m and m.group(2) else line)
        return out
    if len(row) == 2:
        codes, labels = row
        # "98 99" typed into one line of the code box, over two answers.
        codes = [c for line in codes for c in line.split()]
        if codes and all(_CODE.match(c.strip()) for c in codes):
            if len(codes) == len(labels):
                return [f"{c.strip()}\t{a.strip()}" for c, a in zip(codes, labels, strict=True)]
            if len(codes) == 1:
                return [f"{codes[0].strip()}\t{' '.join(x.strip() for x in labels)}"]
        if not codes:
            return list(labels)  # "WRITE IN RESPONSE"
        if labels:
            # The code box does not line up with the answers ("1 2 3 4 9899"
            # over six answers). The answers are certain, the codes are not:
            # keep the answers and say so, rather than pair them by guess.
            return [*labels, f"[PN: codes as written ({' '.join(codes)}) do not match "
                             f"the {len(labels)} answers — check the codes]"]
        return None
    points = [_scale_point(c) for c in row]
    if all(points):
        return [f"{code}\t{label}" for code, label in points]
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
        if not _CODE.match(code) or any(row[2:]) or not row[1]:
            return None
        items.append((int(code), " ".join(x.strip() for x in row[1])))
    return items, cols


def card_lines(cells: list[list[list[str]]]) -> tuple[list[str], Grid | None]:
    """A question card as the paragraph lines the prose reader takes, plus its
    grid (rows, columns) when it is one — handed over as structure, because a
    scale written back out as text would have its codes guessed from position."""
    label = label_of(cells)
    head = [x.strip() for x in cells[0][0]]
    at = next(i for i, x in enumerate(head[:3]) if x == f"{label}.")
    # The short name above the label ("INTERNET WIFI TYPE") is the variable's
    # name in the data, not wording a respondent sees.
    rest = head[at + 1:]
    # The label is carried on the block (DocumentBlock.label), not the text:
    # "QGENDER. Are you?" is no label the text reader knows, and left in it
    # became part of the question wording.
    lines = list(rest) or [f"{label}."]
    # The question again in another language ("¿En qué idioma…?" under
    # "Which language…?"): the same wording, never an answer.
    lines = lines[:1] + [f"[PN: also worded as: {x}]" if x.rstrip().endswith("?") else x
                         for x in lines[1:]]
    # "ISSUES:" closing the head cell introduces the answers; it is not one.
    if len(lines) > 1 and lines[-1].endswith(":") and lines[-1][:-1].isupper():
        lines[-1] = f"[PN: {lines[-1]}]"

    # A closing single-cell row is the scripter's notes.
    has_notes = len(cells) > 2 and len(cells[-1]) == 1
    middle = cells[1:-1] if has_notes else cells[1:]
    notes = cells[-1][0] if has_notes else []
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
    return [*first[0], *(f"[PN: {x.strip()}]" for row in others for x in row[0] if x.strip())]

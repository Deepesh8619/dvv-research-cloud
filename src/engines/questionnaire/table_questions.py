"""Read a question that was written as a TABLE, from the table's real cells.

A questionnaire mixes prose and tables, and a grid is almost always a table:
statements down the side, a scale across the top, punch codes underneath.

    ''   '[PN: RANDOMIZE ROWS]'  'Every day'  'A few times a week'  ...
    ''   ''                      '1'          '2'                   ...
    '1'  'Face wash / cleanser'  ''           ''                    ...
    '2'  'Moisturizer'           ''           ''                    ...

The loader already keeps those cells on `DocumentBlock.rows`. Nothing read them:
the prose parser worked off the serialized `a | b | c` text, so the whole header
row became "answer option 1", the code row became "option 2", and each statement
became another option. The question was typed single-select, its data map got
one column instead of one per statement, and the scale existed nowhere.

Two table shapes carry a question:

* a full grid — a header of column labels plus rows of statements;
* a scale strip — one row of nothing but scale points ("0=Not Important At All",
  1 … "10=Very Important"), where the statements are prose lines around it.

Everything else (quota tables, a one-cell programmer note) is left alone. Being
wrong about a grid is worse than not reading it: the rows and columns ARE the
data map, so a guess here shows up as fabricated columns in a delivery file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.core.survey_definition import Expression, Option
from src.engines.questionnaire.routing_prose import parse_routing

# "0=Not Important At All", "10 = Very Important", or a bare "7".
_SCALE_POINT = re.compile(r"^\s*(-?\d+)\s*(?:=\s*(.*))?$", re.S)
_DIRECTIVE = re.compile(r"\[[^\]]*\]")
_BRACKET_RE = re.compile(r"\[([^\]]*)\]")
_RANDOMIZE_ROWS = re.compile(r"RANDOMI[SZ]E\s+(ROWS|STATEMENTS|ITEMS)", re.I)
_MIN_GRID_COLUMNS = 2
_MIN_GRID_ROWS = 2
_MIN_SCALE_POINTS = 3


@dataclass
class TableQuestion:
    """What a table states: the grid's rows and columns, and how to show them."""

    rows: list[Option]
    cols: list[Option]
    randomize_rows: bool = False
    evidence: str = ""


def _clean(cell: str) -> str:
    """A cell's respondent-facing text: directives stripped, whitespace folded."""
    return " ".join(_DIRECTIVE.sub(" ", cell or "").split())


def _scale_point(cell: str) -> Option | None:
    match = _SCALE_POINT.match(_clean(cell))
    if not match:
        return None
    code = int(match.group(1))
    label = " ".join((match.group(2) or "").split())
    return Option(code=code, label=label or str(code))


def scale_strip(rows: list[list[str]]) -> list[Option] | None:
    """The columns of a scale written as a single table row, or None.

    "0=No Impact | 1 | … | 10=Much more likely" is the scale for statements that
    live in the prose around the table. Read as answer options it turned an
    11-point scale into eleven single-select answers.
    """
    filled = [row for row in rows if any(cell.strip() for cell in row)]
    if len(filled) != 1:
        return None
    points = [_scale_point(cell) for cell in filled[0] if cell.strip()]
    if len(points) < _MIN_SCALE_POINTS or any(p is None for p in points):
        return None
    codes = [p.code for p in points]
    if codes != sorted(codes) or len(set(codes)) != len(codes):
        return None  # not a scale: unordered or repeating
    return points


def statements_table(rows: list[list[str]]) -> list[Option] | None:
    """The rated statements of a grid, listed as `code | statement | ...`.

    Only meaningful next to a scale strip — the caller must have found one in
    the same question, because this shape is indistinguishable from a quota
    table ("1 | 13-17 | N=400 QUOTA") on its own, and turning a quota table into
    grid rows invents a question nobody is asked.

    Columns after the statement are the scripter's ("Theme – INTERNAL, NOT
    SHOWN"): a respondent never sees them, so neither does the definition.
    """
    filled = [row for row in rows if any(cell.strip() for cell in row)]
    out: list[Option] = []
    for row in filled:
        code_cell = _clean(row[0]) if row else ""
        label = next((_clean(c) for c in row[1:] if _clean(c)), "")
        if not code_cell.isdigit() or not label:
            continue  # header rows and directive rows have no code
        out.append(Option(code=int(code_cell), label=label))
    return out or None


def grid(rows: list[list[str]]) -> TableQuestion | None:
    """The rows and columns of a grid table, or None if this is not one."""
    filled = [row for row in rows if any(cell.strip() for cell in row)]
    if len(filled) < _MIN_GRID_ROWS + 1:
        return None

    header_index, labels = _header(filled)
    if labels is None:
        return None

    positions = [i for i, _ in labels]
    codes, code_row_used = _column_codes(filled, header_index, positions)
    cols = [
        Option(code=code, label=label)
        for code, (_, label) in zip(codes, labels, strict=True)
    ]

    # Past the header AND past the punch-code row when that is where the codes
    # came from: those digits are the scale's codes, not answers somebody wrote
    # into the grid. Counted as interior content they made a small grid look
    # like a filled-in data table, and it was refused. A grid with nine rows
    # survived on dilution alone.
    body = filled[header_index + (2 if code_row_used else 1) :]
    statements = _statements(body, positions)
    if len(statements) < _MIN_GRID_ROWS or len(cols) < _MIN_GRID_COLUMNS:
        return None
    if not _interior_is_empty(body, positions):
        # A respondent grid's interior is EMPTY — the respondent fills it in.
        # A quota table ("N=400 QUOTA" / "No Quota") and a region map ("23%")
        # have the same shape with the answers already written in, and reading
        # one as a question invents a grid nobody is ever asked.
        return None

    randomize = any(
        _RANDOMIZE_ROWS.search(cell) for row in filled[: header_index + 1] for cell in row
    )
    return TableQuestion(
        rows=statements,
        cols=cols,
        randomize_rows=randomize,
        evidence=f"a {len(statements)}x{len(cols)} table",
    )


def _interior_is_empty(body: list[list[str]], positions: list[int]) -> bool:
    cells = [
        _clean(row[i]) for row in body for i in positions if i < len(row)
    ]
    if not cells:
        return False
    return sum(1 for c in cells if c) <= len(cells) // 4


def _header(filled: list[list[str]]) -> tuple[int, list[tuple[int, str]] | None]:
    """(index, [(column, label)]) of the row naming the columns."""
    for index, row in enumerate(filled[:2]):  # a header is the first row, or just under a title
        labels = [
            (i, _clean(cell))
            for i, cell in enumerate(row)
            if _clean(cell) and not _clean(cell).isdigit()
        ]
        # The first one or two columns hold the row code and the row label, not
        # a column label — a header therefore starts beyond them.
        labels = [(i, text) for i, text in labels if i >= 1]
        if len(labels) >= _MIN_GRID_COLUMNS:
            return index, labels
    return 0, None


def _column_codes(
    filled: list[list[str]], header_index: int, positions: list[int]
) -> tuple[list[int], bool]:
    """(codes, whether they came from a dedicated row under the header)."""
    if header_index + 1 < len(filled):
        candidate = filled[header_index + 1]
        values = [_clean(candidate[i]) if i < len(candidate) else "" for i in positions]
        if values and all(v.lstrip("-").isdigit() for v in values):
            return [int(v) for v in values], True
    return list(range(1, len(positions) + 1)), False


def _statements(rows: list[list[str]], column_positions: list[int]) -> list[Option]:
    """The grid's row statements: a code cell and a label cell, left of the scale."""
    first_column = min(column_positions)
    out: list[Option] = []
    for row in rows:
        code_cell = _clean(row[0]) if row else ""
        label = ""
        for i in range(1, first_column):
            if i < len(row) and _clean(row[i]):
                label = _clean(row[i])
                break
        if not label and code_cell and not code_cell.isdigit():
            # No separate code column: the statement itself is in column 0.
            label, code_cell = code_cell, ""
        if not label:
            continue
        code = int(code_cell) if code_cell.isdigit() else len(out) + 1
        out.append(Option(code=code, label=label, show_if=_row_condition(row)))
    return out


def _row_condition(row: list[str]) -> Expression | None:
    """A condition written in the row itself: "[SHOW IF Q14.1 SELECTED ANY BRAND]".

    A grid whose rows each depend on an earlier answer is ordinary in a real
    questionnaire — ask about the medication types this person actually fills —
    and every row was being shown to everybody.
    """
    for cell in row:
        for body in _BRACKET_RE.findall(cell or ""):
            condition = parse_routing(body)
            if condition is not None:
                return condition
    return None


def question_from_tables(
    tables: list[list[list[str]]], prose_options: list[Option]
) -> TableQuestion | None:
    """What a question's tables state, composed: a grid needs rows AND columns.

    Three real shapes, in order of how much the table itself says:

    1. one table holding the whole grid (statements down, scale across);
    2. a scale strip plus a statements table beside it;
    3. a scale strip whose statements are prose the parser already read.

    Returns None unless BOTH rows and columns are known. Half a grid is not an
    improvement: a grid with no rows emits no data columns at all, which is the
    bug this exists to fix, and inventing the missing half would put fabricated
    statements in a delivery file.
    """
    for rows in tables:
        whole = grid(rows)
        if whole:
            return whole

    scale = next((s for s in (scale_strip(t) for t in tables) if s), None)
    if not scale:
        return None
    statements = next((s for s in (statements_table(t) for t in tables) if s), None)
    if not statements:
        statements = [o for o in prose_options if o.label.strip()]
    if len(statements) < _MIN_GRID_ROWS:
        return None
    return TableQuestion(
        rows=list(statements),
        cols=list(scale),
        evidence=f"a {len(scale)}-point scale table over {len(statements)} statements",
    )

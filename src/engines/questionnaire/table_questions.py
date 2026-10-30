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
    # Row label -> the condition written on that row ("Q10.1>0"), kept so the
    # bracket never reaches a respondent and the rule is not lost.
    row_conditions: dict[str, str] | None = None


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
    if len(filled) == 2:
        # "Within the past 3 months | 3-5 months ago | ..." over "1 | 2 | ...":
        # the labels on one row and their punch codes on the next.
        labels = [_clean(c) for c in filled[0] if _clean(c)]
        codes = [_clean(c) for c in filled[1] if _clean(c)]
        if (
            len(labels) == len(codes) >= _MIN_SCALE_POINTS
            and all(c.isdigit() for c in codes)
            and [int(c) for c in codes] == sorted({int(c) for c in codes})
            and not any(label.isdigit() for label in labels)
        ):
            pairs = zip(codes, labels, strict=True)
            return [Option(code=int(c), label=label) for c, label in pairs]
        return None
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
    if punched := _punch_codes(filled[header_index + 1:], positions):
        # Each row repeats the column's punch code ("Pizza | 1 | 2 | … | 6"):
        # the scale's codes, printed for the scripter — not answers written in.
        # The first such row is a statement too, not a separate code row.
        statements = _statements(filled[header_index + 1:], positions)
        cols = [Option(code=code, label=c.label) for code, c in zip(punched, cols, strict=True)]
    elif not _interior_is_empty(body, positions):
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


def _punch_codes(body: list[list[str]], positions: list[int]) -> list[int] | None:
    """The column codes when every row prints the same ones, else None.

    A quota table's interior varies row to row (targets, percentages); a punch
    grid's never does — that is the difference, not whether cells are empty.
    """
    rows = [tuple(_clean(row[i]) if i < len(row) else "" for i in positions) for row in body]
    rows = [r for r in rows if any(r)]
    if len(rows) < _MIN_GRID_ROWS or len(set(rows)) != 1:
        return None
    codes = rows[0]
    if not all(c.isdigit() for c in codes) or len(set(codes)) != len(codes):
        return None
    return [int(c) for c in codes]


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
        # "98 | Other (Specify) [ANCHOR]", "None of the above / [ANCHOR,
        # EXCLUSIVE, …]": the tags are cleaned out of the label, and their
        # meaning went with them.
        tags = " ".join(_BRACKET_RE.findall(" ".join(row)))
        out.append(Option(
            code=code, label=label, show_if=_row_condition(row),
            anchored=bool(re.search(r"\bANCHOR", tags, re.I)),
            exclusive=bool(re.search(r"\bEXCLUSIVE\b", tags, re.I)),
        ))
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


_COLUMNS_BANNER = re.compile(r"^\s*COLUMNS?\s*:?\s*$", re.I)
_ROWS_BANNER = re.compile(r"^\s*\[?\s*ROWS?\b", re.I)
_BRACKETED = re.compile(r"^\s*\[[^\]]*\]\s*$")
_ROW_CONDITION = re.compile(r"\[\s*(?:SHOW|ASK)\s+IF\s+([^\]]+)\]", re.I)


def _is_banner(row: list[str], pattern: re.Pattern) -> bool:
    cells = [c for c in row if (c or "").strip()]
    return bool(cells) and all(pattern.match(c) for c in cells)


def _merged_value(row: list[str]) -> str:
    """A row whose cells are one merged cell repeated: that one value."""
    return next((c.strip() for c in row if (c or "").strip()), "")


def stacked_grid(rows: list[list[str]]) -> TableQuestion | None:
    """A grid written as a stack rather than side by side:

        COLUMNS:  | COLUMNS:            | COLUMNS: | ...
        (opt-out) | Not at all satisfied |          | Somewhat | ... | Extremely
        0         | 1                    | 2        | 3        | ... | 5
        [ROWS, RANDOMIZE] | [ROWS, RANDOMIZE] | ...
        TMS       | TMS                  | TMS      | ...   (one merged cell)

    Both TRD studies write every rating grid this way, and the side-by-side
    reader found neither rows nor columns in it — the respondent got a grid
    with nothing in it.
    """
    cols_at = next((i for i, r in enumerate(rows) if _is_banner(r, _COLUMNS_BANNER)), None)
    if cols_at is None:
        return None
    # The code row ("0 | 1 | 2 | 3 | 4 | 5") closes the column header. What
    # follows is an optional banner — "[ROWS, RANDOMIZE]" or just "[RANDOMIZE]"
    # — and then the items.
    code_at = next(
        (i for i in range(cols_at + 1, len(rows))
         if [c for c in rows[i] if c.strip()]
         and all(re.fullmatch(r"-?\d+", c.strip()) for c in rows[i] if c.strip())),
        None,
    )
    if code_at is None:
        return None
    code_row = rows[code_at]
    header = rows[cols_at + 1 : code_at + 1]
    rows_at = code_at
    if code_at + 1 < len(rows) and (
        _is_banner(rows[code_at + 1], _ROWS_BANNER)
        or _is_banner(rows[code_at + 1], _BRACKETED)
    ):
        rows_at = code_at + 1
    label_row = next((r for r in header if r is not code_row), [""] * len(code_row))
    cols: list[Option] = []
    seen: set[int] = set()
    for label, code in zip(label_row + [""] * len(code_row), code_row, strict=False):
        if not code.strip() or int(code) in seen:
            continue  # a merged cell repeats its column; keep it once
        seen.add(int(code))
        cols.append(Option(code=int(code), label=_clean(label) or code.strip()))
    if len(cols) < _MIN_GRID_COLUMNS:
        return None

    statements: list[Option] = []
    conditions: dict[str, str] = {}
    for row in rows[rows_at + 1 :]:
        raw = _merged_value(row)
        label = _clean(raw)
        if not label:
            continue
        condition = _ROW_CONDITION.search(raw)
        if condition:
            conditions[label] = condition.group(1).strip()
        statements.append(Option(code=len(statements) + 1, label=label))
    if len(statements) < 1:
        return None
    return TableQuestion(
        rows=statements,
        cols=cols,
        randomize_rows=bool(re.search(r"RANDOMI[SZ]E", " ".join(rows[rows_at]), re.I)),
        evidence=f"a stacked COLUMNS/ROWS table: {len(cols)}-point scale over "
                 f"{len(statements)} rows",
        row_conditions=conditions or None,
    )


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
        whole = stacked_grid(rows) or grid(rows)
        if whole:
            return whole

    scale_table = next((t for t in tables if scale_strip(t)), None)
    if scale_table is None:
        return None
    scale = scale_strip(scale_table)
    # Never from the scale's own table: its code row "1 | 2 | 3" reads as a
    # statement numbered 1 called "2", and one statement sank the whole grid.
    statements = next(
        (s for s in (statements_table(t) for t in tables if t is not scale_table) if s), None
    )
    if not statements:
        statements = [o for o in prose_options if o.label.strip()]
        if len(statements) < _MIN_GRID_ROWS:
            # One stray option read out of the prose is not a statement list.
            # It used to be enough to fail the whole shape, which threw the
            # SCALE away too and left "How much do you like this ad?" with no
            # answers at all on a live study.
            statements = []
    if not statements:
        # "How likely are you to consider each of these brands?" over a 0-10
        # strip, and no list under it: the rows are a list stated EARLIER, and
        # `list_sources.fill_lists` takes them from there. Throwing the scale
        # away too left a grid with no rows AND no columns — a blank page.
        return TableQuestion(
            rows=[], cols=list(scale),
            evidence=f"a {len(scale)}-point scale; its rows are a list stated elsewhere",
        )
    if len(statements) < _MIN_GRID_ROWS:
        return None
    return TableQuestion(
        rows=list(statements),
        cols=list(scale),
        evidence=f"a {len(scale)}-point scale table over {len(statements)} statements",
    )


#: "COLUMNS" over the scale, "ROWS" over the statements — a grid written as
#: labelled lists of plain paragraphs rather than as a table. Q18a of the Vivo
#: wave-2 document is written this way, and its column list was read as the
#: question's ANSWER options, leaving a grid with no rows and no columns.
_SECTION_LABEL = re.compile(r"^\s*(columns?|scale|rows?|statements?|items?)\s*:?\s*$", re.I)
_COLUMN_WORDS = frozenset({"column", "columns", "scale"})


def labelled_sections(lines: list[str]) -> tuple[list[Option], list[Option]]:
    """(rows, cols) from "ROWS"/"COLUMNS" headings over plain lines.

    Codes are positional, the way a scripter reads them off the page: the
    first line under COLUMNS is 1. Returns empty lists when the document does
    not use this convention, which is most of the time.
    """
    rows: list[str] = []
    cols: list[str] = []
    target: list[str] | None = None
    for line in lines:
        label = _SECTION_LABEL.match(line or "")
        if label:
            target = cols if label.group(1).lower() in _COLUMN_WORDS else rows
            continue
        if target is None or not (line or "").strip():
            continue
        target.append(line.strip())
    def coded(items: list[str]) -> list[Option]:
        return [Option(code=i, label=text) for i, text in enumerate(items, start=1)]

    return coded(rows), coded(cols)


#: The header of a column you fill in: "% of TRD Patients", "Number of patients".
_MEASURE_HEADER = re.compile(r"%|\bpercent|\bshare\b|\bnumber\b|#|\bcount\b|\bhow many\b", re.I)
#: What a fill-in cell holds when it is not empty: a scripter note or a blank.
_FILL_CELL = re.compile(r"^\s*(?:\[[^\]]*\]|_+\s*%?|%)?\s*$")


def entry_table(rows: list[list[str]]) -> tuple[list[Option], bool] | None:
    """Rows to type a number into, under a header naming the measure.

        Treatment                     | % of TRD Patients
        Treated with TMS Only         | [DROP DOWN % - TO BE CONFIRMED]
        Treated with Spravato Only    |

    Read as an answer list, the header became a choice and the respondent was
    asked to pick ONE treatment instead of giving a share for each. Returns
    the rows and whether the measure is a percentage; None for any other shape.
    """
    cleaned = []
    for row in rows:
        cells = [_clean(c) for c in row]
        # Word repeats a merged cell's text across every column it spans.
        cells = [c for i, c in enumerate(cells) if i == 0 or c != cells[i - 1] or not c]
        if any(cells):
            cleaned.append(cells)
    if len(cleaned) < 1 + _MIN_GRID_ROWS:
        return None
    header, body = cleaned[0], cleaned[1:]
    if len(header) != 2 or not header[0] or not _MEASURE_HEADER.search(header[1]):
        return None
    if any(len(row) != 2 or not row[0] or not _FILL_CELL.match(row[1]) for row in body):
        return None
    labels = [Option(code=i, label=row[0]) for i, row in enumerate(body, start=1)]
    return labels, "%" in header[1] or "percent" in header[1].lower()

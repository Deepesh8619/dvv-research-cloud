"""Numbers that must add up: one box per item, one total.

    Q37. Thinking about your monthly skincare spend, roughly what percentage
         goes to each of the following?
         ___ % Brands I already know and trust
         ___ % Brands that are new to me or I'm trying for the first time
         Total: 100% [PN: MUST SUM TO 100]

Two numbers, one per line, that must total 100. Read as an answer list it was
a single choice between two blanks and "Total: 100%" — or, typed as a grid, a
grid with no rows. Either way the respondent could not give an answer and the
data file had no column for the two shares.

It is a number question with ROWS: one box, and one data column, per item, and
a rule that they sum to the total the document states.

The same question is just as often written as a table — a unit column with
empty cells and one row per item:

    [0-100, MUST SUM TO 100%]
                  | % |
    Talk therapy alone |  |
    1st or 2nd oral antidepressant trial |  |

Unrecognised, that went to a model, which called it a rating grid with no scale
— and the respondent saw the wording with nothing to fill in.
"""

from __future__ import annotations

import re

from src.core.survey_definition import Option, QuestionType, SurveyDefinition
from src.engines.questionnaire import schema

# "___ % Brands I already know and trust", "____ Hours on weekdays"
_BOX_LINE = re.compile(r"^\s*_{2,}\s*%?\s*(?P<label>\S.*?)\s*$")
# "MUST SUM TO 100", "should add up to 100%", "Total: 100%"
_TOTAL = re.compile(
    r"(?:SUM\s+TO|ADD\s+UP\s+TO|TOTAL\s*[:=]?)\s*(?P<n>\d{1,4})\s*%?", re.I
)
# Only an explicit sum makes an empty table a constant sum; a bare "Total" row
# appears in ordinary grids too.
# "MUST SUM TO 100%", "MUST ADD TO AT LEAST 100 PERCENT" — the second for items
# a respondent can count more than once (a patient on two treatments).
_SUM = re.compile(
    r"(?:SUM|ADD)(?:\s+UP)?\s+TO\s+(?P<least>AT\s+LEAST\s+)?(?P<n>\d{1,4})", re.I
)
# A serialized table row: cells joined by "|". "Talk therapy alone |  | "
_CELL_SPLIT = re.compile(r"\s*\|\s*")
_MIN_ITEMS = 2


def _table_items(lines: list[str]) -> list[str]:
    """Row labels of a table whose every other cell is an empty box to fill."""
    items: list[str] = []
    has_unit_header = False
    for line in lines:
        if "|" not in line:
            continue
        cells = _CELL_SPLIT.split(line.strip())
        label, rest = cells[0].strip(), [c.strip() for c in cells[1:]]
        if not label and any(rest):
            has_unit_header = True  # " | % | ": the column every row fills
        elif label and not any(rest):
            items.append(label)
    return items if has_unit_header else []


def read(text: str, tables: bool = True) -> tuple[list[str], int | None, bool] | None:
    """The items and the total of a constant sum, or None if this is not one.

    `tables=False` reads only the "___ % item" form. The table form is decided
    where a question's own type is still open; over a whole stored question it
    misfired on a single-choice question whose text had swallowed the NEXT
    question's sum table."""
    lines = (text or "").splitlines()
    items = [m.group("label") for line in lines if (m := _BOX_LINE.match(line))]
    if len(items) >= _MIN_ITEMS:
        total = _TOTAL.search(text)
        return items, int(total.group("n")) if total else None, False
    stated = _SUM.search(text or "") if tables else None
    if stated:
        items = _table_items(lines)
        if len(items) >= _MIN_ITEMS:
            return items, int(stated.group("n")), bool(stated.group("least"))
    return None


def make_constant_sum(q, items: list[str], total: int | None, at_least: bool = False) -> None:
    """One number box per item, with the sum the document states — exact, or a
    floor when `at_least`."""
    q.type = QuestionType.NUMERIC
    q.rows = [Option(code=i, label=label) for i, label in enumerate(items, start=1)]
    q.options = []
    q.cols = None
    validation = dict(q.validation or {})
    validation["min"] = 0
    if total:
        validation["sum_min" if at_least else "sum"] = total
        validation["max"] = total
    q.validation = validation
    q.extraction_evidence["type"] = (
        f"{len(items)} boxes to fill"
        + (f", totalling {'at least ' if at_least else ''}{total}" if total else "")
    )
    if schema.FLAG_EMPTY_OPTIONS in q.flags:
        q.flags.remove(schema.FLAG_EMPTY_OPTIONS)


def apply(survey: SurveyDefinition) -> list[str]:
    """Turn every constant-sum list or table into a constant-sum number question."""
    changed = []
    for q in survey.questions:
        found = read(q.source_text or "", tables=False)
        if found is None:
            continue
        make_constant_sum(q, *found)
        changed.append(q.qid)
    if changed:
        survey.metadata["constant_sums"] = changed
    return changed

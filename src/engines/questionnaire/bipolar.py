"""Bipolar grids: two opposite statements per row, one scale between them.

    Q41. Now you'll see pairs of opposite statements about skincare...
    0=Agree fully with this statement | 1 | ... | 5=Neutral | ... | 10=Agree fully
     | [PN: ... HALF SEE LEFT AND RIGHT STATEMENTS FLIPPED] | ... | Theme [DO NOT SHOW]
    Q41_1 | I stick with the brands I know | I'm always on the lookout for new | Loyalty

A grid like this was read as one whose rows were "0=Agree fully with" and
"Theme". The document states the whole thing in a table, so it is read from
the table: row n is `Q41_n`, its label "left | right" (the form an analyst
reads, and the one the data map carries).

"Half see them flipped" is an order-effect control, like block rotation:
whichever statement sits on the left is agreed with more. Each respondent is
dealt an arm least-fill, the flipped arm sees right-hand statements on the
left AND the scale reversed, so a code keeps its meaning (0 is always "agree
fully with the left statement as written") and the data needs no recoding.
The arm is recorded as a hidden variable so the analyst can control for it.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from src.core.survey_definition import Option, Question, QuestionType

BIPOLAR_KEY = "bipolar"
#: The arm each respondent was dealt, delivered as a hidden variable.
FLIP_VARIABLE = "HID_BIPOLAR_FLIP"
FLIP_LABELS = {1: "Statements as written", 2: "Left and right flipped"}
FLIP_ARMS = ["1", "2"]
SEPARATOR = " | "

_PAIR_WORDS = re.compile(
    r"\bpairs?\b|\bopposite\b|one\s+statement\s+or\s+the\s+other|\bleft\b.*\bright\b|"
    r"\bbipolar\b|semantic\s+differential", re.I | re.S,
)
_FLIP_WORDS = re.compile(r"\bflip(?:ped)?\b|\breverse\s+(?:the\s+)?(?:sides|statements)\b", re.I)
_HIDDEN_CELL = re.compile(r"do\s+not\s+show", re.I)


def _table_rows(q: Question) -> list[tuple[int, list[str]]]:
    """(code, text cells) for each `Q41_n | … | …` line of the question's table."""
    stem = re.escape(q.qid)
    line = re.compile(rf"^\s*{stem}(?:_|\.|r)?(\d+)\s*\|(.*)$", re.I)
    out = []
    for raw in (q.source_text or "").splitlines():
        m = line.match(raw)
        if m:
            cells = [c.strip() for c in m.group(2).split("|")]
            out.append((int(m.group(1)), [c for c in cells if c]))
    return out


def _hidden_columns(q: Question) -> int:
    """How many trailing columns the header marks [DO NOT SHOW] (a theme label)."""
    for raw in (q.source_text or "").splitlines():
        cells = [c.strip() for c in raw.split("|")]
        if len(cells) >= 3 and any(_HIDDEN_CELL.search(c) for c in cells):
            return sum(1 for c in cells if _HIDDEN_CELL.search(c))
    return 0


def read_table(q: Question) -> Question | None:
    """The question with its rows read from the document's own table, or None.

    Two statement cells and pair wording make it bipolar; one cell is a plain
    row list. Anything else (cells that disagree in number) is left alone.
    """
    rows = _table_rows(q)
    if len(rows) < 2:
        return None
    hidden = _hidden_columns(q)
    shown = [cells[: len(cells) - hidden] if hidden and len(cells) > hidden else cells
             for _code, cells in rows]
    widths = {len(c) for c in shown}
    if len(widths) != 1:
        return None
    width = widths.pop()
    after = copy.deepcopy(q)
    if width == 2 and _PAIR_WORDS.search(q.text or q.source_text or ""):
        after.rows = [Option(code, SEPARATOR.join(cells)) for (code, _), cells in zip(
            rows, shown, strict=True)]
        after.validation = {
            **(q.validation or {}),
            BIPOLAR_KEY: {"flip_half": bool(_FLIP_WORDS.search(q.source_text or ""))},
        }
    elif width == 1:
        after.rows = [Option(code, cells[0]) for (code, _), cells in zip(
            rows, shown, strict=True)]
    else:
        return None
    if after.type is not QuestionType.GRID_SINGLE and after.cols:
        after.type = QuestionType.GRID_SINGLE
    return after


def is_bipolar(q: Question) -> bool:
    return bool((q.validation or {}).get(BIPOLAR_KEY))


def flips(q: Question) -> bool:
    return bool(((q.validation or {}).get(BIPOLAR_KEY) or {}).get("flip_half"))


def split(label: str) -> tuple[str, str]:
    left, _, right = (label or "").partition(SEPARATOR.strip())
    return left.strip(), right.strip()


def flipping_questions(survey: Any) -> list[str]:
    return [q.qid for q in survey.questions if q.type is QuestionType.GRID_SINGLE and flips(q)]


def apply_flip(surveyjs_json: dict, qids: list[str]) -> dict:
    """The flipped arm: right-hand statements on the left, the scale reversed.

    Codes are untouched, so 0 still means "agree fully with the statement
    written on the left in the document" whichever side the respondent saw it.
    """
    out = copy.deepcopy(surveyjs_json)
    wanted = set(qids)
    for page in out.get("pages") or []:
        for element in page.get("elements") or []:
            if element.get("name") not in wanted:
                continue
            element["columns"] = list(reversed(element.get("columns") or []))
            for row in element.get("rows") or []:
                left, right = row.get("leftText"), row.get("rightText")
                if left is not None and right is not None:
                    row["leftText"], row["rightText"] = right, left
                    row["text"] = f"{right}  ⟷  {left}"
    return out

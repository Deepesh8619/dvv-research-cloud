"""Which items a respondent "picked" when the source of a mask is a GRID.

A mask narrows one question's list to what the respondent chose earlier:
"ONLY DISPLAY BRANDS AWARE FROM Q2a". When Q2a is a multi-select that is a
list of codes and there is nothing to decide. When it is a grid — one row per
brand, one column per answer — it is a `{row: column}` map, and "which brands
did they pick" needs a reading:

    Q5. Which of these pharmacies have you heard of?
                        Yes     No
        CVS             (x)     ( )
        Walgreens       ( )     (x)

The mask wants CVS, and nothing about `{1: 1, 2: 2}` says so on its own.

**The rule: a row counts unless it was answered with a negative column.**
Read against a Yes/No grid that means the rows answered Yes. Read against
"Daily / Weekly / Monthly / Never" it means every row except the ones answered
Never — which is what a scripter means by "brands they use". And a grid with
no negative column at all (a 0-10 rating per brand) means every row they
answered, which is the only sensible reading of "the ones they rated".

Before this, a grid-sourced mask compiled to `{Q5} = {item}` — an object
compared to an option code, never true — so a question masked on an awareness
grid offered the respondent NOTHING. It looked like a question with no answers
rather than like a bug.
"""

from __future__ import annotations

import re
from typing import Any

#: Columns that mean "not this one". Matched on the whole label, so "Never"
#: is negative and "Never heard of, but would try" is not treated as one.
_NEGATIVE = re.compile(
    r"^\s*(no|none|never|not\s+at\s+all|not\s+aware|not\s+familiar|don'?t\s+know|"
    r"do\s*n'?t\s+use|have\s+not\s+used|none\s+of\s+(these|the\s+above)|n/?a)\s*[.!]?\s*$",
    re.I,
)


def affirmative_codes(grid: Any) -> list[int]:
    """The column codes that mean "yes, this row counts"."""
    columns = list(getattr(grid, "cols", None) or [])
    if not columns:
        return []
    positive = [c.code for c in columns if not _NEGATIVE.match(c.label or "")]
    # Every column reads as a negative (a badly labelled grid): rather than
    # decide nobody picked anything, treat any answer as an answer.
    return positive or [c.code for c in columns]


def mask_codes(source: Any, answer: Any) -> set[int] | None:
    """The item codes this answer "chose", for masking. None if unreadable.

    Deliberately NOT the same function routing uses. A routing condition
    against a grid asks what was ANSWERED ("any row answered 1"); a mask asks
    which ROWS were picked. Same data, two questions — conflating them is how
    a mask ends up offering a list of column codes.
    """
    if answer is None:
        return None
    if isinstance(answer, dict):
        positive = set(affirmative_codes(source))
        picked: set[int] = set()
        for row, value in answer.items():
            row_code = _as_code(row)
            if row_code is None or value is None:
                continue
            values = value if isinstance(value, (list, tuple, set)) else [value]
            if any(_as_code(v) in positive for v in values):
                picked.add(row_code)
        return picked
    if isinstance(answer, (list, tuple, set)):
        return {c for c in (_as_code(v) for v in answer) if c is not None}
    code = _as_code(answer)
    return {code} if code is not None else set()


def _as_code(value: Any) -> int | None:
    """A punch code as an int. Grid rows arrive as strings over JSON."""
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def variable_name(qid: str) -> str:
    """The survey variable the browser sets for a grid-sourced mask."""
    return f"{qid}_picked"


def mask_code_map(question: Any, source: Any) -> dict[int, int]:
    """{source code: this question's code} for answers the two lists share by label.

    A list coded by its own order (Bubble Q6: CeraVe 2) masked by an awareness
    list coded alphabetically (Q2a: CeraVe 5) shares no codes worth comparing;
    the brand NAME is what the mask means. Identity where the codes agree.
    """
    own = {(o.label or "").strip().casefold(): o.code
           for o in [*(question.options or []), *(question.rows or [])]}
    out: dict[int, int] = {}
    for o in [*(source.options or []), *(source.rows or [])]:
        code = own.get((o.label or "").strip().casefold())
        if code is not None:
            out[o.code] = code
    return out

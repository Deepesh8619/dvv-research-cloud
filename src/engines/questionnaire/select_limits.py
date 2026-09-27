"""How many options a multi-punch lets a respondent tick.

"Select up to 2." is an instruction to the RESPONDENT that nothing was reading.
It survived extraction as part of the question wording, was shown on screen,
and then nothing enforced it: the respondent could tick every option in the
list, and the data came back with more punches than the questionnaire allows.

Nobody sees that as a bug. The question looks right on screen because the
sentence is right there in it, and a tab of a question capped at two shows
percentages that sum past what a cap would allow — which reads as a
multi-punch doing what multi-punches do.

Read here rather than in either parser because both conventions state it: the
prose documents put it in the wording ("Select up to 2."), the spec tables put
it in an `Other details:` row. One reader, so a cap means the same thing in
both.

Bounds only. Nothing here decides what happens when a respondent exceeds one —
that is the compiler's job in the browser and the fielding route's on the
server, and they must agree.
"""

from __future__ import annotations

import re

#: Documents write small counts as words as often as digits ("your top three").
_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
_COUNT = r"(?P<n>\d{1,2}|" + "|".join(_WORDS) + r")"

# A CEILING. "no more than", "a maximum of", "up to" — and "top N", which is how
# a document writes a cap when it also wants the best ones named first.
_MAX_RE = re.compile(
    r"\b(?:select|choose|pick|tick|check|mark)\b[^.?!]{0,30}?"
    r"\b(?:up\s+to|no\s+more\s+than|at\s+most|a\s+maximum\s+of|max(?:imum)?\s+of)\s+"
    + _COUNT + r"\b",
    re.I,
)
_TOP_N_RE = re.compile(
    r"\b(?:select|choose|pick|rank)\b[^.?!]{0,20}?\btop\s+" + _COUNT + r"\b", re.I
)
# A FLOOR. Rarer, and the one that strands a respondent when it is wrong.
_MIN_RE = re.compile(
    r"\b(?:select|choose|pick|tick|check|mark)\b[^.?!]{0,30}?"
    r"\b(?:at\s+least|no\s+fewer\s+than|a\s+minimum\s+of|min(?:imum)?\s+of)\s+"
    + _COUNT + r"\b",
    re.I,
)
# BOTH at once: "Select exactly 3", "Please select 3 options".
_EXACT_RE = re.compile(
    r"\b(?:select|choose|pick|tick|check|mark)\s+(?:exactly\s+)?" + _COUNT
    + r"\s+(?:of\s+the\s+following|options?|answers?|items?|responses?|choices?)\b",
    re.I,
)


# "Select one answer PER ROW", "one response for each statement" — a grid's
# instruction about each row, not a cap on the question. Read as a cap it says
# the whole grid takes one answer, which is the opposite of what a grid is.
_PER_ITEM_RE = re.compile(
    r"\b(?:per|for\s+each|in\s+each|each)\s+"
    r"(?:row|statement|item|brand|attribute|column|one)\b",
    re.I,
)


def _number(token: str) -> int:
    return _WORDS.get(token.lower(), 0) or int(token)


def read_limits(text: str) -> dict[str, int]:
    """The select bounds a question's own wording states, as a validation dict.

    Returns {} when it says nothing — which is most questions, and must stay
    cheap and silent. A bound is only ever read from an explicit instruction;
    nothing is inferred from the length of the option list.
    """
    if not text or _PER_ITEM_RE.search(text):
        return {}
    limits: dict[str, int] = {}

    exact = _EXACT_RE.search(text)
    if exact:
        n = _number(exact.group("n"))
        # "Select 1 of the following" is a single-punch, not a capped multi.
        return {"min_select": n, "max_select": n} if n > 1 else {"max_select": 1}

    for pattern in (_MAX_RE, _TOP_N_RE):
        found = pattern.search(text)
        if found:
            limits["max_select"] = _number(found.group("n"))
            break

    floor = _MIN_RE.search(text)
    if floor:
        limits["min_select"] = _number(floor.group("n"))

    # A floor above the ceiling is not a bound, it is a contradiction — and
    # applied it would reject every possible answer. Keep both so preflight can
    # say so; refusing to guess which one the scripter meant is the point.
    return limits


if __name__ == "__main__":  # self-check
    assert read_limits("Select up to 2.") == {"max_select": 2}
    assert read_limits("Please choose up to three brands") == {"max_select": 3}
    assert read_limits("Select at least 2 and no more than 5") == {
        "min_select": 2, "max_select": 5
    }
    assert read_limits("Select your top 3") == {"max_select": 3}
    assert read_limits("Select exactly 3 options") == {"min_select": 3, "max_select": 3}
    assert read_limits("Which brands do you know?") == {}
    assert read_limits("Select all that apply") == {}
    assert read_limits("Select one") == {}
    # A grid's per-row instruction is not a cap on the grid.
    assert read_limits("Select one answer per row.") == {}
    assert read_limits("Please give one response for each statement") == {}
    # A contradiction is preserved, never silently resolved.
    assert read_limits("Select at least 5, up to 2") == {"min_select": 5, "max_select": 2}
    print("ok")

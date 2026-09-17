"""Who sees each turn of a loop, from the definitions the document lists.

A looped question does not have one base — it has one per iteration, and the
document spells them out:

    Among who: Provider Current or Lapsed User based on DA_PLATFORM_USAGE
    [Google Ads current user (DA_PLATFORM_USAGE 1,2,3, or 4=5),
     Google Ads Lapsed User (... DA_PLATFORM_USAGE 1, 2, 3, or 4 = 4),
     LinkedIn Current Users (DA_PLATFORM_USAGE 10 = 5),
     LinkedIn Lapsed Users (DA_PLATFORM_USAGE 10 = 4), ...]

Each entry names a brand, a user state, and an exact condition on rows of an
earlier grid: "DA_PLATFORM_USAGE 10 = 5" is row 10 answered 5, and "1,2,3, or
4=5" is any of rows 1 to 4 answered 5, because one brand can span several rows.

Matching them to the loop's iterations is the whole job. An iteration whose
brand appears in no definition gets NO base and keeps its flag: asking every
brand of everybody is the failure this exists to prevent, and a guess here is
indistinguishable from a correct answer in the data.
"""

from __future__ import annotations

import re

from src.core.survey_definition import Expression

# One definition: "<label> (<QID> <rows> = <value>)". The condition is inside
# the brackets; the words before it name the brand and the state.
_DEFINITION_RE = re.compile(
    r"(?P<label>[A-Za-z][A-Za-z0-9 .&'/-]{1,60}?)\s*"
    r"\(\s*(?:NOT\s+[^()]*?AND\s+)?"
    r"(?P<qid>[A-Za-z][A-Za-z0-9_]{2,40})\s*"
    # "1,2,3, or 4" — a comma AND a conjunction between the last two, which is
    # how anybody writes a list. Requiring one separator matched the single-row
    # brands and silently skipped every multi-row one, so the two biggest
    # brands in the study got no base at all.
    r"(?P<rows>\d+(?:\s*,?\s*(?:or|and)?\s*\d+)*)\s*"
    r"=\s*(?P<value>\d+)\s*\)",
    re.I,
)
_ROW_RE = re.compile(r"\d+")


def parse_iteration_bases(text: str) -> dict[str, Expression]:
    """Label → the condition that admits it, for every definition stated.

    Labels are lowercased and stripped of the state words ("current user",
    "lapsed users") so "Google Ads current user" and "Google Ads Lapsed User"
    both answer to "google ads" — a brand is shown its questions if it is in
    ANY of the states listed for it, which is what "Current or Lapsed User"
    says.
    """
    by_label: dict[str, list[Expression]] = {}
    for match in _DEFINITION_RE.finditer(text or ""):
        label = _brand(match.group("label"))
        if not label:
            continue
        rows = [int(r) for r in _ROW_RE.findall(match.group("rows"))]
        value = int(match.group("value"))
        qid = match.group("qid")
        # "NOT Google Ads Current User AND ..." qualifies a LAPSED state. The
        # negated half is another brand's state, not this one's, and reading it
        # here would exclude people the document includes — so only the exact
        # half is taken, and the two states are OR-ed together below anyway.
        alternatives = [
            Expression(
                op="in", qid=qid, row=row, values=[value],
                verbatim_text=match.group(0).strip(),
            )
            for row in rows
        ]
        condition = (
            alternatives[0]
            if len(alternatives) == 1
            else Expression(op="or", children=alternatives,
                            verbatim_text=match.group(0).strip())
        )
        by_label.setdefault(label, []).append(condition)

    return {
        label: (
            conditions[0]
            if len(conditions) == 1
            else Expression(op="or", children=conditions,
                            verbatim_text="; ".join(
                                c.verbatim_text or "" for c in conditions))
        )
        for label, conditions in by_label.items()
    }


_STATE_WORDS = re.compile(
    r"\b(current|lapsed|non|past|former|users?|user|aware|awareness)\b", re.I
)
# "OR  TikTok Lapsed Users" — the conjunction that joined this entry to the
# previous one is not part of the brand's name, and left on it produced a brand
# nobody would ever match against.
_LEADING_CONJUNCTION = re.compile(r"^\s*(?:or|and)\b\s*", re.I)


def _brand(label: str) -> str:
    """The brand a definition is about, with the state words removed."""
    without_state = _STATE_WORDS.sub(" ", _LEADING_CONJUNCTION.sub("", label or ""))
    return " ".join(without_state.split()).strip(" .,-").lower()


def base_for(label: str, bases: dict[str, Expression]) -> Expression | None:
    """The condition for one iteration, or None when the document names none.

    Matched on the brand name alone. None is a real answer — the caller leaves
    the iteration unrouted and flagged, rather than admitting everybody.
    """
    wanted = _brand(label)
    if wanted in bases:
        return bases[wanted]
    # "LinkedIn" against a definition written as "LinkedIn Ads", or the reverse.
    for known, condition in bases.items():
        if known and (known in wanted or wanted in known):
            return condition
    return None

"""A bracketed directive sitting UNDER an answer belongs to that answer.

    Variable list:
    1: Google Ads
    [IF GOOGLE ADS CURRENT USER]
    2: Meta Ads
    [IF META ADS CURRENT USER]
    [INSERT CURRENTLY USED BRANDS FROM DA_PLATFORM_USAGE 9-15]
    99: None of these

Every line here says something, and the parser was reading the brackets as
answers in their own right — a respondent offered "[IF GOOGLE ADS CURRENT
USER]" as a choice, and the real answers diluted by junk codes that go into
the data file.

Three kinds, told apart because they are not equally certain:

* **A base on one answer.** "[IF GOOGLE ADS CURRENT USER]" means option 1 is
  shown only to current Google Ads users. Who those are is defined somewhere
  else in the document — a hidden variable, a punch range on an earlier
  question — and guessing which would put a condition nobody wrote in front
  of a respondent. The wording is kept verbatim on the option and flagged, so
  a reviewer connects it to its source in one click instead of discovering it
  in the data.

* **A base that names its question.** "[IF SELECTED AT Q7]", "[IF Q13=1-5]" —
  the source and its codes are right there, so the condition is built and no
  confirmation is needed.

* **More answers, from another question.** "[INSERT CURRENTLY USED BRANDS
  FROM DA_PLATFORM_USAGE 9-15]" is not a base at all: it says the list
  continues with codes 9 to 15 of DA_PLATFORM_USAGE, narrowed to the ones
  this respondent uses. Recorded as a list source for the pass that fills
  lists, not invented here.
"""

from __future__ import annotations

import re
from typing import Any

from src.core.survey_definition import Expression

#: A line that is ENTIRELY a bracketed directive: "[IF ...]", "[INSERT ...]".
BRACKETED = re.compile(r"^\s*[\[(]\s*(?P<body>.+?)\s*[\])]\s*$", re.S)

#: "IF ...", "ASK IF ...", "SHOW IF ...", "ONLY IF ..."
_IS_BASE = re.compile(r"^(?:only\s+)?(?:ask\s+|show\s+|display\s+)?if\b", re.I)

#: "[INSERT CURRENTLY USED BRANDS FROM DA_PLATFORM_USAGE 9-15]"
#: "[INSERT CURRENTLY USED BRANDS FROM DA_PLATFORM_USAGE 9-15]", and the same
#: thing said with AT: "[INSERT GOALS SELECTED AT DA_GOALS]". Only FROM was
#: accepted, so a response list that named its source question said nothing and
#: the question was left with no columns at all.
_INSERT_FROM = re.compile(
    r"\b(?:insert|show|pull|bring)\b.*?\b(?:from|at|in)\s+(?P<qid>[A-Za-z][A-Za-z0-9_]{1,40})"
    r"(?:\s+(?P<lo>\d{1,3})\s*[-–]\s*(?P<hi>\d{1,3}))?",
    re.I,
)
#: "[INSERT RESPONSE FROM Q8]" puts ONE earlier answer into the wording. That is
#: a pipe, not a list, and reading it as a list would claim this question's
#: answers come from Q8 when they do not.
_ONE_ANSWER = re.compile(r"\binsert\s+(?:response|rating|selection|answer)\b", re.I)

#: "IF Q13=1-5", "IF SELECTED AT Q7", "IF DA_USAGE 9-15". The leading keyword
#: is stripped before this runs, so a two-character qid like "S7" is reachable
#: without "IF" itself matching as the question name.
_NAMES_QUESTION = re.compile(
    r"\b(?:at\s+)?(?P<qid>[A-Z][A-Z0-9_]{1,40})\b\s*"
    r"(?:=\s*)?(?P<lo>\d{1,3})?(?:\s*[-–]\s*(?P<hi>\d{1,3}))?",
)

#: What sits in front of the condition and is not part of it.
_LEADING = re.compile(r"^(?:only\s+)?(?:ask\s+|show\s+|display\s+)?if\b\s*", re.I)


def read(line: str) -> dict[str, Any] | None:
    """What one bracketed line states, or None when it is not one."""
    match = BRACKETED.match(line or "")
    if not match:
        return None
    body = match.group("body").strip()

    insert = _INSERT_FROM.search(body)
    if insert and not _IS_BASE.match(body) and not _ONE_ANSWER.search(body):
        codes = None
        if insert.group("lo") and insert.group("hi"):
            codes = [int(insert.group("lo")), int(insert.group("hi"))]
        return {"kind": "list_from", "verbatim": body,
                "source_qid": insert.group("qid"), "codes": codes}

    if _IS_BASE.match(body):
        return {"kind": "base", "verbatim": body, "condition": _condition(body)}
    return None


def _condition(body: str) -> Expression | None:
    """A real condition when the wording names a question, else None.

    None is the answer for "IF GOOGLE ADS CURRENT USER": the reviewer is
    shown the words and connects them. Inventing a qid here would put a
    condition nobody wrote in front of a respondent.
    """
    named = _NAMES_QUESTION.search(_LEADING.sub("", body))
    if not named or not named.group("lo"):
        return None
    qid, low = named.group("qid"), int(named.group("lo"))
    high = int(named.group("hi")) if named.group("hi") else low
    if high < low:
        return None
    values = list(range(low, high + 1))
    return Expression(op="in", qid=qid, values=values, verbatim_text=body)


def attach(option: Any, line: str) -> str | None:
    """Put a directive on the option above it. Returns the kind attached."""
    found = read(line)
    if found is None or option is None:
        return None
    if found["kind"] == "base":
        option.show_if = found["condition"]
        return "base"
    return found["kind"]

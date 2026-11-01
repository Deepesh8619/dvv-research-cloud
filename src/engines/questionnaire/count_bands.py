"""Count bands: "selected ONLY TWO of punch 3 at S5a, S6, S7" as a group rule.

No count operator exists, and none is added: "exactly K of N sources" is written
with the operators every evaluator and the SurveyJS compiler already run. It is
an OR over every choice of K sources that have the punch, each choice an AND of
those sources plus "not" for the rest; none is an AND of "not"s. A leaf is the
same `in` test compile_condition writes for a single and a multi alike.
"""

from __future__ import annotations

import re
from itertools import combinations

from src.core.survey_definition import Expression, SurveyDefinition
from src.engines.questionnaire.conditions import _known_qid

MAX_SOURCES = 8
_WORDS = ["none", "one", "two", "three", "four", "five", "six", "seven", "eight"]
NOT_APPLICABLE = re.compile(r"^\s*n\s*/\s*a\s*\.?\s*$", re.I)
_COUNT = re.compile(
    r"^\s*(?:(?:did\s+)?select(?:ed)?\s+)?(?P<only>only\s+)?(?P<k>none|\d{1,2}|"
    + "|".join(_WORDS[1:]) + r")\s+of\s+punch(?:es)?\s+"
    r"(?P<codes>\d+(?:\s*(?:,|\bor\b|\band\b)\s*\d+)*)\s+(?:at|in)\s+(?P<qs>.+?)\s*\.?\s*$", re.I)


def is_count_form(rule: str) -> bool:
    return bool(_COUNT.match(rule))


def count_condition(rule: str, survey: SurveyDefinition) -> tuple[Expression, str]:
    """(the rule, a note on sources skipped), or ValueError saying why a person must decide."""
    m = _COUNT.match(rule)
    if not m:
        raise ValueError(f"cannot read the count in {rule.strip()!r}")
    word = m.group("k").lower()
    k = 0 if word == "none" else int(word) if word.isdigit() else _WORDS.index(word)
    codes = [int(c) for c in re.findall(r"\d+", m.group("codes"))]
    named = [q for q in re.split(r"\s*(?:,|\bor\b|\band\b)\s*", m.group("qs"), flags=re.I) if q]
    if len(named) > MAX_SOURCES:
        raise ValueError(f"counts across {len(named)} questions; more than {MAX_SOURCES} "
                         "needs a person")
    sources, skipped = [], []
    for name in named:
        qid = _known_qid(name, survey)  # ValueError names a question that does not exist
        options = {o.code for o in next(q for q in survey.questions if q.qid == qid).options or []}
        (sources if not options or set(codes) <= options else skipped).append(qid)
    if not sources:
        raise ValueError(f"punch {', '.join(map(str, codes))} is not an answer at "
                         + ", ".join(skipped))
    if k > len(sources):
        raise ValueError(f"needs {k} sources with punch {codes[0]} but only "
                         f"{', '.join(sources)} have it")
    if k and not m.group("only") and k != len(named):
        raise ValueError(f"says selected {word} of {len(named)} without \"only\": "
                         "at least or exactly?")

    def has(qid: str) -> Expression:
        return Expression(op="in", qid=qid, values=list(codes))

    def lacks(qid: str) -> Expression:
        return Expression(op="not", children=[has(qid)])

    ways = [Expression(op="and", children=[has(s) if s in c else lacks(s) for s in sources])
            for c in combinations(sources, k)]
    detail = f"punch {', '.join(map(str, codes))} is not an answer at {', '.join(skipped)}; " \
             "left out of the count" if skipped else ""
    expr = ways[0] if len(ways) == 1 else Expression(op="or", children=ways)
    return expr, detail

"""Read the questionnaire the way an analyst does: the brief first, then check it.

A questionnaire opens by saying what the study needs — quota groups, who must
qualify, programming rules, the variables a scripter has to create — and only
then starts asking. An analyst reads that page, then reads the questions with
it in hand, ticking each need off. This is that tick list: every need the
document stated, whether the instrument meets it, and where.

It also records the document's own conventions (how it labels questions, how it
marks programmer notes) so a reader of the review page, or the copilot, can
talk about THIS document rather than questionnaires in general.

Nothing here changes the survey. A need marked "missing" is a question for the
person who knows the study, never something fixed on a guess.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from src.core.survey_definition import SurveyDefinition
from src.engines.questionnaire.buckets import HIDDEN, bucket_of
from src.engines.questionnaire.section_paths import _is_screen_id
from src.engines.questionnaire.study_overview import (
    _FIRST_NUMBER,
    _QUOTA_REQUIREMENT,
    _TOTAL_ROW,
    QuotaTarget,
    StudyOverview,
    _numbers_in,
    unenforced_requirements,
    unmeasurable_quotas,
)

# "CODE GENDER – BASED ON S3", "HIDDEN: AGE BAND", "COMPUTE NET SCORE" — a line
# that tells the scripter to make a variable, not to ask anybody anything.
_CREATE = re.compile(r"^\s*(?:CODE|HIDDEN|COMPUTE|CALCULATE|CREATE|DERIVE)\b", re.I)
# How this document marks a note for the scripter: "[PN: …]", "PROGRAMMER NOTE",
# "[SCRIPTER: …]", "{PROG: …}".
_PN_MARKER = re.compile(
    r"[\[{(]\s*(PN|PROG(?:RAMMER)?(?:\s+NOTE)?|SCRIPTER(?:\s+NOTE)?|INTERVIEWER(?:\s+NOTE)?)\s*[:.\-]"
    r"|^\s*(PROGRAMMER\s+NOTE|PROGRAMMING\s+NOTE|SCRIPTER\s+NOTE)\b",
    re.I | re.M,
)
_PREFIX = re.compile(r"^[A-Za-z]+")


def _number_index(survey: SurveyDefinition) -> dict[int, list[str]]:
    """Each number the instrument asks or routes on -> the questions using it."""
    index: dict[int, list[str]] = {}
    for q in survey.questions:
        found = _numbers_in(q.terminate_if) | _numbers_in(q.show_if)
        texts = [q.text or ""] + [o.label or "" for o in q.options or []]
        for option in q.options or []:
            found |= _numbers_in(option.show_if)
        found |= {int(n.replace(",", "")) for t in texts for n in _FIRST_NUMBER.findall(t)}
        for n in found:
            index.setdefault(n, []).append(q.qid)
    return index


def _where(text: str, index: dict[int, list[str]]) -> list[str]:
    """The questions using the MOST of the numbers a need names.

    "Micro 2-9 Employees": the employee-count question carries both 2 and 9;
    a dozen others carry one of them by accident.
    """
    hits: Counter = Counter()
    for n in {int(n.replace(",", "")) for n in _FIRST_NUMBER.findall(text)}:
        hits.update(index.get(n, []))
    if not hits:
        return []
    best = max(hits.values())
    return [q for q, n in hits.items() if n == best][:5]


def _overview(brief: dict) -> StudyOverview:
    return StudyOverview(
        screening_requirements=list(brief.get("screening_requirements") or []),
        quotas=[QuotaTarget(**q) for q in brief.get("quotas") or []],
    )


def _variables(survey: SurveyDefinition) -> list[dict[str, Any]]:
    items = []
    for q in survey.questions:
        says_create = bool(_CREATE.match(q.text or ""))
        made = bucket_of(q)[0] == HIDDEN
        if not (says_create or made):
            continue
        items.append({
            "kind": "variable",
            "text": f"{q.qid}: {(q.text or '').strip()[:120]}",
            "status": "covered" if made else "missing",
            "where": [q.qid],
            "why": "built as a hidden variable" if made else
                   "the document says to create this variable, "
                   "but it was read as an asked question",
        })
    return items


def _conventions(survey: SurveyDefinition) -> dict[str, Any]:
    asked = [q for q in survey.questions if not _is_screen_id(q) and q.qid != "UNKNOWN"]
    families = Counter(m.group(0).upper() for q in asked if (m := _PREFIX.match(q.qid)))
    markers: Counter = Counter()
    for q in survey.questions:
        for m in _PN_MARKER.finditer(q.source_text or ""):
            markers[re.sub(r"\s+", " ", (m.group(1) or m.group(2))).upper()] += 1
    return {
        "label_families": dict(families.most_common()),
        "pn_markers": dict(markers.most_common()),
    }


def brief_checklist(survey: SurveyDefinition) -> dict[str, Any]:
    """Every need the document stated, covered or missing, and where."""
    meta = survey.metadata or {}
    brief = meta.get("study_overview") or {}
    overview = _overview(brief)
    index = _number_index(survey)
    items: list[dict[str, Any]] = []

    blind = set(unmeasurable_quotas(overview, survey))
    for quota in overview.quotas:
        if _TOTAL_ROW.match(quota.group):
            continue
        stated = f"{quota.group} {quota.definition}".strip()
        has_number = bool(_FIRST_NUMBER.search(stated))
        items.append({
            "kind": "quota",
            "text": stated,
            "status": "missing" if quota.group.strip() in blind else
                      "covered" if has_number else "check",
            "where": _where(stated, index),
            "why": "no question measures this group" if quota.group.strip() in blind else
                   "a question asks the number it names" if has_number else
                   "names no number to match; confirm which question sorts it",
        })

    unmet = set(unenforced_requirements(overview, survey))
    for requirement in overview.screening_requirements:
        quota_like = bool(_QUOTA_REQUIREMENT.search(requirement))
        has_number = bool(_FIRST_NUMBER.search(requirement))
        status = ("missing" if requirement in unmet else
                  "covered" if has_number and not quota_like else "check")
        items.append({
            "kind": "screening",
            "text": requirement,
            "status": status,
            "where": [] if quota_like else _where(requirement, index),
            "why": {"missing": "no screener question enforces it",
                    "covered": "a screener question tests the number it names",
                    "check": "a quota or a rule with no number; confirm how it is met"}[status],
        })

    items += _variables(survey)

    qids = {q.qid.upper() for q in survey.questions}
    for note in brief.get("programming_notes") or []:
        if note.rstrip().endswith(":") or len(note.split()) < 3:
            continue  # "Grid question:" heads the notes under it; it is not one
        named = re.findall(r"\b[A-Z]{1,4}\d+[a-z]?\b", note)
        lost = [n for n in named if n.upper() not in qids]
        items.append({
            "kind": "note",
            "text": note,
            "status": "missing" if lost else "check",
            "where": [n for n in named if n.upper() in qids][:5],
            "why": f"names {', '.join(lost)}, which the instrument does not have" if lost else
                   "a rule for the whole study; confirm the script follows it",
        })

    screens = [q.qid for q in survey.questions if _is_screen_id(q)]
    counts = Counter(i["status"] for i in items)
    return {
        "items": items,
        "covered": counts["covered"],
        "missing": counts["missing"],
        "check": counts["check"],
        "screens": screens,
        "conventions": _conventions(survey),
    }

"""What kind of variable each question is, and how it connects to the others.

A report of eighty question cards in document order reads like the document.
A researcher reads it by KIND — who got in (screener), who they are (profile),
what the study measures (core), the per-brand repeats (loops), what was
computed rather than asked (hidden), what people wrote (open ends) — and by
DEPENDENCY: which question decides who sees this one, whose answers fill its
options, which later questions hang off it.

Every link here is read from what the questionnaire already states: bases,
screen-outs, masks, pipes, loops, derived variables. No model is asked and no
link is inferred from answers — an association found in data is a different
claim and belongs in analysis, labelled as such.

Only one bucket is a judgement: profile vs core measure. It is decided from
wording and marked `suggested`, so the reviewer is asked rather than told —
the same rule as hidden variables.
"""

from __future__ import annotations

import re
from typing import Any

from src.core.survey_definition import Question, QuestionType, SurveyDefinition
from src.engines.questionnaire import schema

SCREENER = "screener"
PROFILE = "profile"
CORE = "core"
LOOP = "loop"
HIDDEN = "hidden"
OPEN_END = "open_end"

#: Order the report shows them in: how a respondent meets them, roughly.
BUCKET_ORDER = [SCREENER, PROFILE, CORE, LOOP, OPEN_END, HIDDEN]

# "S1", "S10a", "SS_REVENUE", "SCR3" — how screeners are numbered in the wild.
_SCREENER_QID = re.compile(r"^(?:S\d|SS_|SCR)", re.I)
# Who the respondent is, not what they think.
_PROFILE_WORDS = re.compile(
    r"\b(age|how old|gender|identify as|sex|region|state|city|zip|postcode|"
    r"income|education|employ\w*|occupation|job title|household|children|"
    r"marital|ethnic\w*|race|religion|company size|people .* employed|industry)\b",
    re.I,
)
_QID_TOKEN = re.compile(r"\b[A-Z][A-Z0-9_]{1,30}\b")


def bucket_of(question: Question) -> tuple[str, str, bool]:
    """(bucket, why, suggested). `suggested` means a person should confirm it."""
    flags = set(question.flags or ())
    if schema.FLAG_DERIVED_VARIABLE in flags or question.qid.upper().startswith("HID_"):
        return HIDDEN, "computed from other answers, not asked", False
    if (question.validation or {}).get("loop"):
        return LOOP, "asked once per brand in a loop", False
    if question.terminate_if is not None:
        return SCREENER, "it can screen a respondent out", False
    if question.type is QuestionType.OPEN_TEXT:
        return OPEN_END, "the respondent types the answer", False
    # Wording before numbering: age and gender are usually asked in the
    # screener section, but a table is cut BY them — that is a profile variable.
    if _PROFILE_WORDS.search(question.text or ""):
        return PROFILE, "asks who the respondent is", True
    if _SCREENER_QID.match(question.qid):
        return SCREENER, f"numbered as a screener ({question.qid})", True
    return CORE, "what the study measures", True


def _mask_sources(question: Question) -> list[str]:
    techniques = (question.validation or {}).get("techniques") or {}
    sources = []
    for mask in techniques.get("masks") or []:
        source = mask.get("exclude_qid") or mask.get("source_qid")
        if source:
            sources.append(source)
    return sources


def _pipe_sources(question: Question) -> list[str]:
    techniques = (question.validation or {}).get("techniques") or {}
    return [p["source_qid"] for p in techniques.get("pipes") or [] if p.get("source_qid")]


def links(survey: SurveyDefinition) -> dict[str, dict[str, Any]]:
    """Per question: what it depends on, and what depends on it."""
    known = {q.qid for q in survey.questions}
    out: dict[str, dict[str, Any]] = {}
    for q in survey.questions:
        asked_if = sorted((q.show_if.referenced_qids() & known) - {q.qid}) if q.show_if else []
        # Only OTHER questions: "SS_RESP screens out on SS_RESP" says nothing the
        # screener bucket has not already said.
        screen = (
            sorted((q.terminate_if.referenced_qids() & known) - {q.qid}) if q.terminate_if else []
        )
        computed = []
        if bucket_of(q)[0] == HIDDEN:
            words = f"{q.text or ''} {q.source_text or ''}"
            computed = sorted({t for t in _QID_TOKEN.findall(words) if t in known} - {q.qid})
        loop = (q.validation or {}).get("loop") or {}
        out[q.qid] = {
            "asked_if": asked_if,
            "asked_if_text": (q.show_if.verbatim_text if q.show_if else None),
            "screens_out_on": screen,
            "options_from": [s for s in _mask_sources(q) if s in known],
            "text_from": [s for s in _pipe_sources(q) if s in known],
            "computed_from": computed,
            "loop_of": loop.get("of"),
            "used_by": [],
        }
    # The reverse edge: a question's answer decides something about these.
    for qid, link in out.items():
        for key in ("asked_if", "options_from", "text_from", "computed_from"):
            for source in link[key]:
                if source in out and qid not in out[source]["used_by"]:
                    out[source]["used_by"].append(qid)
        for source in link["screens_out_on"]:
            if source in out and qid not in out[source]["used_by"]:
                out[source]["used_by"].append(qid)
    return out

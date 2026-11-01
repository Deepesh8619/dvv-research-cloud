"""How an answer is entered, as the scripter's note states it.

"Allow a write-in whole number or check 'Prefer not to answer' (which cannot be
combined with any other response). Acceptable range: 1-120." settles a question
the reader could not type: a number box with one answer that closes it. A real
single or multi is never retyped; a question with no answers is never made one.
"""

from __future__ import annotations

import re

from src.core.survey_definition import Option, QuestionType
from src.engines.questionnaire.note_lines import wording_lines
from src.engines.questionnaire.prose_directives import _strip_label

_NUMBER = re.compile(r"\bwhole\s+number|\bacceptable\s+range\b|\bnumeric\b", re.I)
_ONE = re.compile(r"\b(?:allow|accept)\s+(?:only\s+)?one\s+response", re.I)
_MANY = re.compile(r"\b(?:allow|accept)\s+multiple\s+responses?\b(?!\s+except)", re.I)
_TEXT = re.compile(r"\bwrite\s+(?:in\s+)?(?:your\s+)?response\s+here|\bopen[- ]end(?:ed)?\s+response", re.I)  # noqa: E501
_OPT_OUT = re.compile(r"\bor\s+check\s+[‘'“\"]([^’'”\"]{2,60})[’'”\"]\s*\(\s*which\s+cannot\s+be\s+combined", re.I)  # noqa: E501
_MIN_LEN = re.compile(r"\bminimum\s+character\s+count\s*:?\s*(\d{1,5})|\b(\d{1,5})[- ]character\s+minimum", re.I)  # noqa: E501
_RANK = re.compile(r"\brank\s+1\s*,\s*2(?:\s*,\s*3)?(?:\s*,\s*(\d))*", re.I)


def _unsettled(q) -> bool:
    return q.type is QuestionType.INFO and "extraction_error" in (q.validation or {})


def apply(q, notes: list[str], made: list[dict]) -> None:
    """Settle the entry type of an unsettled question; add its opt-out answer."""
    from src.engines.questionnaire.note_rules import _limits  # circular at import time

    text = " ".join(notes)
    if not text:
        return
    unsettled, before = _unsettled(q), q.type
    opted = _OPT_OUT.search(text)
    if unsettled or q.type is QuestionType.NUMERIC or (opted and not q.options):
        if _NUMBER.search(text) and q.type in (QuestionType.INFO, QuestionType.OPEN_TEXT):
            q.type = QuestionType.NUMERIC
        elif unsettled and _TEXT.search(text):
            q.type = QuestionType.OPEN_TEXT
    if unsettled and q.options:
        if _MANY.search(text):
            q.type = QuestionType.MULTI
        elif _ONE.search(text):
            q.type = QuestionType.SINGLE
    if opted and q.type is QuestionType.NUMERIC:
        label = opted.group(1).strip()
        # The answer may already be listed, with its own note after the label.
        if not any(o.label.strip().lower().startswith(label.lower()) for o in q.options or []):
            codes = [o.code for o in q.options or []]
            code = 99 if 99 not in codes else max(codes) + 1
            stated = re.search(rf"\b(\d{{1,3}})\s*[.:)=-]?\s*{re.escape(label)}", text, re.I)
            code = int(stated.group(1)) if stated else code
            q.options = [*(q.options or []), Option(code=code, label=label, exclusive=True)]
            made.append({"qid": q.qid, "set": f"opt-out {code}", "said": opted.group(0)})
    if (m := _MIN_LEN.search(text)) and q.type is QuestionType.OPEN_TEXT:
        q.validation = {**(q.validation or {}), "min_length": int(m.group(1) or m.group(2))}
        made.append({"qid": q.qid, "set": "min_length", "said": m.group(0)})
    if _RANK.search(text) and q.type is QuestionType.RANK and q.options:
        q.validation = {**(q.validation or {}), "rank_top": 3}
        made.append({"qid": q.qid, "set": "rank_top 3", "said": "Rank 1,2,3"})
    if q.type is not before:
        made.append({"qid": q.qid, "set": f"type {q.type.value}", "said": text[:80]})
        _limits(q, notes, made)  # range and screen-out, now the type is settled
        if unsettled and (q.type not in (QuestionType.SINGLE, QuestionType.MULTI) or q.options):
            q.required = True  # an unread question defaulted to optional
            # The unread stand-in holds the raw source, notes included: a respondent
            # must never read those.
            if words := wording_lines(q):
                q.text = _strip_label(" ".join(words))
            q.validation.pop("extraction_error")
            q.flags = [f for f in q.flags if not f.startswith("EXTRACTION_")]
            if "could not be fully read" in str((q.validation.get("resolution") or {}).get("summary")):  # noqa: E501
                q.validation.pop("resolution")
            q.extraction_evidence["type"] = f"the note: {text[:80]}"

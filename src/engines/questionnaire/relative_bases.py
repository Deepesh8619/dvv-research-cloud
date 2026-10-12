"""Bases that point at "the question above" instead of naming it.

    [BASE: ANSWERED YES ABOVE] Of the [INSERT Q2 RESPONSE] MDD patients ...
    [BASE: ANSWERED NO ABOVE] For the remainder of this survey, TRD is ...

Unnumbered documents cannot name a question, so they point. The parsers read
"[BASE: ...]" as a label and moved on, so both bases were dropped without a
trace and everyone was asked both. A scripter reads "ANSWERED YES ABOVE" as
"the nearest question above that offers Yes, answered Yes" — and so does this.

A base that points and cannot be resolved is never dropped: the question gets
AMBIGUOUS_ROUTING (which blocks approval) with the document's own words kept.
"""

from __future__ import annotations

import re

from src.core.survey_definition import Expression, SurveyDefinition
from src.engines.questionnaire import schema

_POINTING = re.compile(
    r"\[\s*(?:BASE\s*[:\-]|ASK\s+IF|SHOW\s+IF)\s*"
    r"(?:(?:THOSE\s+WHO\s+)?(?:ANSWERED|SAID|SELECTED|CHOSE)\s+)?"
    r"[\"“']?(?P<answer>[A-Z0-9][\w' /-]{0,40}?)[\"”']?\s+"
    r"(?:ABOVE|AT\s+(?:THE\s+)?PREVIOUS(?:\s+QUESTION)?|PREVIOUSLY)\s*\]",
    re.I,
)


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w]", " ", (text or "").lower()).split())


def resolve_relative_bases(survey: SurveyDefinition) -> list[str]:
    """Apply every "ANSWERED X ABOVE" base; returns the qids it resolved."""
    resolved: list[str] = []
    questions = survey.questions
    for i, q in enumerate(questions):
        if q.show_if is not None:
            continue
        lines = (q.source_text or "").strip().splitlines()
        match = _POINTING.search(lines[0]) if lines else None
        if not match:
            continue
        wanted = _norm(match.group("answer"))
        source = next(
            (
                (prev, option)
                for prev in reversed(questions[:i])
                for option in prev.options or []
                if _norm(option.label) == wanted
            ),
            None,
        )
        verbatim = match.group(0)
        if source is None:
            q.validation = {**(q.validation or {}), "routing_verbatim": verbatim}
            if schema.FLAG_AMBIGUOUS_ROUTING not in q.flags:
                q.flags.append(schema.FLAG_AMBIGUOUS_ROUTING)
            continue
        prev, option = source
        q.show_if = Expression(op="eq", qid=prev.qid, value=option.code, verbatim_text=verbatim)
        q.extraction_evidence["show_if"] = (
            f"{verbatim} → {prev.qid} = {option.code} ({option.label})"
        )
        resolved.append(q.qid)
    return resolved

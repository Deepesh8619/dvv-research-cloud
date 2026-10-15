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


#: "[BASE SELECTS 1-4 IN PREVIOUS QUESTION]", "[ASK IF CODES 1, 2 ABOVE]": the
#: codes of the question above, not an answer label.
_POINTING_CODES = re.compile(
    r"\[\s*(?:BASE\s*[:\-]?|ASK\s+IF|SHOW\s+IF)\s*(?:THOSE\s+WHO\s+)?"
    r"(?:SELECTS?|SELECTED|ANSWERED|CHOSE|CODES?|PUNCH(?:ES)?)?\s*"
    r"(?P<codes>\d{1,3}(?:\s*(?:-|–|,|OR|AND|TO)\s*\d{1,3})*)\s+"
    r"(?:(?:IN|AT|ON|TO)\s+)?(?:THE\s+)?(?:PREVIOUS(?:\s+QUESTION)?|PRIOR\s+QUESTION|ABOVE)\s*\]",
    re.I,
)


def _codes(text: str) -> list[int]:
    out: list[int] = []
    for part in re.split(r"\s*(?:,|OR|AND)\s*", text, flags=re.I):
        low, _, high = re.sub(r"\s*(?:–|TO)\s*", "-", part, flags=re.I).partition("-")
        out += range(int(low), int(high or low) + 1)
    return out


def _resolve_codes(q, earlier, match) -> bool:
    """The nearest question above that offers every code named."""
    wanted = _codes(match.group("codes"))
    for prev in reversed(earlier):
        offered = {o.code for o in prev.options or []}
        if prev.type.value == "info" or not offered:
            continue
        if not set(wanted) <= offered:
            return False  # the question above does not have these codes: a person decides
        q.show_if = Expression(op="in", qid=prev.qid, values=wanted,
                               verbatim_text=match.group(0))
        q.extraction_evidence["show_if"] = (
            f"{match.group(0)} → {prev.qid} in {wanted}"
        )
        return True
    return False


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
        coded = _POINTING_CODES.search(lines[0]) if lines else None
        if coded:
            if _resolve_codes(q, questions[:i], coded):
                resolved.append(q.qid)
            else:
                q.validation = {**(q.validation or {}), "routing_verbatim": coded.group(0)}
                if schema.FLAG_AMBIGUOUS_ROUTING not in q.flags:
                    q.flags.append(schema.FLAG_AMBIGUOUS_ROUTING)
            continue
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


# "[ASK IF MULTIPLE AT S6 SELECTED]": two or more answers ticked at S6.
_MULTIPLE_AT = re.compile(
    r"\bMULTIPLE\s+(?:ANSWERS\s+|RESPONSES\s+)?(?:SELECTED\s+)?(?:AT|IN)\s+"
    r"(?P<qid>[A-Za-z][A-Za-z0-9_]*\d[A-Za-z0-9_]*)", re.I,
)
# "Ask if GA_GOOGLE_TOOL = 3-5 for at least one option": any row of a grid.
_ANY_ROW = re.compile(
    r"(?P<qid>[A-Za-z][A-Za-z0-9_]*)\s*=\s*(?P<codes>\d+(?:\s*[-–]\s*\d+)?(?:\s*(?:,|or)\s*\d+)*)"
    r"\s+for\s+at\s+least\s+one\s+(?:option|row|item|statement|brand)", re.I,
)
_MAX_PAIRS = 120
_ONLY_THOSE_ROWS = re.compile(
    r"\bonly\s+show\s+(?:the\s+)?(?:options|rows|items|statements|tools|brands)\b", re.I
)


def _codes_in(spec: str) -> list[int]:
    out: list[int] = []
    for part in re.split(r"\s*(?:,|or)\s*", spec, flags=re.I):
        low, _, high = part.replace("–", "-").partition("-")
        out += range(int(low), int(high or low) + 1)
    return out


def resolve_counted_bases(survey: SurveyDefinition) -> list[str]:
    """Bases that count answers or range over a grid's rows.

    "MULTIPLE AT S6" is any two of S6's answers together — an OR of pairs, in
    the ops the link and the data checks already evaluate. "= 3-5 for at least
    one option" is any row of that grid answered 3-5. Left in words (flagged)
    when the named question is not the shape the sentence needs.
    """
    by_upper = {q.qid.upper(): q for q in survey.questions}
    resolved = []
    for q in survey.questions:
        if q.show_if is not None:
            continue
        said = " ".join(filter(None, [
            (q.source_text or "").split("\n", 1)[0],
            str((q.validation or {}).get("routing_verbatim") or ""),
            str((q.extraction_evidence or {}).get("base") or ""),
        ]))
        many = _MULTIPLE_AT.search(said)
        rows = _ANY_ROW.search(said)
        if many and (source := by_upper.get(many.group("qid").upper())):
            codes = [o.code for o in source.options or [] if not o.exclusive]
            pairs = [(a, b) for i, a in enumerate(codes) for b in codes[i + 1:]]
            if len(pairs) < 1 or len(pairs) > _MAX_PAIRS:
                continue
            q.show_if = Expression(op="or", verbatim_text=many.group(0), children=[
                Expression(op="and", children=[
                    Expression(op="selected", qid=source.qid, value=a),
                    Expression(op="selected", qid=source.qid, value=b)])
                for a, b in pairs])
        elif rows and (source := by_upper.get(rows.group("qid").upper())) and source.rows:
            codes = _codes_in(rows.group("codes"))
            q.show_if = Expression(op="or", verbatim_text=rows.group(0), children=[
                Expression(op="in", qid=source.qid, row=r.code, values=codes)
                for r in source.rows])
            if _ONLY_THOSE_ROWS.search(said) and q.rows:
                # "Only show options familiar with from GA_GOOGLE_TOOL": each row
                # of this grid only where the SAME row there was answered so.
                theirs = {_norm(r.label): r.code for r in source.rows}
                for row in q.rows:
                    code = theirs.get(_norm(row.label))
                    if code is not None and row.show_if is None:
                        row.show_if = Expression(op="in", qid=source.qid, row=code,
                                                 values=codes, verbatim_text=rows.group(0))
        else:
            continue
        q.flags = [f for f in q.flags if f != schema.FLAG_AMBIGUOUS_ROUTING]
        q.extraction_evidence["show_if"] = q.show_if.verbatim_text or ""
        resolved.append(q.qid)
    return resolved

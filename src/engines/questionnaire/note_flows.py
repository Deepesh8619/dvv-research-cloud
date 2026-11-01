"""Refuse, warn, ask again: a note that adds a second ask is made a real question.

"If respondent selects ‘X’, show …: MESSAGE … If respondent selects ‘X’ a second
time, terminate." adds `<qid>_REASK` right after the question: same type, answers
and range, shown only if X was answered, its text starting with MESSAGE, and a
screen-out on X again. No new runtime: the second ask is an ordinary question.
Anything less exact is left alone (the ledger reports it as `asked`).
"""

from __future__ import annotations

import copy
import re

from src.core.survey_definition import Expression, Question, SurveyDefinition
from src.engines.questionnaire.note_lines import note_lines

_QUOTE = r"[‘'“\"]([^’'”\"]{2,60})[’'”\"]"
_RESPONDENT = r"\bif\s+(?:the\s+)?respondents?\s+selects?\s+"
_FLOW = re.compile(
    rf"{_RESPONDENT}{_QUOTE}\s*,?\s*show\b[^:]*:\s*(?P<msg>.+?)\s*"
    rf"{_RESPONDENT}{_QUOTE}\s*,?\s*(?:a\s+)?second\s+time\s*,?\s*terminat",
    re.I)
_SOFT = re.compile(r"\bshow\s+(?:a\s+)?warning\b[^:]*:\s*(?P<msg>.+)", re.I)
_ALLOW = re.compile(r"[,;.]?\s*\b(?:but\s+)?allow\w*\b[^.]*\bcontinue\b.*$", re.I)
SUFFIX = "_REASK"


def _label(text: str) -> str:
    return text.strip(" ,.;").lower()


def _without(expr: Expression | None, qid: str, code: int) -> Expression | None:
    """`expr` with a test for `code` on `qid` taken out (an opt-out must not end
    the first ask); None if nothing is left."""
    if expr is None:
        return None
    if expr.op == "or":
        kept = [c for c in (_without(c, qid, code) for c in expr.children or []) if c]
        return kept[0] if len(kept) == 1 else Expression(op="or", children=kept) if kept else None
    if expr.qid != qid:
        return expr
    if expr.op == "in":
        left = [v for v in expr.values or [] if v != code]
        return Expression.from_dict({**expr.to_dict(), "values": left}) if left else None
    return None if expr.op == "eq" and expr.value == code else expr


def _retarget(expr: Expression, old: str, new: str) -> Expression:
    out = Expression.from_dict(expr.to_dict())

    def walk(e: Expression) -> None:
        if e.qid == old:
            e.qid = new
        for c in e.children or []:
            walk(c)

    walk(out)
    return out


def _reask(survey: SurveyDefinition, q: Question, text: str, made: list[dict]) -> None:
    m = _FLOW.search(text)
    if not m or _label(m.group(1)) != _label(m.group(3)) or not q.options:
        return
    said = _label(m.group(1))
    opt = next((o for o in q.options if o.label.strip().lower().startswith(said)), None)
    qid = q.qid + SUFFIX
    if opt is None or any(x.qid == qid for x in survey.questions):
        return
    q.terminate_if = _without(q.terminate_if, q.qid, opt.code)
    ends = [Expression(op="in", qid=qid, values=[opt.code])]
    if q.terminate_if is not None:
        ends.insert(0, _retarget(q.terminate_if, q.qid, qid))
    second = Question(
        qid=qid, text=f"{m.group('msg').strip()} {q.text}", type=q.type,
        options=[copy.deepcopy(o) for o in q.options], required=q.required,
        show_if=Expression(op="in", qid=q.qid, values=[opt.code]),
        terminate_if=ends[0] if len(ends) == 1 else Expression(op="or", children=ends),
        validation=copy.deepcopy(q.validation), extraction_method="note_flow",
        extraction_evidence={"flow": m.group(0)})
    second.terminate_if.verbatim_text = m.group(0)
    survey.questions.insert(survey.questions.index(q) + 1, second)
    survey._rebuild_index()  # the compiler looks questions up here; unindexed means not in the link
    for block in survey.blocks:
        if q.qid in block.question_ids:
            block.question_ids.insert(block.question_ids.index(q.qid) + 1, qid)
    # Later reads of q.qid (hidden variable bands, bases) must see the second
    # answer where there is one; the readers of those use this record.
    flows = survey.metadata.setdefault("note_flows", {}).setdefault("reask", [])
    flows.append({"qid": q.qid, "reask": qid, "opt_out": opt.code, "replaces": q.qid})
    made.append({"qid": qid, "set": "reask question", "said": m.group(0), "how": "note_flow"})


def apply(survey: SurveyDefinition, made: list[dict]) -> None:
    for q in list(survey.questions):
        if q.qid.endswith(SUFFIX):
            continue
        text = " ".join(note_lines(q))
        _reask(survey, q, text, made)
        if (m := _SOFT.search(text)) and re.search(r"\ballow\w*\b[^.]*\bcontinue\b", text, re.I):
            warning = _ALLOW.sub("", m.group("msg")).strip(" ,.;")
            if warning and "soft_message" not in (q.validation or {}):
                q.validation = {**(q.validation or {}), "soft_message": warning}
                made.append({"qid": q.qid, "set": "soft_message", "said": m.group(0)})

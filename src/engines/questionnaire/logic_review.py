"""The logic reviewer: the whole survey, read the way a programmer reads it.

Every other check looks at one question. The rules-based reader finishes most
questions alone, and when the AI is asked it sees one question with nothing
around it. So problems that live BETWEEN questions belonged to nobody:

    Q4  "Of the [INSERT Q2 RESPONSE] MDD patients..."   Q2 has eight answers
    Q4  [BASE: ANSWERED YES ABOVE]                       above = which question?
    Q5  ...a second copy of Q4's question

A survey programmer catches these by reading the whole script and asking how
the questions connect. This does that: one model call per document over a
compact outline of every question — type, answers, who is asked, what it pipes
— asking only cross-question questions.

It is an opinion, so it is held to evidence and never acts:
  * every finding must name questions that exist and quote the outline;
    anything else is dropped and counted;
  * findings go to the QA record for a person; nothing is edited;
  * the model that gave the opinion is recorded.
"""

from __future__ import annotations

import re

from src.ai.router import BackendUnavailable, ModelOutputError, run_task
from src.core.survey_definition import Expression, Question, QuestionType, SurveyDefinition

_TEXT_LIMIT = 160
_OUTLINE_LIMIT = 40_000

REVIEW_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "qids": {"type": "array", "items": {"type": "string"}},
                    "problem": {"type": "string", "description": "What is wrong, in one sentence."},
                    "evidence": {"type": "string",
                                 "description": "A short quote from the outline that shows it."},
                    "severity": {"type": "string", "enum": ["error", "warning"]},
                    "fix": {"type": "string", "description": "What a programmer would do."},
                },
                "required": ["qids", "problem", "evidence", "severity"],
            },
        },
    },
    "required": ["findings"],
}


def _label(by_qid: dict[str, Question], qid: str | None, code) -> str:
    q = by_qid.get(qid or "")
    if q is None:
        return ""
    option = next((o for o in [*(q.options or []), *(q.cols or [])] if o.code == code), None)
    return f' "{option.label[:30]}"' if option else ""


def _cond(expr: Expression | None, by_qid: dict[str, Question]) -> str:
    if expr is None:
        return ""
    if expr.op in ("and", "or"):
        joined = f" {expr.op.upper()} ".join(_cond(c, by_qid) for c in expr.children or [])
        return f"({joined})"
    if expr.op == "not":
        return "NOT " + _cond((expr.children or [None])[0], by_qid)
    target = expr.qid + (f".row{expr.row}" if expr.row is not None else "")
    if expr.op == "in":
        return f"{target} in {expr.values}"
    if expr.op == "selected":
        return f"{target} selected {expr.value}{_label(by_qid, expr.qid, expr.value)}"
    return f"{target} {expr.op} {expr.value}{_label(by_qid, expr.qid, expr.value)}"


def _kind(q: Question) -> str:
    if q.type is QuestionType.NUMERIC and q.rows:
        total = (q.validation or {}).get("sum") or (q.validation or {}).get("sum_min")
        return f"numbers x{len(q.rows)} rows" + (f", must total {total}" if total else "")
    if q.type is QuestionType.GRID_SINGLE:
        return f"grid {len(q.rows or [])} rows x {len(q.cols or [])} scale points"
    if q.type is QuestionType.NUMERIC:
        v = q.validation or {}
        return "number" + (f" {v.get('min')}-{v.get('max')}" if "min" in v and "max" in v else "")
    if q.options:
        return f"{q.type.value} {len(q.options)} answers"
    return q.type.value


def outline(survey: SurveyDefinition) -> str:
    """One line per question: everything that connects it to the others."""
    by_qid = {q.qid: q for q in survey.questions}
    lines = []
    for q in survey.questions:
        text = " ".join((q.text or "").split())[:_TEXT_LIMIT]
        parts = [f"{q.qid} [{_kind(q)}] \"{text}\""]
        if q.options and len(q.options) <= 12:
            parts.append("answers: " + "; ".join(f"{o.code}={o.label[:28]}" for o in q.options))
        if q.show_if is not None:
            parts.append("asked if: " + _cond(q.show_if, by_qid))
        elif (q.validation or {}).get("routing_verbatim"):
            parts.append("routing stated but not applied: " + q.validation["routing_verbatim"])
        if q.terminate_if is not None:
            parts.append("screens out if: " + _cond(q.terminate_if, by_qid))
        techniques = (q.validation or {}).get("techniques") or {}
        pipes = [p.get("source_qid") for p in techniques.get("pipes") or [] if p.get("source_qid")]
        if pipes:
            parts.append("inserts the answer of: " + ", ".join(pipes))
        masks = [m.get("source_qid") for m in techniques.get("masks") or [] if m.get("source_qid")]
        if masks:
            parts.append("answers limited by: " + ", ".join(masks))
        lines.append(" | ".join(parts))
    return "\n".join(lines)[:_OUTLINE_LIMIT]


def _prompt(survey_outline: str) -> str:
    return (
        "You are a senior survey programmer reviewing a survey script before it goes to "
        "respondents. Below is every question in order, one per line: its id, kind, wording, "
        "answers, who is asked it, what it screens out, and whose answer it inserts.\n\n"
        "Find problems that are BETWEEN questions — the kind no single question shows:\n"
        "- a question inserts another's answer, but that answer cannot read as one value "
        "(several boxes, several rows), comes AFTER it, or is never asked to the same people;\n"
        "- a condition points at a later question, a code that question does not offer, or "
        "people who were already screened out;\n"
        "- the same question asked twice, or two conditions that contradict;\n"
        "- wording that depends on an earlier answer that no condition guarantees.\n\n"
        "Rules: only report what the outline shows. Quote it in `evidence`, word for word. "
        "Name every question involved in `qids`. Do not report wording style, and do not "
        "report a question for having no condition — most are asked of everyone. "
        "If nothing is wrong, return an empty list.\n\n"
        f"=== SURVEY ===\n{survey_outline}"
    )


def _flat(text: str) -> str:
    return " ".join(re.sub(r"[^\w%]", " ", (text or "").lower()).split())


def _quoted(evidence: str, source: str) -> bool:
    """Every piece of the quote is in the outline, word for word.

    People quote with "..." and skip the middle of a line; the model does too,
    and requiring one unbroken string threw away three correct findings out of
    seven. Each fragment must still be verbatim, and one must be substantial,
    so invented evidence fails exactly as before.
    """
    fragments = [_flat(f) for f in re.split(r"\.\.\.|…|\|", evidence or "")]
    fragments = [f for f in fragments if len(f) >= 4]
    return (
        bool(fragments)
        and max(len(f) for f in fragments) >= 12
        and all(f in source for f in fragments)
    )


def review(survey: SurveyDefinition) -> dict:
    """The reviewer's findings, held to the outline. Never raises.

    A second opinion over the QA agent: anything QA already reports on the same
    question is left out, so what remains is what no rule caught. Measured on a
    planted test survey it found 7 of 7 errors, and raised 1-2 false alarms on
    the clean version — which is why its findings are shown for a person to
    judge and never flag or block a question.
    """
    survey_outline = outline(survey)
    try:
        response = run_task("questionnaire.logic_review", _prompt(survey_outline),
                            REVIEW_SCHEMA, timeout=300)
    except (BackendUnavailable, ModelOutputError) as exc:
        return {"unavailable": str(exc)[:200], "findings": []}
    known = {q.qid.upper(): q.qid for q in survey.questions}
    source = _flat(survey_outline)
    kept, dropped, dropped_examples = [], 0, []
    for item in (response.content or {}).get("findings") or []:
        if not isinstance(item, dict):
            dropped += 1
            continue
        qids = [known[q.upper()] for q in item.get("qids") or [] if isinstance(q, str)
                and q.upper() in known]
        if not qids or not _quoted(item.get("evidence", ""), source):
            dropped += 1
            # Kept for whoever tunes this: an opinion thrown away should be
            # inspectable, or a too-strict rule looks like a blind reviewer.
            dropped_examples.append({k: item.get(k) for k in ("qids", "problem", "evidence")})
            continue
        kept.append({
            "qids": qids,
            "problem": str(item.get("problem", "")).strip(),
            "evidence": str(item.get("evidence", "")).strip(),
            "severity": item.get("severity") if item.get("severity") in ("error", "warning")
            else "warning",
            "fix": str(item.get("fix", "")).strip(),
        })
    already = {
        (e.get("qid") or "").upper()
        for e in ((survey.metadata or {}).get("qa") or {}).get("entries", [])
        if e.get("status") == "needs_you"
    }
    fresh = [f for f in kept if not {q.upper() for q in f["qids"]} & already]
    return {
        "model": f"{response.backend}/{response.model}",
        "findings": fresh,
        "already_reported_by_qa": len(kept) - len(fresh),
        "claims_dropped_unsupported": dropped,
        "dropped_examples": dropped_examples[:5],
    }

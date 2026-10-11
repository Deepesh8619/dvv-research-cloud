"""Fix one question, with an agent, in front of the analyst.

The analyst sees a question that is wrong — "this is a sum to 100, not one
answer" — and wants it fixed now, not by re-reading the whole document. Two
agents can offer a fix:

* the programmer agent's repairs (re-read with today's parsers, read a
  sum-to-N table, lift scripter notes, clean separators) — no model, the same
  answer every time;
* a model following the analyst's own words, for what no repair covers.

Every suggestion comes back with the before/after and what the QA agent finds
on each, so the analyst chooses with evidence. Nothing is saved here: a chosen
suggestion lands in the editor, where it can still be rewritten, and the
analyst saves it. A model may not invent answers — any label that is not in
the document, the question as it stands, or the analyst's words is dropped
and reported.
"""

from __future__ import annotations

import copy
import re
from dataclasses import fields as dataclass_fields

from src.ai.known_failures import block_for
from src.ai.router import BackendUnavailable, ModelOutputError, run_task
from src.core.survey_definition import Option, Question, QuestionType
from src.engines.questionnaire.diffing import _diff_question
from src.engines.questionnaire.qa_loop import REPAIRS, qa_check

_OPTION_FIELDS = {f.name for f in dataclass_fields(Option)}
_TYPES = [t.value for t in QuestionType if t is not QuestionType.MAXDIFF]
_ITEM = {
    "type": "object",
    "properties": {
        "code": {"type": "integer"},
        "label": {"type": "string"},
        "exclusive": {"type": "boolean"},
        "anchored": {"type": "boolean"},
        "open_other": {"type": "boolean"},
    },
    "required": ["code", "label"],
}
REVISE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": _TYPES},
        "text": {"type": "string"},
        "options": {"type": "array", "items": _ITEM},
        "rows": {"type": "array", "items": _ITEM},
        "cols": {"type": "array", "items": _ITEM},
        "validation": {
            "type": "object",
            "description": "min, max, sum (must equal), sum_min (at least), min_select, max_select",
        },
        "explanation": {"type": "string", "description": "One sentence: what you changed."},
    },
    "required": ["type", "explanation"],
}


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w%]", " ", (text or "").lower()).split())


def _as_dict(q: Question) -> dict:
    return q.to_dict()


def _issues(q: Question, by_qid: dict[str, Question]) -> list[str]:
    return [f"{i.detail} ({i.evidence})" if i.evidence else i.detail
            for i in qa_check(q, {**by_qid, q.qid: q})]


def _suggestion(source: str, explanation: str, before: Question, after: Question,
                by_qid: dict[str, Question], dropped: list[str] | None = None) -> dict:
    return {
        "source": source,
        "explanation": explanation,
        "question": _as_dict(after),
        "change": _diff_question(_as_dict(before), _as_dict(after)).to_dict(),
        "qa_before": _issues(before, by_qid),
        "qa_after": _issues(after, by_qid),
        "dropped": dropped or [],
    }


def suggest_repairs(q: Question, by_qid: dict[str, Question]) -> list[dict]:
    """Every programmer-agent repair that changes this question and leaves QA
    no worse — each one shown on its own, so the analyst picks."""
    out = []
    before_issues = len(qa_check(q, by_qid))
    for repair in REPAIRS:
        trial = copy.deepcopy(q)
        did = repair(trial)
        if not did or _as_dict(trial) == _as_dict(q):
            continue
        if len(qa_check(trial, {**by_qid, q.qid: trial})) > before_issues:
            continue
        out.append(_suggestion("programmer agent", did, q, trial, by_qid))
    # Best first: the fix that leaves QA the least to say.
    return sorted(out, key=lambda s: len(s["qa_after"]))


def _grounded(items: list[dict], allowed: str) -> tuple[list[Option], list[str]]:
    kept, dropped = [], []
    for item in items or []:
        label = str(item.get("label", "")).strip()
        if not label:
            continue
        if _norm(label) and _norm(label) in allowed:
            data = {k: v for k, v in item.items() if k in _OPTION_FIELDS}
            kept.append(Option.from_dict({**data, "code": int(item.get("code", len(kept) + 1)),
                                          "label": label}))
        else:
            dropped.append(label)
    return kept, dropped


def _prompt(q: Question, instruction: str) -> str:
    current = {k: v for k, v in _as_dict(q).items()
               if k in ("qid", "text", "type", "options", "rows", "cols", "validation")}
    return (
        "You are a senior survey programmer fixing ONE question for an analyst.\n\n"
        f"The analyst says: {instruction}\n\n"
        f"The question as it stands now:\n{current}\n\n"
        f"The questionnaire's own text for it:\n{q.source_text or '(not available)'}\n\n"
        "Return the corrected question. Rules:\n"
        "- Change what the analyst asks, and only what that change requires.\n"
        "- Every option, row and scale label must come from the questionnaire text, the "
        "question as it stands, or the analyst's words. Never invent one.\n"
        "- Types: single (one answer), multi (several), dropdown, rank, grid_single (each "
        "row rated on one scale: rows + cols), numeric (a number; with rows = one box per "
        "item, and validation.sum when they must add up — validation.sum_min for 'at "
        "least'), open_text, info (display only).\n"
        "- Say in `explanation`, in one plain sentence, what you changed.\n"
        + block_for("extract")
    )


def revise(q: Question, instruction: str, by_qid: dict[str, Question]) -> dict:
    """A model's revision of the question, following the analyst's words."""
    try:
        response = run_task("questionnaire.revise", _prompt(q, instruction), REVISE_SCHEMA)
    except (BackendUnavailable, ModelOutputError):
        return {"error": (
            "No AI model could be reached just now, so the agent could not follow "
            "your words. Any suggestions shown come from the programmer agent, which "
            "needs no AI. Try again in a minute."
        )}
    raw = response.content if isinstance(response.content, dict) else {}
    try:
        new_type = QuestionType(raw.get("type"))
    except ValueError:
        return {"error": f"The agent answered with a type that does not exist: {raw.get('type')}"}

    allowed = _norm(" ".join([
        q.source_text or "", q.text or "", instruction,
        *(o.label for o in [*(q.options or []), *(q.rows or []), *(q.cols or [])]),
    ]))
    after = copy.deepcopy(q)
    after.type = new_type
    if raw.get("text"):
        after.text = str(raw["text"]).strip()
    dropped: list[str] = []
    for name in ("options", "rows", "cols"):
        if name in raw:
            kept, lost = _grounded(raw.get(name) or [], allowed)
            setattr(after, name, kept or ([] if name == "options" else None))
            dropped += lost
    if isinstance(raw.get("validation"), dict):
        after.validation = {**(q.validation or {}), **raw["validation"]}
    explanation = str(raw.get("explanation") or "Revised as asked.").strip()
    return _suggestion(f"agent ({response.backend}/{response.model})", explanation,
                       q, after, by_qid, dropped)

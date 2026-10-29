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
from src.engines.questionnaire import schema
from src.engines.questionnaire.diffing import _diff_question
from src.engines.questionnaire.qa_loop import REPAIRS, qa_check

_OPTION_FIELDS = {f.name for f in dataclass_fields(Option)}
# The analyst asked for fewer answers: only then may a list shrink.
_REMOVING = re.compile(
    r"\b(remove|delete|drop|take out|get rid of|fewer|only keep|keep only)\b", re.I
)
_TYPES = [t.value for t in QuestionType if t is not QuestionType.MAXDIFF]
# Kinds a model may recognise that are not types of their own here. Squeezing
# them into the nearest type silently is how a bipolar grid became a grid of
# "0=Agree fully with" and "Theme"; naming them keeps the real kind on record.
#   name -> (stand-in type, or None to keep the current one; runs as is?)
_INTENDED_TYPES: dict[str, tuple[QuestionType | None, bool]] = {
    "bipolar_grid": (QuestionType.GRID_SINGLE, True),
    "slider": (None, True),
    "rank_grid": (QuestionType.GRID_SINGLE, False),
    "other": (None, False),
}
# Kept small and explicit: optional flags on every row and an open-ended
# "validation" object are where models produce malformed structured answers
# ('anchored false': None), which providers reject outright.
_ROW = {
    "type": "object",
    "properties": {"code": {"type": "integer"}, "label": {"type": "string"}},
    "required": ["code", "label"],
}
_ITEM = {
    "type": "object",
    "properties": {
        "code": {"type": "integer"},
        "label": {"type": "string"},
        "exclusive": {"type": "boolean", "description": "Only for 'None of these'-type answers."},
    },
    "required": ["code", "label"],
}
_NUMBER = {"type": "integer"}
REVISE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": [*_TYPES, *_INTENDED_TYPES],
                 "description": "grid_multi: several answers per row; bipolar_grid: two "
                                "opposite statements per row with a scale between; rank_grid: "
                                "a rank per row; slider: a slider; other: none of these."},
        "text": {"type": "string"},
        "options": {"type": "array", "items": _ITEM},
        "rows": {"type": "array", "items": _ROW},
        "cols": {"type": "array", "items": _ROW},
        # Flat on purpose. As a nested "validation" object, the model looped on
        # invented keys ("sum_not_applicable_2": null ...) until the provider
        # rejected the answer. Each of these is a plain number or absent.
        "total": {**_NUMBER, "description": "Number boxes must add up to exactly this."},
        "total_at_least": {**_NUMBER, "description": "Boxes must add up to at least this."},
        "box_min": {**_NUMBER, "description": "Smallest number a box accepts."},
        "box_max": {**_NUMBER, "description": "Largest number a box accepts."},
        "max_answers": {**_NUMBER, "description": "Most answers a respondent may pick."},
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


def _related(q: Question, instruction: str, by_qid: dict[str, Question]) -> list[Question]:
    """Questions the analyst names ("...the categories from Q2") and the ones
    this question inserts answers from. Their answers are legitimate material:
    "put Q2's categories here" is a request, not an invention."""
    named = {m.upper() for m in re.findall(r"\b[A-Za-z]{1,4}\d+[A-Za-z]?\b", instruction)}
    pipes = ((q.validation or {}).get("techniques") or {}).get("pipes") or []
    named |= {(p.get("source_qid") or "").upper() for p in pipes}
    return [other for qid, other in by_qid.items() if qid.upper() in named and qid != q.qid]


def _describe(other: Question) -> str:
    def items(xs):
        return "; ".join(f"{o.code}={o.label}" for o in xs or [])
    parts = [f"{other.qid} [{other.type.value}] {other.text}"]
    if other.options:
        parts.append(f"answers: {items(other.options)}")
    if other.rows:
        parts.append(f"rows: {items(other.rows)}")
    if other.cols:
        parts.append(f"scale: {items(other.cols)}")
    return " | ".join(parts)


def _prompt(q: Question, instruction: str, related: list[Question],
            document: str = "") -> str:
    current = {k: v for k, v in _as_dict(q).items()
               if k in ("qid", "text", "type", "options", "rows", "cols", "validation")}
    if document:
        context = f"The questionnaire around it:\n{document}\n\n"
    elif related:
        context = (
            "Other questions the analyst refers to (you may reuse their answers):\n"
            + "\n".join(_describe(o) for o in related) + "\n\n"
        )
    else:
        context = ""
    return (
        "You are a senior survey programmer fixing ONE question for an analyst.\n\n"
        f"The analyst says: {instruction}\n\n"
        f"The question as it stands now:\n{current}\n\n"
        f"The questionnaire's own text for it:\n{q.source_text or '(not available)'}\n\n"
        + context +
        "Return the corrected question. Rules:\n"
        "- Change what the analyst asks, and only what that change requires.\n"
        "- Every option, row and scale label must come from the questionnaire text, the "
        "question as it stands, or the analyst's words. Never invent one.\n"
        "- Types: single (one answer), multi (several), dropdown, rank, grid_single (each "
        "row rated on one scale: rows + cols), numeric (a number; with rows = one box per "
        "item, and `total` when they must add up — `total_at_least` for 'at least'), "
        "open_text, info (display only).\n"
        "- Leave out any field you are not changing.\n"
        "- Say in `explanation`, in one plain sentence, what you changed.\n"
        + block_for("extract")
    )


def revise(q: Question, instruction: str, by_qid: dict[str, Question]) -> dict:
    """A model's revision of the question, following the analyst's words."""
    return revise_with_context(q, instruction, by_qid, _related(q, instruction, by_qid))


def revise_with_context(q: Question, instruction: str, by_qid: dict[str, Question],
                        related: list[Question], document: str = "",
                        task: str = "questionnaire.revise") -> dict:
    """`revise`, given the questions around it. Labels may come from any of them."""
    try:
        response = run_task(task, _prompt(q, instruction, related, document), REVISE_SCHEMA)
    except BackendUnavailable as exc:
        from src.ai.router import why_unavailable

        return {"error": (
            f"No AI model could answer just now ({why_unavailable(exc)}), so the agent "
            "could not follow your words. Any suggestions shown come from the programmer "
            "agent, which needs no AI. Try again in a minute."
        )}
    except ModelOutputError:
        return {"error": (
            "The agent answered, but not in a form that could be used. Try again, or "
            "say it in different words."
        )}
    raw = response.content if isinstance(response.content, dict) else {}
    intended = raw.get("type") if raw.get("type") in _INTENDED_TYPES else None
    if intended:
        stand_in, runs = _INTENDED_TYPES[intended]
        if intended == "slider":  # one slider per row is a grid; a single one a number
            stand_in = QuestionType.GRID_SINGLE if raw.get("rows") else QuestionType.NUMERIC
        new_type = stand_in or q.type
    else:
        try:
            new_type = QuestionType(raw.get("type"))
        except ValueError:
            return {"error": "The agent answered with a type that does not exist: "
                             f"{raw.get('type')}"}

    allowed = _norm(" ".join([
        q.source_text or "", q.text or "", instruction,
        *(o.label for o in [*(q.options or []), *(q.rows or []), *(q.cols or [])]),
        *(o.label for other in related
          for o in [*(other.options or []), *(other.rows or []), *(other.cols or [])]),
    ]))
    after = copy.deepcopy(q)
    after.type = new_type
    if raw.get("text"):
        after.text = str(raw["text"]).strip()
    dropped: list[str] = []
    notes: list[str] = []
    removing = bool(_REMOVING.search(instruction))
    for name in ("options", "rows", "cols"):
        if name in raw:
            kept, lost = _grounded(raw.get(name) or [], allowed)
            had = list(getattr(q, name) or [])
            if had and 0 < len(kept) < len(had) and not removing:
                # Asked to keep the rows, a model returned one of two and the
                # other was gone. Shorter lists only when the analyst asked.
                notes.append(f"kept all {len(had)} {name} — the agent returned only "
                             f"{len(kept)} without being asked to remove any")
                continue
            setattr(after, name, kept or ([] if name == "options" else None))
            dropped += lost
    limits = {
        key: raw[field] for field, key in (
            ("total", "sum"), ("total_at_least", "sum_min"), ("box_min", "min"),
            ("box_max", "max"), ("max_answers", "max_select"),
        )
        if isinstance(raw.get(field), int) and not isinstance(raw.get(field), bool)
    }
    if limits:
        after.validation = {**(q.validation or {}), **limits}
    if intended:
        after.validation = {**(after.validation or {}), "intended_type": intended}
        if intended == "bipolar_grid":
            after.validation.setdefault("bipolar", {"flip_half": False})
        if not runs and schema.FLAG_TYPE_NOT_SUPPORTED not in after.flags:
            after.flags = [*after.flags, schema.FLAG_TYPE_NOT_SUPPORTED]
    explanation = str(raw.get("explanation") or "Revised as asked.").strip()
    if notes:
        explanation += " (" + "; ".join(notes) + ")"
    return _suggestion(f"agent ({response.backend}/{response.model})", explanation,
                       q, after, by_qid, dropped)

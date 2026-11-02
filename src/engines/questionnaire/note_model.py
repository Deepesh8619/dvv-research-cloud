"""A model for what the readers left: clauses no pattern understood.

One batched call per document, `run_private` only (in-region or local, never a
free tier). The model never sets a survey field. Each answer that survives
strict checking becomes a question in "What a reader would ask" ("This note
seems to say: ... Apply it?"), and its ledger records become `asked`.
Anything doubtful leaves the clause `unread`. No client text is logged.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any

from src.ai.router import BackendUnavailable, ModelOutputError, run_private
from src.core.survey_definition import SurveyDefinition
from src.engines.questionnaire import correction_examples
from src.engines.questionnaire.note_ledger import CAP, KINDS, checklist, read_notes, summary

MAX_CLAUSES = 80
_KIND_NAMES = [k for k, _ in KINDS]
_CODES_SHOWN = 12
_QID_LIST_LIMIT = 12_000

SCHEMA: dict = {
    "type": "object",
    "properties": {"records": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "index": {"type": "integer"},
            "kind": {"type": "string", "enum": [*_KIND_NAMES, "none"]},
            "qids": {"type": "array", "items": {"type": "string"}},
            "codes": {"type": "array", "items": {"type": ["integer", "string"]}},
            "says": {"type": "string", "description": "What the note says, one plain line."},
        },
        "required": ["index", "kind", "qids", "codes", "says"],
    }}},
    "required": ["records"],
}


def _codes(survey: SurveyDefinition) -> dict[str, set[str]]:
    return {q.qid: {str(o.code) for o in [*(q.options or []), *(q.cols or [])]}
            for q in survey.questions}


def _prompt(clauses: list[str], survey: SurveyDefinition, examples: str = "") -> str:
    known = "\n".join(
        f"{q.qid}: " + ", ".join(sorted(c)[:_CODES_SHOWN])
        for q, c in ((q, _codes(survey)[q.qid]) for q in survey.questions))[:_QID_LIST_LIMIT]
    numbered = "\n".join(f"{i}: {c}" for i, c in enumerate(clauses))
    return (
        "You read a survey programmer's notes. Each numbered clause below is one note that "
        "no rule understood. For each clause you can place, say which kind of instruction it "
        f"is (one of: {', '.join(_KIND_NAMES)}; or none), which questions it names, which "
        "answer codes it names, and what it says in one plain line. Use only question ids "
        "and codes from the list. Skip a clause you are unsure of.\n\n"
        f"=== QUESTIONS (id: codes) ===\n{known}\n\n=== CLAUSES ===\n{numbered}"
        + (f"\n\n{examples}" if examples else ""))


def _valid(item: Any, n: int, known: dict[str, set[str]]) -> dict | None:
    if not isinstance(item, dict):
        return None
    i, kind, qids, codes, says = (item.get(k) for k in ("index", "kind", "qids", "codes", "says"))
    if (not isinstance(i, int) or isinstance(i, bool) or not 0 <= i < n
            or kind not in _KIND_NAMES  # "none" is an answer, not a suggestion
            or not isinstance(qids, list) or not isinstance(codes, list)
            or not isinstance(says, str) or not says.strip()):
        return None
    if not all(isinstance(q, str) and q in known for q in qids):
        return None
    if not all(isinstance(c, (int, str)) and not isinstance(c, bool) for c in codes):
        return None
    if codes and not qids:
        return None
    if any(not any(str(c) in known[q] for q in qids) for c in codes):
        return None
    return {"index": i, "kind": kind, "qids": qids, "codes": codes,
            "says": " ".join(says.split())[:240]}


def _examples(clauses: list[str], rows: list[dict], study_id: str) -> list[dict]:
    """The most similar settled cases across the batch: top 2 per clause, at most 8."""
    chosen: list[dict] = []
    for c in clauses:
        for r in correction_examples.similar(c, rows, 2, study_id):
            if not any(r is x for x in chosen):
                chosen.append(r)
    return correction_examples.fit(chosen[:8])


def apply(survey: SurveyDefinition,
          rows: list[dict] | Callable[[], list[dict]] | None = None) -> None:
    """Ask the model about unread clauses. No backend or no valid answer: no change.

    `rows` (or a function returning them, called only if a clause is unread) are settled
    cases from this deployment, shown as advice. They never change what is accepted."""
    records = read_notes(survey)
    clauses = list(dict.fromkeys(r["said"] for r in records if r["outcome"] == "unread"))
    clauses = clauses[:MAX_CLAUSES]
    if not clauses:
        return
    shown: list[dict] = []
    if rows is not None:
        shown = _examples(clauses, rows() if callable(rows) else rows, survey.study_id)
        used = survey.metadata.setdefault("examples_used", {"note_model": 0, "questions": 0})
        used["note_model"] = len(shown)
    try:
        response = run_private(
            _prompt(clauses, survey, correction_examples.prompt_block(shown)), SCHEMA,
            timeout=300)
    except (BackendUnavailable, ModelOutputError):
        return
    items = response.content.get("records") if isinstance(response.content, dict) else None
    if not isinstance(items, list):
        return
    known, kept = _codes(survey), {}
    for item in items:
        if (v := _valid(item, len(clauses), known)) and v["index"] not in kept:
            kept[v["index"]] = v
    if not kept:
        return
    suggestions, asks = [], []
    for r in records:
        if r["outcome"] != "unread" or r["said"] not in clauses:
            continue
        if (v := kept.get(clauses.index(r["said"]))) is None:
            continue
        r["outcome"], r["detail"] = "asked", "model suggestion"
        key = hashlib.sha1(f"note_model|{r['qid']}|{r['said']}".encode()).hexdigest()[:12]
        if any(a["key"] == key for a in asks):
            continue
        suggestions.append({"key": key, "qid": r["qid"], "said": r["said"], "kind": v["kind"],
                            "qids": v["qids"], "codes": v["codes"], "says": v["says"]})
        asks.append({"qid": r["qid"], "kind": "note_model", "key": key, "how": "model",
                     "ask": f"This note seems to say: {v['says']} Apply it?",
                     "options": ["Yes", "No, leave it for the scripter"]})
    survey.metadata["note_model"] = suggestions
    survey.metadata["note_ledger"] = {"summary": summary(records), "records": records[:CAP],
                                      "checks": checklist(records)}
    analyst = survey.metadata.setdefault("decoding", {}).setdefault("analyst", {})
    analyst["questions"] = [*analyst.get("questions", []), *asks]

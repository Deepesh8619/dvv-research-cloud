"""What an answer to "What a reader would ask" does to the survey.

Only three answers change anything, and only on the survey passed in:

- "Keep both" / "Only the first" on a two-lists question creates those hidden
  variables (a variable is created the way the Copilot's add-hidden-variable card
  does: type info, not asked, one derive_if per band);
- "Leave for the scripter" marks the matching ledger records `scripter`.

Every other answer stays stored and is reported as not applied. A decision whose
question or variable is gone is reported `stale` and applied to nothing else.
Running it again changes nothing: a variable it made is recognised by its
`extraction_method`.
"""

from __future__ import annotations

from typing import Any

from src.core.survey_definition import (
    Block,
    Expression,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
)
from src.engines.questionnaire.note_ledger import checklist

METHOD = "reading_decision"
LIVE_SENTENCE = ("This survey is live, so your answer is saved but the survey is not changed. "
                 "Close the link first, then answer again or read the document again.")


def mark_hidden(question: Question) -> None:
    """A variable the script computes and never asks (shared with the add-question route)."""
    question.type = QuestionType.INFO
    question.flags = [*question.flags, "DERIVED_VARIABLE"]
    question.validation = {**(question.validation or {}), "not_in_link": True}


def hidden_name(name: str) -> str:
    name = name.strip().upper()
    return name if name.startswith(("HID_", "HIDDEN_")) else f"HID_{name}"


def _create(survey: SurveyDefinition, var: dict[str, Any]) -> tuple[str, str]:
    qid = hidden_name(var["name"])
    have = next((q for q in survey.questions if q.qid.upper() == qid), None)
    if have is not None:
        if have.extraction_method == METHOD:
            return "already", ""
        return "not_applied", f"{qid} already exists and was not made from this answer"
    if var.get("status") != "ready":
        return "not_applied", f"{var['name']} is not ready: {var.get('why') or 'no exact rule'}"
    qids = {q.qid for q in survey.questions}
    options = []
    for i, band in enumerate(var["bands"], 1):
        when = Expression.from_dict(band["condition"])
        if not when.referenced_qids() <= qids:
            return "stale", f"{var['name']} reads a question that is no longer in the survey"
        options.append(Option(code=int(band.get("code") or i), label=band["label"],
                              derive_if=when))
    question = Question(qid=qid, text=qid, type=QuestionType.INFO, options=options,
                        required=False, extraction_method=METHOD)
    mark_hidden(question)
    before = survey.validate()
    survey.questions.append(question)
    if not survey.blocks:
        survey.blocks.append(Block(block_id="B1", question_ids=[]))
    survey.blocks[-1].question_ids.append(qid)
    survey._rebuild_index()
    if new := [p for p in survey.validate() if p not in before]:
        survey.questions.pop()
        survey.blocks[-1].question_ids.pop()
        survey._rebuild_index()
        return "not_applied", f"{qid} would break the survey: {new[0]}"
    return "applied", ""


def _scripter(survey: SurveyDefinition, ask: dict) -> tuple[str, str]:
    ledger = survey.metadata.get("note_ledger") or {}
    mine = [r for r in ledger.get("records") or []
            if r["qid"] == ask["qid"] and r["kind"] == "hidden_var"]
    todo = [r for r in mine if r["outcome"] != "scripter"]
    if not mine:
        return "not_applied", "no note on that question is in the ledger"
    if not todo:
        return "already", ""
    for r in todo:
        for bucket in (ledger["summary"]["by_outcome"],
                       ledger["summary"]["by_kind"].setdefault(r["kind"], {})):
            bucket[r["outcome"]] = max(bucket.get(r["outcome"], 0) - 1, 0)
            bucket["scripter"] = bucket.get("scripter", 0) + 1
        r["outcome"] = "scripter"
    ledger["checks"] = checklist(ledger["records"])
    return "applied", ""


def apply_decisions(survey: SurveyDefinition, decisions: dict[str, dict]) -> list[dict]:
    """Apply every stored answer that can be applied; report all of them."""
    reading = (survey.metadata.get("decoding") or {}).get("analyst") or {}
    asks = {a.get("key"): a for a in reading.get("questions") or []}
    variables = {v["name"]: v for v in reading.get("variables") or []}
    out: list[dict] = []
    for key, decision in decisions.items():
        answer = str(decision.get("answer") or "")
        said = answer.strip().lower()
        ask, made = asks.get(key), []
        if ask is None:
            status, why = "stale", "that question is no longer asked of this document"
        elif said == "leave for the scripter":
            status, why = _scripter(survey, ask)
        elif ask["kind"] == "two_lists" and said in ("keep both", "only the first"):
            first = variables.get((ask.get("variables") or [""])[0])
            names = [first["name"], *(first.get("also") or [])][: 2 if said == "keep both" else 1] \
                if first else []
            if not names:
                status, why = "stale", "that variable is no longer in the reading"
            else:
                results = [(n, *_create(survey, variables[n])) if n in variables
                           else (n, "stale", f"{n} is no longer in the reading") for n in names]
                made = [hidden_name(n) for n, s, _ in results if s == "applied"]
                order = ("stale", "not_applied", "applied", "already")
                status = "applied" if made else min((s for _, s, _ in results), key=order.index)
                why = "; ".join(w for _, _, w in results if w)
        else:
            status, why = "not_applied", "this answer is kept, and changes nothing by itself"
        out.append({"key": key, "answer": answer, "status": status, "why": why, "made": made})
    return out

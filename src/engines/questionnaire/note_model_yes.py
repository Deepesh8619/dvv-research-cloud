"""A person's "Yes" to a `note_model` suggestion, applied without the model.

The model only suggested ({"key","qid","said","kind","qids","codes","says"});
nothing here asks it again. Each kind is re-applied from fields already checked
against the survey (codes that exist on the question) or from the clause's own
words through the existing condition reader. Anything else is not applied, with
a sentence saying it needs doing by hand. Applying twice changes nothing.
"""

from __future__ import annotations

import re

from src.core.survey_definition import Expression, SurveyDefinition
from src.engines.questionnaire.note_groups import with_reask
from src.engines.questionnaire.note_ledger import _NEGATED, checklist

_IF = re.compile(r"\bif\s+(.+)$", re.I)
_TAIL = re.compile(r",?\s*(?:then\s+)?(?:terminate|screen[- ]?out|close|end)\b.*$", re.I)
_SETTING = {"exclusive": "exclusive", "anchor": "anchored"}


def move_record(ledger: dict, record: dict, outcome: str, kind: str | None = None) -> None:
    """Change one ledger record's outcome (and kind), keeping the counts true."""
    summary = ledger["summary"]
    old_kind, new_kind = record["kind"], kind or record["kind"]
    summary["by_outcome"][record["outcome"]] = max(
        summary["by_outcome"].get(record["outcome"], 0) - 1, 0)
    summary["by_outcome"][outcome] = summary["by_outcome"].get(outcome, 0) + 1
    old = summary["by_kind"].setdefault(old_kind, {})
    old[record["outcome"]] = max(old.get(record["outcome"], 0) - 1, 0)
    new = summary["by_kind"].setdefault(new_kind, {})
    new[outcome] = new.get(outcome, 0) + 1
    record["outcome"], record["kind"] = outcome, new_kind
    ledger["checks"] = checklist(ledger["records"])


def _by_hand(suggestion: dict, why: str) -> tuple[str, str, list[str]]:
    return "not_applied", f"{why} This needs doing by hand: {suggestion['says']}", []


def _options_set(q, suggestion: dict) -> tuple[str, str, list[str]]:
    attr = _SETTING[suggestion["kind"]]
    by_code = {str(o.code): o for o in q.options or []}
    codes = [str(c) for c in suggestion["codes"]]
    if not codes or not all(c in by_code for c in codes):
        return _by_hand(suggestion, f"The codes are not all answers of {q.qid}.")
    new = [by_code[c] for c in codes if not getattr(by_code[c], attr)]
    for o in new:
        setattr(o, attr, True)
    made = [f"{q.qid} {attr} {o.code}" for o in new]
    return ("applied", "", made) if new else ("already", "", [])


def _rule(suggestion: dict, survey: SurveyDefinition, q) -> Expression | None:
    from src.engines.questionnaire.note_rules import _compile

    for text in (suggestion["said"], suggestion["says"]):
        m = _IF.search(text)
        if m and (expr := _compile(_TAIL.sub("", m.group(1)).strip(), q.qid, survey)):
            return expr
    return None


def _condition(survey: SurveyDefinition, q, suggestion: dict) -> tuple[str, str, list[str]]:
    kind, field = suggestion["kind"], "terminate_if" if suggestion["kind"] == "terminate" \
        else "show_if"
    expr = _rule(suggestion, survey, q)
    if expr is None:
        return _by_hand(suggestion, "The words do not give an exact rule.")
    before = {x.qid.upper() for x in survey.questions[: survey.questions.index(q)]}
    if kind == "terminate":
        before.add(q.qid.upper())  # a screen-out may read the question's own answer
    if not {x.upper() for x in expr.referenced_qids()} <= before:
        return _by_hand(suggestion, "The rule names a question that does not come earlier.")
    expr = with_reask(expr, survey, frozenset({q.qid}))
    expr.verbatim_text = expr.verbatim_text or suggestion["said"]
    have = getattr(q, field)
    if have is not None:
        if have.to_dict() == expr.to_dict():
            return "already", "", []
        return _by_hand(suggestion, f"{q.qid} already has a rule; it was not overwritten.")
    problems = survey.validate()
    setattr(q, field, expr)
    if new := [p for p in survey.validate() if p not in problems]:
        setattr(q, field, None)
        return _by_hand(suggestion, f"It would break the survey ({new[0]}).")
    return "applied", "", [f"{q.qid} {field}"]


def apply(survey: SurveyDefinition, suggestion: dict) -> tuple[str, str, list[str]]:
    """(status, why, what was made) for one confirmed suggestion."""
    q = next((x for x in survey.questions if x.qid == suggestion["qid"]), None)
    if q is None:
        return "stale", "that question is no longer in the survey", []
    kind = suggestion["kind"]
    if kind in _SETTING:
        return _options_set(q, suggestion)
    if kind == "randomize":
        if _NEGATED.search(suggestion["said"]) or _NEGATED.search(suggestion["says"]):
            return _by_hand(suggestion, "The note says not to shuffle.")
        if not q.options:
            return _by_hand(suggestion, f"{q.qid} has no answer list to shuffle.")
        if q.randomize_options:
            return "already", "", []
        q.randomize_options = True
        return "applied", "", [f"{q.qid} randomize"]
    if kind in ("terminate", "base"):
        return _condition(survey, q, suggestion)
    return _by_hand(suggestion, "This kind of note is not applied automatically.")


def mark_ledger(survey: SurveyDefinition, suggestion: dict, outcome: str) -> bool:
    """Move the ledger records behind this suggestion; False if there are none to move."""
    ledger = survey.metadata.get("note_ledger") or {}
    mine = [r for r in ledger.get("records") or []
            if r["qid"] == suggestion["qid"] and r["said"] == suggestion["said"]]
    for r in mine:
        if r["outcome"] != outcome:
            move_record(ledger, r, outcome, suggestion["kind"] if r["kind"] == "none" else None)
    return bool(mine)

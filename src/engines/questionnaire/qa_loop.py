"""The QA loop: a QA agent tests the survey, a programmer agent fixes it,
QA re-checks — with every step written to one record everyone reads.

On a research team the script goes programmer -> QA -> programmer -> QA until
QA has nothing left: QA opens the link, walks every question against the
questionnaire, and sends back what is wrong; the programmer fixes it; QA checks
the fix. Here both roles are agents, and the loop runs on every extraction.

* The QA agent checks each question two ways. As the RESPONDENT meets it — the
  question compiled exactly as the survey link renders it: is there anything to
  answer, is a scripter's note showing on screen. And against the DOCUMENT —
  are the document's answers all there, do the codes match, does the type match
  the wording, is routing the document states actually applied.

* The programmer agent tries the repairs it knows, one at a time, on a copy:
  re-read the question with today's parsers, read a sum-to-100 table, lift a
  scripter's note out of the respondent's text into the routing it states. A
  repair is kept only when QA, re-run on the copy, finds strictly less wrong
  and nothing new. Otherwise the copy is thrown away.

* What neither can settle goes to a person, with the evidence: the question
  carries QA_ISSUE, is never auto- or bulk-approved, and the record says what
  QA found and what was tried.

A question a person has approved is never repaired — a reviewer's decision
outranks an agent's — but QA still reports on it.

Nothing here calls a model. Every check and every repair is decidable from the
question's own text, so the loop gives the same answer on every run.
"""

from __future__ import annotations

import copy
import re
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from src.core.survey_definition import (
    Question,
    QuestionType,
    SurveyDefinition,
    has_nothing_to_answer,
)
from src.engines.questionnaire import schema
from src.engines.questionnaire.constant_sum import make_constant_sum
from src.engines.questionnaire.constant_sum import read as read_constant_sum
from src.engines.questionnaire.routing_prose import parse_routing
from src.engines.questionnaire.verification import verify_against_source

FLAG_QA_ISSUE = "QA_ISSUE"

# Questions the loop does not test: not shown to a respondent, or not read yet
# (an unread question already blocks approval on its own flag).
_UNREAD = {
    schema.FLAG_EXTRACTION_UNAVAILABLE,
    schema.FLAG_EXTRACTION_CONFIG_ERROR,
    schema.FLAG_EXTRACTION_MODEL_ERROR,
    schema.FLAG_DERIVED_VARIABLE,
}
_CHOICE_ELEMENTS = {"radiogroup", "checkbox", "dropdown", "ranking", "tagbox"}
# A scripter's note at the start of a question's text: "[ASK IF ...] Which ..."
_LEADING_NOTE = re.compile(r"^\s*\[([^\]]+)\]\s*")
_ROUTING_NOTE = re.compile(r"^\s*(?:ASK|SHOW|DISPLAY)\b|\bONLY\s+(?:SHOW|ASK)\b", re.I)
_ANY_NOTE = re.compile(r"\s*\[[^\]]*\]\s*")
# What must never reach a respondent's screen: a bracketed scripter note, or the
# "|" that separates table cells in the stored text.
_SCRIPTER_RESIDUE = re.compile(r"\[[^\]]*\]|\s\|(?:\s|$)|^\|")
_EDGE_PIPES = re.compile(r"^(?:\s*\|)+\s*|(?:\s*\|)+\s*$")
# A table's header row glued onto the end of the question: "...Fill in | % |".
# Short cells only, so a real sentence that happens to contain "|" is left alone.
_TRAILING_CELLS = re.compile(r"(?:\s*\|\s*[^|]{0,12})+\s*\|?\s*$")
# "[ASK OF ALL]", "[BASE: ALL]": everyone is asked, so there is no routing to apply.
_EVERYONE = re.compile(r"\b(?:OF|TO)\s+ALL\b|\bBASE\s*:\s*ALL\b", re.I)
# Issues a repair may REVEAL without being blamed for them: a fix that gives an
# empty grid its rows also exposes that one row is only for some respondents.
# That is the truth surfacing, not a regression.
_DISCLOSURES = {"row_condition_not_applied", "routing_not_applied"}


@dataclass
class Issue:
    check: str
    detail: str
    evidence: str = ""


# --- QA agent -----------------------------------------------------------------


def _respondent_view(q: Question, by_qid: dict[str, Question]) -> list[Issue]:
    """The question exactly as the survey link compiles it."""
    from src.renderers.surveyjs_compiler import _compile_question

    try:
        element = _compile_question(q, by_qid)
    except Exception as exc:  # a question the link cannot build at all
        return [Issue("link_cannot_build", "the survey link could not build this question",
                      str(exc)[:160])]
    kind = element.get("type")
    empty = (
        (kind in _CHOICE_ELEMENTS and not element.get("choices"))
        or (kind in ("matrix", "matrixdropdown")
            and not (element.get("rows") and element.get("columns")))
        or (kind == "multipletext" and not element.get("items"))
        or kind == "html"
        or has_nothing_to_answer(q)
    )
    if empty:
        return [Issue("nothing_to_answer",
                      "the respondent sees this question with nothing to answer",
                      f"shown as {kind}")]
    leftovers = [t for t in _screen_texts(element) if _SCRIPTER_RESIDUE.search(t)]
    if leftovers:
        return [Issue("scripter_text_on_screen",
                      "the respondent would read a scripter's note or a table separator",
                      "; ".join(leftovers[:2])[:160])]
    return []


def _screen_texts(element: dict) -> list[str]:
    """Every piece of text the link puts in front of the respondent."""
    def text(x):
        return (x.get("text") or x.get("title") or "") if isinstance(x, dict) else str(x)
    texts = [element.get("title") or ""]
    for key in ("choices", "rows", "columns", "items"):
        texts += [text(x) for x in element.get(key) or []]
    return [t for t in texts if t]


def qa_check(q: Question, by_qid: dict[str, Question]) -> list[Issue]:
    """Everything QA finds wrong with one question, each with its evidence."""
    issues = _respondent_view(q, by_qid)
    for check in verify_against_source(q, q.source_text or ""):
        if check.passed or check.name == "no_scripter_tag_in_respondent_text":
            # Judged on the compiled screen above: the link already strips
            # notes from titles, so the stored text over-reports.
            continue
        # "Age < 25" names a value, not an answer code — not a broken screen-out.
        if check.name == "terminate_names_a_real_code" and q.type is QuestionType.NUMERIC:
            continue
        issues.append(Issue(check.name, check.detail, check.evidence))
    # Routing a note states — still in the text, or lifted out of it by the
    # programmer agent. Taking a note off the respondent's screen must never
    # take the problem out of sight with it.
    leading = _LEADING_NOTE.match(q.text or "")
    notes = ([leading.group(1)] if leading else []) + list(
        (q.validation or {}).get("lifted_notes") or []
    )
    if q.show_if is None:
        for body in notes:
            if _ROUTING_NOTE.search(body) and not _EVERYONE.search(body):
                issues.append(Issue(
                    "routing_not_applied",
                    "the document says who is asked this, and the survey asks everyone",
                    f"[{body}]",
                ))
    for row, condition in ((q.validation or {}).get("row_conditions") or {}).items():
        issues.append(Issue(
            "row_condition_not_applied",
            f'the row "{row}" is only for some respondents; the survey shows it to everyone',
            condition,
        ))
    return issues


# --- Programmer agent -----------------------------------------------------------


def _reread(q: Question) -> str | None:
    from src.engines.questionnaire.extractor import reread_question

    fresh = reread_question(q)
    if fresh is None:
        return None
    for field in ("type", "options", "rows", "cols", "text", "validation", "randomize_options"):
        setattr(q, field, getattr(fresh, field))
    return "re-read it from the document with today's parser"


def _constant_sum(q: Question) -> str | None:
    found = read_constant_sum(q.source_text or "")
    if found is None or (q.type is QuestionType.NUMERIC and q.rows):
        return None
    make_constant_sum(q, *found)
    return f"read it as {len(found[0])} number boxes that must add up to {found[1]}"


def _lift_notes(q: Question) -> str | None:
    """Take scripter notes out of what the respondent reads; apply routing they state."""
    done: list[str] = []
    note = _LEADING_NOTE.match(q.text or "")
    if note:
        body = note.group(1)
        if _ROUTING_NOTE.search(body) and not _EVERYONE.search(body) and q.show_if is None:
            routing = parse_routing(body)
            if routing is not None:
                q.show_if = routing
                done.append(f"applied its routing [{body}]")
        q.text = (q.text or "")[note.end():]
        # Kept on the question: off the respondent's screen, not out of the record.
        q.validation = {**(q.validation or {}),
                        "lifted_notes": [*((q.validation or {}).get("lifted_notes") or []), body]}
        done.append("moved the scripter note out of the question text")
    if " |" in (q.text or ""):
        trimmed = _TRAILING_CELLS.sub("", q.text).strip()
        if trimmed and trimmed != q.text:
            q.text = trimmed
            done.append("removed a table header that ran into the question text")
    for item in [*(q.options or []), *(q.rows or []), *(q.cols or [])]:
        cleaned = _EDGE_PIPES.sub("", _ANY_NOTE.sub(" ", item.label)).strip()
        if cleaned and cleaned != item.label:
            item.label = cleaned
            done.append("removed scripter notes and table separators from answer labels")
    return "; ".join(dict.fromkeys(done)) or None


REPAIRS = (_reread, _constant_sum, _lift_notes)


# --- The loop ---------------------------------------------------------------------


def _better(before: list[Issue], after: list[Issue]) -> bool:
    """Keep a repair only if it resolves something and breaks nothing.

    Resolves: some kind of issue is gone. Breaks nothing: no kind of issue grows,
    except the disclosures a fix can legitimately reveal.
    """
    was, now = Counter(i.check for i in before), Counter(i.check for i in after)
    resolved = set(was) - set(now)
    worse = {k for k in now if now[k] > was.get(k, 0)} - _DISCLOSURES
    return bool(resolved) and not worse


def _tested(q: Question) -> bool:
    return q.type is not QuestionType.INFO and not (set(q.flags or ()) & _UNREAD)


def run_qa(survey: SurveyDefinition, locked: set[str] | None = None) -> dict:
    """QA every question; let the programmer agent fix what it safely can.

    `locked` are qids a person approved: reported on, never changed.
    Writes the record to `survey.metadata["qa"]` and returns it.
    """
    locked = locked or set()
    by_qid = {q.qid: q for q in survey.questions}
    entries: list[dict] = []
    for q in survey.questions:
        if not _tested(q):
            continue
        q.flags = [f for f in q.flags if f != FLAG_QA_ISSUE]
        found = qa_check(q, by_qid)
        if not found:
            continue
        fixes: list[str] = []
        if q.qid not in locked:
            for repair in REPAIRS:
                trial = copy.deepcopy(q)
                did = repair(trial)
                if not did:
                    continue
                rechecked = qa_check(trial, {**by_qid, q.qid: trial})
                if _better(found, rechecked):
                    q.__dict__.update(trial.__dict__)
                    fixes.append(did)
                    found = rechecked
                if not found:
                    break
        for fix in fixes:
            entries.append({"qid": q.qid, "agent": "programmer", "status": "fixed",
                            "detail": fix, "rechecked_by_qa": True})
        for issue in found:
            entries.append({"qid": q.qid, "agent": "qa", "status": "needs_you",
                            **asdict(issue)})
        if found:
            q.flags.append(FLAG_QA_ISSUE)
            q.validation = {**(q.validation or {}),
                            "qa_issues": [f"{i.detail} ({i.evidence})" if i.evidence
                                          else i.detail for i in found]}
        else:
            (q.validation or {}).pop("qa_issues", None)

    flagged = {e["qid"] for e in entries if e["status"] == "needs_you"}
    auto = survey.metadata.get("auto_approved_qids")
    if auto:
        # QA found something on a question extraction had pre-approved.
        survey.metadata["auto_approved_qids"] = [q for q in auto if q not in flagged]
    record = {
        "ran_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "checked": sum(1 for q in survey.questions if _tested(q)),
        "fixed": sum(1 for e in entries if e["status"] == "fixed"),
        "needs_you": len(flagged),
        "entries": entries,
    }
    survey.metadata["qa"] = record
    return record

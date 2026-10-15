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
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
    has_nothing_to_answer,
)
from src.engines.questionnaire import schema
from src.engines.questionnaire.constant_sum import make_constant_sum, numeric_list_sum
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
# An answer that is really a question: it asks ("...?"), or it is an entry
# prompt ("Fill in", "Record number") — what respondents answer, not choose.
_ASKS = re.compile(r"\?\s*(?:\(|\[|$)|\bFill\s+in\b|\bRecord\s+(?:number|age|answer)\b", re.I)
# "{Q2}" in a compiled title — a whole question piped in (not "{Q2.1}").
_PIPE = re.compile(r"\{([A-Za-z][\w]*)\}")
_SUM_STATED = re.compile(r"(?:SUM|ADD)(?:\s+UP)?\s+TO\s+(?:AT\s+LEAST\s+)?\d", re.I)
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


def _codes_in(expr) -> list[tuple[str, object]]:
    """(qid, code) pairs an eq/in/selected condition tests, anywhere in the tree."""
    out = []
    if expr is None:
        return out
    if expr.op in ("eq", "selected") and expr.qid and expr.row is None:
        out.append((expr.qid, expr.value))
    if expr.op == "in" and expr.qid and expr.row is None:
        out.extend((expr.qid, v) for v in expr.values or [])
    for child in expr.children or []:
        out.extend(_codes_in(child))
    return out


def _order_and_answers(q: Question, by_qid: dict[str, Question]) -> list[Issue]:
    """Between-question checks that need no judgment: the survey's order and
    each question's own answer codes decide them. Found by the logic reviewer
    on a planted test survey; exact here, so they cost nothing and never vary."""
    order = {qid: i for i, qid in enumerate(by_qid)}
    here = order.get(q.qid)
    issues: list[Issue] = []
    if here is None:
        return issues
    for kind, expr in (("asked if", q.show_if), ("screens out if", q.terminate_if)):
        for ref in sorted((expr.referenced_qids() if expr else set()) - {q.qid}):
            if ref in order and order[ref] > here:
                issues.append(Issue(
                    "condition_on_later_question",
                    f"\u201c{kind}\u201d depends on {ref}, which is asked after this question "
                    "— nobody has answered it yet",
                    ref,
                ))
        for ref, code in _codes_in(expr):
            source = by_qid.get(ref)
            answers = [*(source.options or []), *(source.cols or [])] if source else []
            offered = {o.code for o in answers}
            if offered and code not in offered:
                issues.append(Issue(
                    "condition_on_missing_answer",
                    f"\u201c{kind}\u201d needs answer {code} at {ref}, which {ref} does not "
                    "offer — it can never be met",
                    f"{ref} = {code}",
                ))
    pipes = ((q.validation or {}).get("techniques") or {}).get("pipes") or []
    for ref in {p.get("source_qid") for p in pipes if p.get("source_qid")}:
        source = by_qid.get(ref)
        if source is None or ref not in order:
            continue
        if order[ref] > here:
            issues.append(Issue(
                "pipe_from_later_question",
                f"the text inserts {ref}'s answer, but {ref} is asked after this question",
                ref,
            ))
        elif source.show_if is not None and (
            q.show_if is None or q.show_if.to_dict() != source.show_if.to_dict()
        ):
            issues.append(Issue(
                "pipe_may_be_missing",
                f"the text inserts {ref}'s answer, but {ref} is only asked of some "
                "respondents and this question has a different condition — check everyone "
                "who sees this has answered it",
                ref,
            ))
    return issues


def _flat(text: str | None) -> str:
    return " ".join(re.sub(r"[^\w%]", " ", (text or "").lower()).split())


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
    # A pipe the link cannot fill: "{Q2}" from a question with several answers
    # (boxes of a constant sum, rows of a grid) prints "[object Object]".
    for name in _PIPE.findall(element.get("title") or ""):
        source = by_qid.get(name)
        if source is not None and source.rows and source.type in (
            QuestionType.NUMERIC, QuestionType.GRID_SINGLE
        ):
            return [Issue(
                "pipe_cannot_fill",
                f"the text inserts {name}'s answer, but {name} has {len(source.rows)} "
                f"answers — the respondent would read “[object Object]”. Say which "
                f"one to insert (e.g. “…FOR <row>”), or reword",
                "{" + name + "}",
            )]
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
    asking = [o.label for o in (q.options or []) if _ASKS.search(o.label)]
    if asking:
        issues.append(Issue(
            "question_inside_answers",
            f"{len(asking)} answer(s) read like questions — later questions were "
            "probably swallowed into this one and would never be asked",
            "; ".join(a[:70] for a in asking[:2]),
        ))
    # Another question's whole wording on a line of its own inside this one — a
    # second ask swallowed whole. Not a shared stem ("Which of the following best
    # describes you?") and not an intro screen split off from this question:
    # both were false alarms, measured on the real documents.
    def bare(text: str | None) -> str:
        return _flat(_ANY_NOTE.sub(" ", text or ""))

    own = bare(q.text)
    lines = {bare(line) for line in (q.source_text or "").splitlines()}
    repeats = [
        other.qid for other in by_qid.values()
        if other.qid != q.qid and other.type is not QuestionType.INFO
        and len(other.text or "") >= 40
        and bare(other.text) in lines
        and bare(other.text) not in own and own not in bare(other.text)
    ]
    if repeats:
        issues.append(Issue(
            "repeats_another_question",
            f"this holds a second copy of {', '.join(repeats)}'s question — usually one "
            "question asked of everyone; merge them or set who sees each",
            repeats[0],
        ))
    issues.extend(_order_and_answers(q, by_qid))
    summed = _own_sum(q)
    if summed and not (q.type is QuestionType.NUMERIC and q.rows):
        issues.append(Issue(
            "sum_not_applied",
            f"the document says these {len(summed[0])} answers must add up to "
            f"{'at least ' if summed[2] else ''}{summed[1]}; the survey asks it as "
            f"{q.type.value}",
            "; ".join(summed[0][:3]),
        ))
    v = q.validation or {}
    first = (q.source_text or "").strip().split("\n")[0]
    stated = _SUM_STATED.search(f"{q.text or ''}\n{first}")  # own wording only
    if (q.type is QuestionType.NUMERIC and q.rows and "sum" not in v and "sum_min" not in v
            and stated):
        issues.append(Issue(
            "sum_total_missing",
            "these number boxes must add up to a total the document states, "
            "but the survey sets no total",
            stated.group(0),
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


def _own_sum(q: Question) -> tuple | None:
    """A sum the question ITSELF states, with the boxes under it.

    Only the question's own wording counts: an older reading could hold the
    NEXT question's text too, and a single-choice question that swallowed a
    sum table must never be turned into number boxes because of it.
    """
    lines = (q.source_text or "").strip().splitlines()
    own = f"{q.text or ''}\n{lines[0] if lines else ''}"
    if not _SUM_STATED.search(own):
        return None
    return read_constant_sum(q.source_text or "")


def _constant_sum(q: Question) -> str | None:
    listed = numeric_list_sum(q)
    if listed:
        rows = [Option(code=o.code, label=o.label, show_if=o.show_if) for o in q.options]
        make_constant_sum(q, [r.label for r in rows], *listed, rows=rows)
        return f"read it as {len(rows)} number boxes that must add up to {listed[0]}"
    found = _own_sum(q)
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

    from src.engines.questionnaire import consistency  # imports Issue from here

    consistency.apply(survey, entries)
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

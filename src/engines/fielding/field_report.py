"""What happened in the field, from the sessions themselves.

The numbers a researcher is asked for the morning after a launch and cannot
currently get: how many started, how many qualified, WHICH screen-out removed
the rest, which quota cell closed, how long the interview actually took, and
where people gave up. All of it is already in the sessions — it has simply
never been counted.

Incidence is the one that costs money. A study bid at 40% incidence that fields
at 12% is a budget problem the day it starts, and the only way to see it on day
one is to divide completes by everyone the screener judged.

Which question terminated a session is not stored — the live route logs it and
moves on — so it is recomputed here through `decisions.terminating_question`,
the same function that made the decision at the time.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from statistics import median
from typing import Any

from src.core.survey_definition import Question, QuestionType, SurveyDefinition
from src.engines.fielding.decisions import (
    STATUS_COMPLETE,
    STATUS_OVERQUOTA,
    STATUS_SECURITY,
    STATUS_TERMINATE,
    QuotaCell,
    matching_cells,
    terminating_question,
)
from src.engines.questionnaire.buckets import HIDDEN, bucket_of, links


@dataclass
class FieldReport:
    starts: int = 0
    dispositions: dict[str, int] = field(default_factory=dict)
    #: completes / (completes + screened out). None when nobody was judged yet.
    incidence_pct: float | None = None
    terminates_by_question: list[dict[str, Any]] = field(default_factory=list)
    overquota_by_cell: list[dict[str, Any]] = field(default_factory=list)
    median_loi_sec: float | None = None
    dropoff_by_question: list[dict[str, Any]] = field(default_factory=list)
    #: One card per question: how the COMPLETES answered it. See `summarise`.
    questions: list[dict[str, Any]] = field(default_factory=list)
    #: What they took it on — a grid that fails on phones shows up here first.
    devices: dict[str, int] = field(default_factory=dict)
    #: Stated place vs the connection. Flagged for a look, never acted on.
    place_checks: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "starts": self.starts,
            "dispositions": self.dispositions,
            "incidence_pct": self.incidence_pct,
            "terminates_by_question": self.terminates_by_question,
            "overquota_by_cell": self.overquota_by_cell,
            "median_loi_sec": self.median_loi_sec,
            "dropoff_by_question": self.dropoff_by_question,
            "questions": self.questions,
            "devices": self.devices,
            "place_checks": self.place_checks,
        }


def _parsed(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def build_field_report(
    survey: SurveyDefinition,
    sessions: Iterable[Any],
    answers: Mapping[str, Mapping[str, Any]],
    quota_cells: list[QuotaCell] | None = None,
) -> FieldReport:
    """Count what the field did, from the sessions and their answers."""
    sessions = list(sessions)
    cells = list(quota_cells or [])
    report = FieldReport(starts=len(sessions))

    counts = {
        "complete": 0,
        "terminate": 0,
        "security": 0,
        "overquota": 0,
        "in_progress": 0,
    }
    by_status = {
        STATUS_COMPLETE: "complete",
        STATUS_TERMINATE: "terminate",
        STATUS_SECURITY: "security",
        STATUS_OVERQUOTA: "overquota",
    }
    terminates: dict[str, int] = {}
    overquota: dict[str, int] = {}
    durations: list[float] = []
    dropoff: dict[str, int] = {}
    completed: list[Mapping[str, Any]] = []

    devices: dict[str, int] = {}
    places: dict[str, int] = {}
    for session in sessions:
        params = getattr(session, "entry_params", None) or {}
        device = str(params.get("device") or "unknown")
        devices[device] = devices.get(device, 0) + 1
        verdict = str(params.get("place_check") or "not checked")
        places[verdict] = places.get(verdict, 0) + 1
        status = getattr(session, "status_code", None)
        counts[by_status.get(status, "in_progress")] += 1
        given = answers.get(session.id, {})

        if status == STATUS_COMPLETE:
            completed.append(given)
            start = _parsed(getattr(session, "started_at", None))
            end = _parsed(getattr(session, "completed_at", None))
            if start and end:
                durations.append((end - start).total_seconds())
            continue

        if status == STATUS_TERMINATE:
            # Recomputed through the same function that decided it at the time.
            question = terminating_question(survey, given)
            key = question.qid if question else "(no longer reproducible)"
            terminates[key] = terminates.get(key, 0) + 1
        elif status == STATUS_OVERQUOTA:
            matched = matching_cells(cells, given)
            key = matched[0].id if matched else "(cell no longer defined)"
            overquota[key] = overquota.get(key, 0) + 1
        elif status is None:
            # Still in progress, or abandoned. The last question they answered
            # is where the survey lost them — the single most actionable number
            # in a soft launch.
            answered = [qid for qid in survey.question_order() if qid in given]
            if answered:
                last = answered[-1]
                dropoff[last] = dropoff.get(last, 0) + 1

    report.devices = dict(sorted(devices.items(), key=lambda kv: -kv[1]))
    report.place_checks = dict(sorted(places.items(), key=lambda kv: -kv[1]))
    report.dispositions = counts
    judged = counts["complete"] + counts["terminate"]
    if judged:
        report.incidence_pct = round(100.0 * counts["complete"] / judged, 1)
    if durations:
        report.median_loi_sec = round(median(durations), 1)
    report.terminates_by_question = [
        {"qid": qid, "n": n}
        for qid, n in sorted(terminates.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    report.overquota_by_cell = [
        {"cell": cell, "n": n}
        for cell, n in sorted(overquota.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    report.dropoff_by_question = [
        {"qid": qid, "n": n}
        for qid, n in sorted(dropoff.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    # Each card carries its kind and its links, so the report can be read by
    # bucket and followed from one question to the ones it decides.
    connections = links(survey)
    report.questions = []
    for question in survey.questions:
        derived = bucket_of(question)[0] == HIDDEN
        if question.type is QuestionType.INFO and not derived:
            continue  # display text: nobody answers it
        card = summarise(question, completed)
        card["bucket"], card["bucket_reason"], card["bucket_suggested"] = bucket_of(question)
        card["links"] = connections.get(question.qid, {})
        report.questions.append(card)
    return report


#: Open ends shown on a card. The file has all of them; the card is a glance.
_SAMPLE_VERBATIMS = 5


def summarise(question: Question, answers: list[Mapping[str, Any]]) -> dict[str, Any]:
    """How the completes answered one question — the topline a researcher reads first.

    Completes only: a screen-out never reached most questions, and counting
    half-finished interviews makes every base different and every share wrong.
    Codes are compared as text because a stored answer may be 3 or "3".
    """
    given = [a[question.qid] for a in answers if a.get(question.qid) not in (None, "", [], {})]
    card: dict[str, Any] = {
        "qid": question.qid,
        "text": question.text,
        "type": question.type.value,
        "base": len(answers),
        "answered": len(given),
    }
    kind = question.type
    if kind in (QuestionType.SINGLE, QuestionType.DROPDOWN, QuestionType.MULTI, QuestionType.RANK):
        tally: dict[str, int] = {}
        for value in given:
            if kind is QuestionType.RANK:
                picked = value[:1] if isinstance(value, list) else []  # ranked 1st
            else:
                picked = value if isinstance(value, list) else [value]
            for code in picked:
                tally[str(code)] = tally.get(str(code), 0) + 1
        card["options"] = [
            {"code": o.code, "label": o.label, "n": tally.get(str(o.code), 0)}
            for o in question.options
        ]
    elif kind is QuestionType.GRID_SINGLE:
        cols = question.cols or []
        card["columns"] = [{"code": c.code, "label": c.label} for c in cols]
        card["rows"] = []
        for row in question.rows or []:
            picked = [
                str(v.get(str(row.code), v.get(row.code))) for v in given if isinstance(v, dict)
            ]
            card["rows"].append({
                "code": row.code,
                "label": row.label,
                "counts": [picked.count(str(c.code)) for c in cols],
            })
    elif kind is QuestionType.GRID_MULTI:
        # Each cell counts the respondents who ticked it; a row's counts can
        # add up to more than its base.
        cols = question.cols or []
        card["columns"] = [{"code": c.code, "label": c.label} for c in cols]
        card["rows"] = []
        for row in question.rows or []:
            picks = [v.get(str(row.code), v.get(row.code)) for v in given if isinstance(v, dict)]
            ticked = [{str(p) for p in (x if isinstance(x, list) else [x])}
                      for x in picks if x is not None]
            card["rows"].append({
                "code": row.code,
                "label": row.label,
                "counts": [sum(str(c.code) in t for t in ticked) for c in cols],
            })
    elif kind is QuestionType.NUMERIC:
        # Opt-out codes are not numbers anyone gave: "99 = prefer not to say"
        # averaged in as an age of 99 moves every statistic on the card.
        opt_outs = {str(o.code) for o in question.options}
        if opt_outs:
            card["options"] = [
                {"code": o.code, "label": o.label,
                 "n": sum(1 for v in given if str(v) == str(o.code))}
                for o in question.options
            ]
        numbers = sorted(float(v) for v in given if _is_number(v) and str(v) not in opt_outs)
        if numbers:
            card["numeric"] = {
                "mean": round(sum(numbers) / len(numbers), 2),
                "median": median(numbers),
                "min": numbers[0],
                "max": numbers[-1],
            }
    else:
        card["verbatims"] = [str(v) for v in given[-_SAMPLE_VERBATIMS:]][::-1]
    return card


def _is_number(value: Any) -> bool:
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True

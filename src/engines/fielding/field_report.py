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

from src.core.survey_definition import SurveyDefinition
from src.engines.fielding.decisions import (
    STATUS_COMPLETE,
    STATUS_OVERQUOTA,
    STATUS_SECURITY,
    STATUS_TERMINATE,
    QuotaCell,
    matching_cells,
    terminating_question,
)


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "starts": self.starts,
            "dispositions": self.dispositions,
            "incidence_pct": self.incidence_pct,
            "terminates_by_question": self.terminates_by_question,
            "overquota_by_cell": self.overquota_by_cell,
            "median_loi_sec": self.median_loi_sec,
            "dropoff_by_question": self.dropoff_by_question,
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

    for session in sessions:
        status = getattr(session, "status_code", None)
        counts[by_status.get(status, "in_progress")] += 1
        given = answers.get(session.id, {})

        if status == STATUS_COMPLETE:
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
    return report

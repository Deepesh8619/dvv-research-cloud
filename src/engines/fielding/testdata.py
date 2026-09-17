"""A dataset that obeys the questionnaire, before a single respondent exists.

Once an instrument is approved its data layout is fixed, and everything
downstream — the tab plan, the banner, the QC rules, the deck — can be built and
tested against it. Until now that work waited for real sample, which means it
waited for the field, which is the most expensive place to discover that a
column is missing.

The respondents come from the dry-run walk, not from a generator written for
this purpose. That walk already honours the survey's own bases, masks,
screen-outs and quotas — so the dummy data has the same holes in the same places
as the real thing: a question nobody qualified for is empty here too, a
screened-out respondent stops where the interview would have stopped, and a
filled quota cell turns people away. A second implementation would drift, and
the day it drifted QA would be testing a survey no respondent will ever see.

It is written through the SAME materializer the live export uses, so a column
that is wrong here is wrong in delivery, and a client's analyst can be handed a
structurally identical file on the day the questionnaire is signed off.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from src.core.models import DataMap
from src.core.survey_definition import SurveyDefinition
from src.engines.fielding.decisions import QuotaCell
from src.engines.fielding.dryrun import simulate
from src.engines.fielding.materialize import ALL_STATUSES, materialize_fielding

#: Where a synthetic interview starts, so timestamps are stable run to run.
_EPOCH = datetime(2026, 1, 1, 9, 0, 0)


@dataclass
class _Session:
    """What the materializer reads off a respondent session."""

    id: str
    status_code: int
    started_at: str
    completed_at: str | None


def synthesise(
    survey: SurveyDefinition,
    quota_cells: list[QuotaCell] | None = None,
    n: int = 200,
    seed: int = 20260917,
) -> tuple[DataMap, pd.DataFrame]:
    """A (DataMap, DataFrame) pair for an approved instrument, with no field.

    Deterministic for a given seed: QA comparing two runs is comparing their
    code, not their luck.
    """
    report = simulate(survey, quota_cells, n=n, seed=seed, collect=True)

    sessions: list[_Session] = []
    answers: dict[str, dict[str, Any]] = {}
    for index, (given, status) in enumerate(report.respondents, start=1):
        session_id = f"synthetic_{index:05d}"
        started = _EPOCH + timedelta(minutes=index)
        # A screened-out interview has no completion time, exactly as in field —
        # so anything computing incidence or length from this data meets the
        # same missing values it will meet in delivery.
        finished = started + timedelta(seconds=240 + (index % 7) * 30)
        sessions.append(
            _Session(
                id=session_id,
                status_code=status,
                started_at=started.isoformat(),
                completed_at=finished.isoformat() if status == 10 else None,
            )
        )
        answers[session_id] = given

    return materialize_fielding(survey, sessions, answers, statuses=ALL_STATUSES)

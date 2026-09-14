"""Heavy work really leaves the web process — and survives the trip there."""

import os
from datetime import datetime
from types import SimpleNamespace

from api import heavy
from src.core.survey_definition import Block, Option, Question, QuestionType, SurveyDefinition
from src.engines.fielding.field_report import build_field_report


def test_heavy_work_runs_in_another_process():
    if heavy.WORKERS == 0:  # inline mode: nothing to prove
        return
    assert heavy.run_heavy(os.getpid) != os.getpid()


def test_orm_rows_become_plain_picklable_sessions():
    row = SimpleNamespace(id="s1", status_code=10, started_at="2026-09-14T10:00:00",
                          completed_at="2026-09-14T10:06:00", entry_params={"pid": "P1"})
    (plain,) = heavy.plain_sessions([row])
    assert (plain.id, plain.status_code, plain.entry_params) == ("s1", 10, {"pid": "P1"})


def test_a_field_report_computed_across_processes_matches_inline():
    survey = SurveyDefinition(
        study_id="s", title="t", source="authored", status="approved", version=1,
        blocks=[Block(block_id="B", question_ids=["Q1"])],
        questions=[Question(qid="Q1", text="Q", type=QuestionType.SINGLE,
                            options=[Option(code=1, label="a"), Option(code=2, label="b")])],
    )
    now = datetime.now().isoformat()
    sessions = heavy.plain_sessions([
        SimpleNamespace(id=f"s{i}", status_code=10 if i % 2 else 20, started_at=now,
                        completed_at=now, entry_params={})
        for i in range(10)
    ])
    answers = {s.id: {"Q1": 1} for s in sessions}
    inline = build_field_report(survey, sessions, answers, []).to_dict()
    remote = heavy.run_heavy(build_field_report, survey, sessions, answers, []).to_dict()
    assert remote == inline

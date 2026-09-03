"""What happened in the field, counted from the sessions themselves.

Every number here is one a researcher is asked for the morning after a launch
and could not previously get. Incidence is the one that costs money: a study
bid at 40% that fields at 12% is a budget problem on day one, and the only way
to see it that early is to divide completes by everyone the screener judged.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.core.survey_definition import (
    Block,
    Expression,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
)
from src.engines.fielding.decisions import QuotaCell
from src.engines.fielding.field_report import build_field_report


@dataclass
class _Session:
    id: str
    status_code: int | None = None
    started_at: str | None = None
    completed_at: str | None = None


def _survey() -> SurveyDefinition:
    age = Question(
        qid="S1",
        text="Age",
        type=QuestionType.NUMERIC,
        terminate_if=Expression(op="lt", qid="S1", value=18),
    )
    gender = Question(
        qid="S2",
        text="Gender",
        type=QuestionType.SINGLE,
        options=[Option(code=1, label="Male"), Option(code=2, label="Female")],
    )
    q1 = Question(qid="Q1", text="Why?", type=QuestionType.OPEN_TEXT)
    return SurveyDefinition(
        study_id="s",
        title="t",
        questions=[age, gender, q1],
        blocks=[Block(block_id="B1", question_ids=["S1", "S2", "Q1"])],
    )


def test_incidence_is_completes_over_everyone_the_screener_judged() -> None:
    sessions = [
        _Session("a", 10, "2026-09-03T10:00:00", "2026-09-03T10:08:00"),
        _Session("b", 10, "2026-09-03T10:00:00", "2026-09-03T10:04:00"),
        _Session("c", 20),
        _Session("d", 20),
        _Session("e", 20),
    ]
    answers = {
        "a": {"S1": 30, "S2": 1, "Q1": "fine"},
        "b": {"S1": 40, "S2": 2, "Q1": "good"},
        "c": {"S1": 15},
        "d": {"S1": 16},
        "e": {"S1": 12},
    }

    report = build_field_report(_survey(), sessions, answers)

    assert report.starts == 5
    assert report.dispositions["complete"] == 2
    assert report.dispositions["terminate"] == 3
    assert report.incidence_pct == 40.0


def test_the_screen_out_that_removed_them_is_named() -> None:
    """Which question terminated a session is not stored — it is recomputed
    through the same function that decided it at the time."""
    sessions = [_Session("c", 20), _Session("d", 20)]
    answers = {"c": {"S1": 15}, "d": {"S1": 16}}

    report = build_field_report(_survey(), sessions, answers)

    assert report.terminates_by_question == [{"qid": "S1", "n": 2}]


def test_median_length_of_interview_uses_completes_only() -> None:
    """A terminate takes 20 seconds. Averaging it in makes the LOI a fiction."""
    sessions = [
        _Session("a", 10, "2026-09-03T10:00:00", "2026-09-03T10:08:00"),  # 480s
        _Session("b", 10, "2026-09-03T10:00:00", "2026-09-03T10:04:00"),  # 240s
        _Session("c", 20, "2026-09-03T10:00:00", "2026-09-03T10:00:20"),
    ]
    answers = {"a": {"S1": 30}, "b": {"S1": 40}, "c": {"S1": 15}}

    report = build_field_report(_survey(), sessions, answers)

    assert report.median_loi_sec == 360.0


def test_the_quota_cell_that_closed_is_named() -> None:
    cells = [
        QuotaCell(
            id="male",
            condition=Expression.from_dict({"op": "in", "qid": "S2", "values": [1]}),
            limit=1,
            count=1,
        )
    ]
    sessions = [_Session("x", 40), _Session("y", 40)]
    answers = {"x": {"S1": 30, "S2": 1}, "y": {"S1": 33, "S2": 1}}

    report = build_field_report(_survey(), sessions, answers, cells)

    assert report.overquota_by_cell == [{"cell": "male", "n": 2}]
    assert report.dispositions["overquota"] == 2


def test_where_the_survey_lost_people_is_reported() -> None:
    """An abandoned session has no status. The last question they answered is
    where the survey lost them — the most actionable number in a soft launch."""
    sessions = [_Session("z"), _Session("w")]
    answers = {"z": {"S1": 30, "S2": 1}, "w": {"S1": 44, "S2": 2}}

    report = build_field_report(_survey(), sessions, answers)

    assert report.dispositions["in_progress"] == 2
    assert report.dropoff_by_question == [{"qid": "S2", "n": 2}]


def test_nobody_judged_yet_reports_no_incidence_rather_than_zero() -> None:
    """0% and "not known yet" are different answers to a client's question."""
    report = build_field_report(_survey(), [_Session("z")], {"z": {"S1": 30}})

    assert report.incidence_pct is None
    assert report.median_loi_sec is None

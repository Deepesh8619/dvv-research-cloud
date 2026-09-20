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


def test_each_question_gets_a_topline_card_from_the_completes():
    """The report a researcher reads first: per question, how many answered and
    how the answers split — completes only, so every share has the same base."""
    from src.core.survey_definition import Option, Question, QuestionType
    from src.engines.fielding.field_report import summarise

    brands = [Option(code=1, label="Bubble"), Option(code=2, label="Anua")]
    completes = [
        {"Q1": 1, "Q2": [1, 2], "Q3": {"1": 2}, "Q4": 30, "Q5": "fine", "Q6": [2, 1]},
        {"Q1": "1", "Q2": [2], "Q3": {"1": 1}, "Q4": "40", "Q6": [1, 2]},
    ]
    single = summarise(Question(qid="Q1", text="?", options=brands), completes)
    assert (single["base"], single["answered"]) == (2, 2)
    assert [o["n"] for o in single["options"]] == [2, 0]  # 1 and "1" are one code

    multi = summarise(
        Question(qid="Q2", text="?", type=QuestionType.MULTI, options=brands), completes
    )
    assert [o["n"] for o in multi["options"]] == [1, 2]

    grid = summarise(
        Question(qid="Q3", text="?", type=QuestionType.GRID_SINGLE,
                 rows=[Option(code=1, label="Price")], cols=brands),
        completes,
    )
    assert grid["rows"][0]["counts"] == [1, 1]

    numeric = summarise(Question(qid="Q4", text="?", type=QuestionType.NUMERIC), completes)
    assert numeric["numeric"]["mean"] == 35

    text = summarise(Question(qid="Q5", text="?", type=QuestionType.OPEN_TEXT), completes)
    assert (text["answered"], text["verbatims"]) == (1, ["fine"])

    rank = summarise(
        Question(qid="Q6", text="?", type=QuestionType.RANK, options=brands), completes
    )
    assert [o["n"] for o in rank["options"]] == [1, 1]  # ranked FIRST


def test_the_report_says_what_they_answered_on_and_whether_the_place_agrees():
    """A grid that fails on phones shows up as "mobile drops at Q12" only if the
    device was recorded; a respondent nowhere near the area the sample was drawn
    on is worth flagging — and never worth dropping silently."""
    from src.engines.fielding.field_report import build_field_report

    class _S:
        def __init__(self, sid, device, place):
            self.id, self.status_code = sid, 10
            self.started_at = self.completed_at = "2026-09-20T10:00:00"
            self.entry_params = {"device": device, "place_check": place}

    survey = SurveyDefinition(
        study_id="T", source="extracted", status="approved",
        blocks=[Block(block_id="B", question_ids=["Q1"])],
        questions=[Question(qid="Q1", text="?", options=[Option(code=1, label="Yes")])],
    )
    sessions = [_S("a", "mobile", "match"), _S("b", "mobile", "mismatch"),
                _S("c", "desktop", None)]
    report = build_field_report(survey, sessions, {s.id: {"Q1": 1} for s in sessions}).to_dict()
    assert report["devices"] == {"mobile": 2, "desktop": 1}
    assert report["place_checks"]["mismatch"] == 1
    assert report["place_checks"]["not checked"] == 1

"""materialize_fielding: pure transform from respondent sessions -> DataMap + DataFrame (T11).

No DB, no HTTP — sessions are lightweight stand-ins carrying only the
attributes materialize_fielding actually reads (id, status_code, started_at,
completed_at), matching its own documented contract.
"""

from dataclasses import dataclass

from src.core.models import VarType
from src.core.survey_definition import (
    Block,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
)
from src.engines.fielding.materialize import (
    PARA_DURATION,
    PARA_END,
    PARA_START,
    STATUS_COMPLETE,
    materialize_fielding,
)


@dataclass
class _FakeSession:
    id: str
    status_code: int | None
    started_at: str | None = None
    completed_at: str | None = None


def _survey() -> SurveyDefinition:
    q_age = Question(qid="Q1", text="Age", type=QuestionType.NUMERIC)
    q_single = Question(
        qid="Q2",
        text="Do you drink coffee?",
        type=QuestionType.SINGLE,
        options=[Option(code=1, label="Yes"), Option(code=2, label="No")],
    )
    q_multi = Question(
        qid="Q3",
        text="Which brands do you know?",
        type=QuestionType.MULTI,
        options=[Option(code=1, label="Alpha"), Option(code=2, label="Beta")],
        required=False,
    )
    q_open = Question(qid="Q4", text="Anything else?", type=QuestionType.OPEN_TEXT, required=False)
    q_grid = Question(
        qid="Q5",
        text="Rate these",
        type=QuestionType.GRID_SINGLE,
        rows=[Option(code=1, label="Speed")],
        cols=[Option(code=1, label="Poor"), Option(code=2, label="Good")],
        required=False,
    )
    q_info = Question(qid="Q6", text="Thanks for participating", type=QuestionType.INFO)
    return SurveyDefinition(
        study_id="s1",
        title="T",
        source="authored",
        status="approved",
        blocks=[Block(block_id="B1", question_ids=["Q1", "Q2", "Q3", "Q4", "Q5", "Q6"])],
        questions=[q_age, q_single, q_multi, q_open, q_grid, q_info],
    )


def test_only_completes_become_respondents():
    survey = _survey()
    sessions = [
        _FakeSession("s_complete", STATUS_COMPLETE, "2026-01-01T00:00:00", "2026-01-01T00:10:00"),
        _FakeSession("s_terminate", 20, "2026-01-01T00:00:00", "2026-01-01T00:01:00"),
        _FakeSession("s_inflight", None, "2026-01-01T00:00:00"),
    ]
    answers = {
        "s_complete": {"Q1": 30, "Q2": 1},
        "s_terminate": {"Q1": 10},
        "s_inflight": {"Q1": 25},
    }
    datamap, df = materialize_fielding(survey, sessions, answers)
    assert len(df) == 1
    assert df.iloc[0]["Q1"] == 30


def test_single_question_emits_scalar_column_with_labels():
    survey = _survey()
    sessions = [_FakeSession("s1", STATUS_COMPLETE, "2026-01-01T00:00:00", "2026-01-01T00:05:00")]
    answers = {"s1": {"Q2": 1}}
    datamap, df = materialize_fielding(survey, sessions, answers)
    assert "Q2" in datamap
    assert datamap["Q2"].type == VarType.SINGLE
    assert datamap["Q2"].values == {1: "Yes", 2: "No"}
    assert df.iloc[0]["Q2"] == 1


def test_multi_question_explodes_into_dummy_columns():
    survey = _survey()
    sessions = [_FakeSession("s1", STATUS_COMPLETE, "2026-01-01T00:00:00", "2026-01-01T00:05:00")]
    answers = {"s1": {"Q3": [1]}}
    datamap, df = materialize_fielding(survey, sessions, answers)
    assert "Q3r1" in datamap and "Q3r2" in datamap
    assert df.iloc[0]["Q3r1"] == 1
    assert df.iloc[0]["Q3r2"] == 0


def test_multi_question_unanswered_is_null_not_zero():
    survey = _survey()
    sessions = [_FakeSession("s1", STATUS_COMPLETE, "2026-01-01T00:00:00", "2026-01-01T00:05:00")]
    answers = {"s1": {}}  # Q3 never shown/answered
    datamap, df = materialize_fielding(survey, sessions, answers)
    assert df.iloc[0]["Q3r1"] is None or str(df.iloc[0]["Q3r1"]) == "<NA>"


def test_open_text_is_text_type():
    survey = _survey()
    sessions = [_FakeSession("s1", STATUS_COMPLETE, "2026-01-01T00:00:00", "2026-01-01T00:05:00")]
    answers = {"s1": {"Q4": "Great coffee"}}
    datamap, df = materialize_fielding(survey, sessions, answers)
    assert datamap["Q4"].type == VarType.TEXT
    assert df.iloc[0]["Q4"] == "Great coffee"


def test_grid_single_emits_one_column_per_row():
    survey = _survey()
    sessions = [_FakeSession("s1", STATUS_COMPLETE, "2026-01-01T00:00:00", "2026-01-01T00:05:00")]
    answers = {"s1": {"Q5": {1: 2}}}
    datamap, df = materialize_fielding(survey, sessions, answers)
    assert "Q5r1" in datamap
    assert df.iloc[0]["Q5r1"] == 2


def test_info_question_emits_no_column():
    survey = _survey()
    sessions = [_FakeSession("s1", STATUS_COMPLETE, "2026-01-01T00:00:00", "2026-01-01T00:05:00")]
    answers = {"s1": {}}
    datamap, _ = materialize_fielding(survey, sessions, answers)
    assert "Q6" not in datamap


def test_missing_numeric_answer_is_null():
    survey = _survey()
    sessions = [_FakeSession("s1", STATUS_COMPLETE, "2026-01-01T00:00:00", "2026-01-01T00:05:00")]
    answers = {"s1": {}}
    _, df = materialize_fielding(survey, sessions, answers)
    assert df.iloc[0]["Q1"] is None or str(df.iloc[0]["Q1"]) == "<NA>"


def test_paradata_columns_present_with_duration():
    survey = _survey()
    sessions = [_FakeSession("s1", STATUS_COMPLETE, "2026-01-01T00:00:00", "2026-01-01T00:10:00")]
    answers = {"s1": {}}
    datamap, df = materialize_fielding(survey, sessions, answers)
    assert PARA_START in datamap and PARA_END in datamap and PARA_DURATION in datamap
    assert df.iloc[0][PARA_DURATION] == 600.0


def test_session_id_paradata_column():
    sessions = [
        _FakeSession("s_b", STATUS_COMPLETE, "2026-01-01T00:01:00", "2026-01-01T00:02:00"),
        _FakeSession("s_a", STATUS_COMPLETE, "2026-01-01T00:00:00", "2026-01-01T00:02:00"),
    ]
    datamap, df = materialize_fielding(_survey(), sessions, {})
    assert "vSessionId" in datamap
    assert list(df["vSessionId"]) == ["s_a", "s_b"]


def test_respondent_id_is_stable_sequential():
    survey = _survey()
    sessions = [
        _FakeSession("s_b", STATUS_COMPLETE, "2026-01-01T00:01:00", "2026-01-01T00:02:00"),
        _FakeSession("s_a", STATUS_COMPLETE, "2026-01-01T00:00:00", "2026-01-01T00:02:00"),
    ]
    answers = {"s_a": {"Q1": 1}, "s_b": {"Q1": 2}}
    _, df = materialize_fielding(survey, sessions, answers)
    # Sorted by started_at: s_a (00:00) first -> respondent_id 1, s_b -> 2.
    assert list(df["respondent_id"]) == [1, 2]
    assert list(df["Q1"]) == [1, 2]


def test_no_completes_yields_empty_frame_not_crash():
    survey = _survey()
    sessions = [_FakeSession("s1", 20, "2026-01-01T00:00:00", "2026-01-01T00:01:00")]
    datamap, df = materialize_fielding(survey, sessions, {"s1": {}})
    assert len(df) == 0
    assert len(datamap) > 0  # DataMap schema still built even with zero rows

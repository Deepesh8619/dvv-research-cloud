"""The data proves a base the questionnaire never stated.

The cross-check can only verify the logic extraction found. On a real
79-question consumer study the parser produced six `show_if` conditions — and
the file showed eleven distinct answer populations, because different audiences
were asked different blocks.
"""

import tempfile
from pathlib import Path

import polars as pl
import pytest

from src.core.survey_definition import (
    Expression,
    Option,
    Question,
    QuestionType,
    QuestionVariableLink,
    SurveyDefinition,
)
from src.engines.qc.base_gaps import find_audiences, summarise


def _survey(*questions: Question) -> SurveyDefinition:
    return SurveyDefinition(study_id="t", questions=list(questions))


def _asked(qid: str, **kw) -> Question:
    return Question(
        qid=qid, text=qid, type=QuestionType.SINGLE,
        options=[Option(code=1, label="Yes")], **kw
    )


@pytest.fixture
def parquet(tmp_path):
    def write(frame: dict):
        path = Path(tmp_path) / "d.parquet"
        pl.DataFrame(frame).write_parquet(str(path))
        return path

    return write


def _links(*qids):
    return [QuestionVariableLink(qid=q, variable_names=[q]) for q in qids]


def test_questions_answered_by_the_same_people_are_one_audience(parquet):
    """Six questions with 30 respondents each are ONE finding, not six. Thirty
    "this looks gated" lines is a wall a reviewer scrolls past."""
    path = parquet({
        "B1": [1] * 30 + [None] * 70,
        "B2": [1] * 30 + [None] * 70,
        "S1": [1] * 100,
    })
    audiences = find_audiences(
        _survey(_asked("B1"), _asked("B2"), _asked("S1")), _links("B1", "B2", "S1"), path
    )
    assert len(audiences) == 1
    assert audiences[0].n_respondents == 30
    assert audiences[0].qids == ["B1", "B2"]
    assert audiences[0].unexplained_qids == ["B1", "B2"]


def test_the_same_count_over_different_people_is_two_audiences(parquet):
    """Two questions with 30 answers each are one audience only if they are the
    SAME 30 — otherwise they are two different gates that happen to match."""
    path = parquet({
        "Q1": [1] * 30 + [None] * 70,
        "Q2": [None] * 70 + [1] * 30,
    })
    audiences = find_audiences(_survey(_asked("Q1"), _asked("Q2")), _links("Q1", "Q2"), path)
    assert len(audiences) == 2


def test_a_question_everyone_answered_is_not_an_audience(parquet):
    """Item non-response on a required question is a percent or two; a base is
    an order of magnitude away from that."""
    path = parquet({"Q1": [1] * 98 + [None] * 2})
    assert find_audiences(_survey(_asked("Q1")), _links("Q1"), path) == []


def test_a_base_the_questionnaire_states_is_not_reported_as_missing(parquet):
    path = parquet({"Q1": [1] * 30 + [None] * 70})
    survey = _survey(
        _asked("Q1", show_if=Expression.from_dict({"op": "eq", "qid": "S1", "value": 1}))
    )
    audiences = find_audiences(survey, _links("Q1"), path)
    assert audiences[0].n_respondents == 30
    assert audiences[0].unexplained_qids == []  # the document explains this one


def test_a_screened_out_population_is_explained_by_its_terminate(parquet):
    """A drop after a screen-out is the terminate working, not a missing base."""
    path = parquet({"S2": [1] * 40 + [None] * 60})
    survey = _survey(
        _asked("S2", terminate_if=Expression.from_dict(
            {"op": "eq", "qid": "S2", "value": 9}
        ))
    )
    assert find_audiences(survey, _links("S2"), path)[0].unexplained_qids == []


def test_a_handful_of_respondents_is_not_a_finding(parquet):
    path = parquet({"Q1": [1] * 5 + [None] * 95})
    assert find_audiences(_survey(_asked("Q1")), _links("Q1"), path) == []


def test_a_missing_data_file_is_a_skip_not_a_crash():
    """The free tier wipes the disk; a study can outlive its own data file, and
    nothing to look at is not the same as nothing found."""
    with tempfile.TemporaryDirectory() as tmp:
        gone = Path(tmp) / "not-here.parquet"
        assert find_audiences(_survey(_asked("Q1")), _links("Q1"), gone) == []


def test_the_summary_names_the_population_and_its_questions(parquet):
    path = parquet({"B1": [1] * 30 + [None] * 70, "B2": [1] * 30 + [None] * 70})
    found = summarise(find_audiences(_survey(_asked("B1"), _asked("B2")), _links("B1", "B2"), path))
    assert found["n_audiences"] == 1
    assert found["n_unexplained"] == 1
    assert "30 respondents answered B1, B2" in found["detail"]

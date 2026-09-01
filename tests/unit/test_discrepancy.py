"""Data checked against the questionnaire that produced it.

Every case plants a KNOWN defect in a real parquet file and asserts it is
found — and, just as importantly, that a clean file produces nothing. A check
that cannot stay quiet is not a check.
"""

from __future__ import annotations

import duckdb
import pytest

from src.core.survey_definition import (
    Block,
    Expression,
    Option,
    Question,
    QuestionType,
    QuestionVariableLink,
    SurveyDefinition,
)
from src.engines.qc.discrepancy import check_against_questionnaire


def _survey(questions: list[Question]) -> SurveyDefinition:
    return SurveyDefinition(
        study_id="s1",
        title="T",
        source="extracted",
        status="draft",
        blocks=[Block(block_id="B1", question_ids=[q.qid for q in questions])],
        questions=questions,
    )


def _parquet(tmp_path, columns: dict[str, list]):
    """Write a small parquet file from column data."""
    path = tmp_path / "data.parquet"
    con = duckdb.connect()
    try:
        n = len(next(iter(columns.values())))
        def _cell(col: str, i: int) -> str:
            value = columns[col][i]
            return "NULL" if value is None else str(value)

        rows = [
            "(" + ", ".join(_cell(c, i) for c in columns) + ")"
            for i in range(n)
        ]
        cols_sql = ", ".join(f'"{c}" INTEGER' for c in columns)
        con.execute(f"CREATE TABLE d ({cols_sql})")
        con.execute(f"INSERT INTO d VALUES {', '.join(rows)}")
        con.execute(f"COPY d TO '{path}' (FORMAT PARQUET)")
    finally:
        con.close()
    return path


SINGLE_Q = Question(
    qid="Q1",
    text="Which one?",
    type=QuestionType.SINGLE,
    options=[Option(code=1, label="A"), Option(code=2, label="B"), Option(code=3, label="C")],
)


def test_a_clean_file_produces_no_findings(tmp_path):
    """The check must be able to say nothing is wrong."""
    path = _parquet(tmp_path, {"Q1": [1, 2, 3, 1]})
    report = check_against_questionnaire(
        _survey([SINGLE_Q]), [QuestionVariableLink(qid="Q1", variable_names=["Q1"])], path
    )
    assert report.findings == []
    assert report.passed is True
    assert report.n_rows == 4
    assert report.checked.get("codes") == 1  # it did look


def test_a_code_the_questionnaire_never_declared_is_found(tmp_path):
    path = _parquet(tmp_path, {"Q1": [1, 2, 6, 1]})
    report = check_against_questionnaire(
        _survey([SINGLE_Q]), [QuestionVariableLink(qid="Q1", variable_names=["Q1"])], path
    )
    assert [f.kind for f in report.findings] == ["undeclared_code"]
    found = report.findings[0]
    assert found.n_rows == 1
    assert found.example_rows == [3]      # points at the actual row
    assert "6" in found.detail
    assert report.passed is False


def test_common_system_codes_are_not_reported_as_undeclared(tmp_path):
    """99 = "prefer not to answer" is in nearly every file and almost never in
    the response list. Flagging it would bury the real findings."""
    path = _parquet(tmp_path, {"Q1": [1, 99, 2]})
    report = check_against_questionnaire(
        _survey([SINGLE_Q]), [QuestionVariableLink(qid="Q1", variable_names=["Q1"])], path
    )
    assert report.findings == []


def test_two_answers_to_a_single_answer_question_are_found(tmp_path):
    path = _parquet(tmp_path, {"Q1_1": [1, 1, 0], "Q1_2": [0, 1, 1], "Q1_3": [0, 0, 0]})
    report = check_against_questionnaire(
        _survey([SINGLE_Q]),
        [QuestionVariableLink(qid="Q1", variable_names=["Q1_1", "Q1_2", "Q1_3"])],
        path,
    )
    kinds = [f.kind for f in report.findings]
    assert "single_multi" in kinds
    finding = next(f for f in report.findings if f.kind == "single_multi")
    assert finding.example_rows == [2]


def test_a_numeric_answer_outside_the_stated_range_is_found(tmp_path):
    age = Question(
        qid="AGE", text="Age?", type=QuestionType.NUMERIC, validation={"min": 18, "max": 99}
    )
    path = _parquet(tmp_path, {"AGE": [25, 17, 40, 120]})
    report = check_against_questionnaire(
        _survey([age]), [QuestionVariableLink(qid="AGE", variable_names=["AGE"])], path
    )
    finding = next(f for f in report.findings if f.kind == "out_of_range")
    assert "18 to 99" in finding.detail  # a researcher writes 18, not 18.0
    assert finding.n_rows == 2
    assert finding.example_rows == [2, 4]


def test_a_respondent_the_screen_out_should_have_removed_is_found(tmp_path):
    """The check the whole data phase exists for."""
    screener = Question(
        qid="S1",
        text="Do you work in market research?",
        type=QuestionType.SINGLE,
        options=[Option(code=1, label="Yes"), Option(code=2, label="No")],
        terminate_if=Expression.from_dict({"op": "in", "qid": "S1", "values": [1]}),
    )
    path = _parquet(tmp_path, {"S1": [2, 2, 1, 2]})
    report = check_against_questionnaire(
        _survey([screener]), [QuestionVariableLink(qid="S1", variable_names=["S1"])], path
    )
    finding = next(f for f in report.findings if f.kind == "terminate_violated")
    assert finding.n_rows == 1
    assert finding.example_rows == [3]


def test_a_screen_out_it_cannot_evaluate_is_reported_as_skipped_not_passed(tmp_path):
    """"Nothing found" and "nothing looked at" must never read the same."""
    gated = Question(
        qid="Q2",
        text="Which brand?",
        type=QuestionType.SINGLE,
        options=[Option(code=1, label="A")],
        terminate_if=Expression.from_dict({"op": "in", "qid": "QMISSING", "values": [1]}),
    )
    path = _parquet(tmp_path, {"Q2": [1, 1]})
    report = check_against_questionnaire(
        _survey([gated]), [QuestionVariableLink(qid="Q2", variable_names=["Q2"])], path
    )
    assert any("unlinked" in s for s in report.skipped)
    assert "terminate" not in report.checked


def test_no_data_file_is_skipped_not_failed(tmp_path):
    """A questionnaire is reviewed long before any data arrives."""
    report = check_against_questionnaire(
        _survey([SINGLE_Q]),
        [QuestionVariableLink(qid="Q1", variable_names=["Q1"])],
        tmp_path / "absent.parquet",
    )
    assert report.findings == []
    assert report.skipped == ["no data file"]


def test_nothing_linked_is_skipped_not_passed(tmp_path):
    """A questionnaire nobody could link must not pass as spotless."""
    path = _parquet(tmp_path, {"Q1": [1]})
    report = check_against_questionnaire(_survey([SINGLE_Q]), [], path)
    assert report.findings == []
    assert report.skipped and "link" in report.skipped[0]


def test_worst_findings_come_first(tmp_path):
    """A researcher reads the top of the list and stops."""
    q = Question(
        qid="Q1", text="Pick", type=QuestionType.SINGLE,
        options=[Option(code=1, label="A")],
    )
    path = _parquet(tmp_path, {"Q1": [5, 5, 5, 6]})
    report = check_against_questionnaire(
        _survey([q]), [QuestionVariableLink(qid="Q1", variable_names=["Q1"])], path
    )
    assert [f.n_rows for f in report.findings] == [3, 1]


@pytest.mark.parametrize("bad", [None])
def test_missing_answers_are_not_discrepancies(tmp_path, bad):
    """A blank is skip logic, not a violation."""
    path = _parquet(tmp_path, {"Q1": [1, bad, 2]})
    report = check_against_questionnaire(
        _survey([SINGLE_Q]), [QuestionVariableLink(qid="Q1", variable_names=["Q1"])], path
    )
    assert report.findings == []

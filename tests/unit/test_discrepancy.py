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


def _screener_and_later() -> list[Question]:
    """A screen-out on S1, and one question that comes after it."""
    return [
        Question(
            qid="S1",
            text="Do you work in market research?",
            type=QuestionType.SINGLE,
            options=[Option(code=1, label="Yes"), Option(code=2, label="No")],
            terminate_if=Expression.from_dict({"op": "in", "qid": "S1", "values": [1]}),
        ),
        Question(qid="Q2", text="How satisfied are you?", type=QuestionType.NUMERIC),
    ]


_SCREENER_LINKS = [
    QuestionVariableLink(qid="S1", variable_names=["S1"]),
    QuestionVariableLink(qid="Q2", variable_names=["Q2"]),
]


def test_a_screened_out_respondent_who_kept_answering_is_an_error(tmp_path):
    """The check the whole data phase exists for.

    Row 3 punched the screen-out and then answered a later question. Whatever
    the script was supposed to do, the interview did not stop.
    """
    path = _parquet(tmp_path, {"S1": [2, 2, 1, 2], "Q2": [7, 8, 9, 6]})
    report = check_against_questionnaire(
        _survey(_screener_and_later()), _SCREENER_LINKS, path
    )
    finding = next(f for f in report.findings if f.kind == "terminate_violated")
    assert finding.severity == "error"
    assert finding.n_rows == 1
    assert finding.example_rows == [3]


def test_a_screened_out_respondent_with_no_later_answers_is_not_an_error(tmp_path):
    """Plenty of exports keep their terminates. That is not a defect.

    Calling mere presence an error made every recorded terminate red on a
    survey that behaved perfectly.
    """
    path = _parquet(tmp_path, {"S1": [2, 2, 1, 2], "Q2": [7, 8, None, 6]})
    report = check_against_questionnaire(
        _survey(_screener_and_later()), _SCREENER_LINKS, path
    )
    assert not [f for f in report.findings if f.kind == "terminate_violated"]
    finding = next(f for f in report.findings if f.kind == "terminate_recorded")
    assert finding.severity == "info"
    assert finding.n_rows == 1
    assert "held" in finding.detail


def test_with_nothing_asked_after_it_enforcement_is_a_warning_not_a_pass(tmp_path):
    """"Nothing found" and "could not look" must never read the same."""
    screener = _screener_and_later()[0]
    path = _parquet(tmp_path, {"S1": [2, 2, 1, 2]})
    report = check_against_questionnaire(
        _survey([screener]), [QuestionVariableLink(qid="S1", variable_names=["S1"])], path
    )
    finding = next(f for f in report.findings if f.kind == "terminate_recorded")
    assert finding.severity == "warning"
    assert "could not be checked" in finding.detail


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


def test_all_bad_values_for_one_question_are_a_single_finding(tmp_path):
    """One broken column must not become one finding per distinct value.

    On a real client file this produced fourteen separate entries for a single
    mis-read age question and buried everything else in the report.
    """
    q = Question(
        qid="Q1", text="Pick", type=QuestionType.SINGLE,
        options=[Option(code=1, label="A")],
    )
    path = _parquet(tmp_path, {"Q1": [5, 5, 5, 6]})
    report = check_against_questionnaire(
        _survey([q]), [QuestionVariableLink(qid="Q1", variable_names=["Q1"])], path
    )
    assert len(report.findings) == 1
    finding = report.findings[0]
    assert finding.n_rows == 4          # every offending respondent, counted once
    assert "5" in finding.detail and "6" in finding.detail


def test_worst_findings_come_first(tmp_path):
    """A researcher reads the top of the list and stops."""
    qs = [
        Question(qid="Q1", text="Pick", type=QuestionType.SINGLE,
                 options=[Option(code=1, label="A")]),
        Question(qid="Q2", text="Pick", type=QuestionType.SINGLE,
                 options=[Option(code=1, label="A")]),
    ]
    path = _parquet(tmp_path, {"Q1": [5, 5, 5, 1], "Q2": [1, 1, 1, 6]})
    report = check_against_questionnaire(
        _survey(qs),
        [QuestionVariableLink(qid="Q1", variable_names=["Q1"]),
         QuestionVariableLink(qid="Q2", variable_names=["Q2"])],
        path,
    )
    assert [f.qid for f in report.findings] == ["Q1", "Q2"]
    assert [f.n_rows for f in report.findings] == [3, 1]


@pytest.mark.parametrize("bad", [None])
def test_missing_answers_are_not_discrepancies(tmp_path, bad):
    """A blank is skip logic, not a violation."""
    path = _parquet(tmp_path, {"Q1": [1, bad, 2]})
    report = check_against_questionnaire(
        _survey([SINGLE_Q]), [QuestionVariableLink(qid="Q1", variable_names=["Q1"])], path
    )
    assert report.findings == []


# --- The data map is the authority on what a column holds -------------------
# Measured on a real 750-respondent client file: without this, comparing raw
# ages against a mis-extracted option list produced 14 "undeclared code"
# findings for ages 32 to 45 — all of them perfectly good data. A check that
# cries wolf 715 times on a clean file is worse than no check at all.


def _datamap(entries):
    from src.core.models import DataMap, Variable, VarType

    dm = DataMap()
    for name, values in entries:
        dm.add(Variable(name=name, label=name, type=VarType.SINGLE, values=values))
    return dm


def test_an_open_numeric_column_is_not_code_checked(tmp_path):
    """The datamap gives RESP_AGE no code list, so it is an open number."""
    age = Question(
        qid="RESP_AGE",
        text="What is your age?",
        type=QuestionType.SINGLE,
        options=[Option(code=c, label=f"band {c}") for c in range(1, 11)],
    )
    path = _parquet(tmp_path, {"RESP_AGE": [32, 38, 44, 45]})
    report = check_against_questionnaire(
        _survey([age]),
        [QuestionVariableLink(qid="RESP_AGE", variable_names=["RESP_AGE"])],
        path,
        datamap=_datamap([("RESP_AGE", {})]),
    )
    assert [f.kind for f in report.findings] == ["type_mismatch"]
    assert report.findings[0].severity == "warning"
    assert report.findings[0].n_rows == 0  # a reading disagreement, not bad data


def test_an_option_the_scripter_never_punched_is_reported(tmp_path):
    """Questionnaire offers code 3; the data map has no punch for it."""
    q = Question(
        qid="Q1", text="Pick", type=QuestionType.SINGLE,
        options=[Option(code=1, label="A"), Option(code=2, label="B"), Option(code=3, label="C")],
    )
    path = _parquet(tmp_path, {"Q1": [1, 2, 1]})
    report = check_against_questionnaire(
        _survey([q]),
        [QuestionVariableLink(qid="Q1", variable_names=["Q1"])],
        path,
        datamap=_datamap([("Q1", {1: "A", 2: "B"})]),
    )
    assert any(f.kind == "option_not_scripted" and "3" in f.detail for f in report.findings)


def test_a_punch_the_questionnaire_never_offers_is_reported(tmp_path):
    """The data map has code 4; the questionnaire never asks it."""
    q = Question(
        qid="Q1", text="Pick", type=QuestionType.SINGLE,
        options=[Option(code=1, label="A"), Option(code=2, label="B")],
    )
    path = _parquet(tmp_path, {"Q1": [1, 2, 4]})
    report = check_against_questionnaire(
        _survey([q]),
        [QuestionVariableLink(qid="Q1", variable_names=["Q1"])],
        path,
        datamap=_datamap([("Q1", {1: "A", 2: "B", 4: "D"})]),
    )
    assert any(f.kind == "punch_not_in_questionnaire" and "4" in f.detail for f in report.findings)


def test_data_is_judged_against_the_scripted_punches_not_the_extraction(tmp_path):
    """A value outside the SCRIPTED list is a real data defect. A value the
    extraction merely failed to list is reported once as a punch mismatch, not
    once per respondent."""
    q = Question(
        qid="Q1", text="Pick", type=QuestionType.SINGLE,
        options=[Option(code=1, label="A")],
    )
    path = _parquet(tmp_path, {"Q1": [1, 2, 7]})
    report = check_against_questionnaire(
        _survey([q]),
        [QuestionVariableLink(qid="Q1", variable_names=["Q1"])],
        path,
        datamap=_datamap([("Q1", {1: "A", 2: "B"})]),
    )
    undeclared = [f for f in report.findings if f.kind == "undeclared_code"]
    assert len(undeclared) == 1
    assert undeclared[0].n_rows == 1        # only the 7
    assert "7" in undeclared[0].detail


# --- Masking ----------------------------------------------------------------
# A masked question offers only what the respondent chose earlier. A
# mis-scripted mask shows the wrong list to real people and leaves no trace in
# the output — nothing else in the pipeline can catch it.


def _masked_pair():
    source = Question(
        qid="Q18",
        text="Which have you used?",
        type=QuestionType.MULTI,
        options=[Option(code=1, label="A"), Option(code=2, label="B"), Option(code=3, label="C")],
    )
    masked = Question(
        qid="Q18a",
        text="How often for each?",
        type=QuestionType.MULTI,
        options=[Option(code=1, label="A"), Option(code=2, label="B"), Option(code=3, label="C")],
        validation={
            "techniques": {
                "masks": [{"source_qid": "Q18", "mode": "selected", "exclude_qid": None}],
                "pipes": [],
                "least_fill": False,
            }
        },
    )
    return source, masked


def _links():
    return [
        QuestionVariableLink(qid="Q18", variable_names=["Q18r1", "Q18r2", "Q18r3"]),
        QuestionVariableLink(qid="Q18a", variable_names=["Q18ar1", "Q18ar2", "Q18ar3"]),
    ]


def test_a_mask_that_held_produces_no_finding(tmp_path):
    """Respondent chose 1 and 3 at Q18, and answered Q18a within that set."""
    path = _parquet(tmp_path, {
        "Q18r1": [1, 1], "Q18r2": [0, 0], "Q18r3": [1, 1],
        "Q18ar1": [1, 0], "Q18ar2": [0, 0], "Q18ar3": [1, 1],
    })
    report = check_against_questionnaire(_survey(list(_masked_pair())), _links(), path)
    assert [f for f in report.findings if f.kind == "mask_violated"] == []
    assert report.checked.get("mask") == 1  # it did look


def test_an_answer_outside_the_masked_list_is_found(tmp_path):
    """Respondent never chose option 2 at Q18, yet answered it at Q18a."""
    path = _parquet(tmp_path, {
        "Q18r1": [1, 1], "Q18r2": [0, 0], "Q18r3": [1, 0],
        "Q18ar1": [1, 1], "Q18ar2": [0, 1], "Q18ar3": [0, 0],
    })
    report = check_against_questionnaire(_survey(list(_masked_pair())), _links(), path)
    finding = next(f for f in report.findings if f.kind == "mask_violated")
    assert finding.qid == "Q18a"
    assert finding.example_rows == [2]


def test_a_respondent_who_never_answered_the_source_is_not_judged(tmp_path):
    """No answer at Q18 means the mask had nothing to work from — that is skip
    logic, not a violation."""
    path = _parquet(tmp_path, {
        "Q18r1": [None], "Q18r2": [None], "Q18r3": [None],
        "Q18ar1": [1], "Q18ar2": [0], "Q18ar3": [0],
    })
    report = check_against_questionnaire(_survey(list(_masked_pair())), _links(), path)
    assert [f for f in report.findings if f.kind == "mask_violated"] == []


def test_a_mask_whose_source_is_unlinked_is_skipped_not_passed(tmp_path):
    """Nothing to compare against must never read as a clean result."""
    _, masked = _masked_pair()
    path = _parquet(tmp_path, {"Q18ar1": [1], "Q18ar2": [0], "Q18ar3": [0]})
    report = check_against_questionnaire(
        _survey([masked]),
        [QuestionVariableLink(qid="Q18a", variable_names=["Q18ar1", "Q18ar2", "Q18ar3"])],
        path,
    )
    assert any("masked from Q18" in s for s in report.skipped)
    assert "mask" not in report.checked


def test_an_open_question_whose_codes_live_in_a_companion_is_explained_not_blamed(tmp_path):
    """"The column has no codes" and "the questionnaire lists bands" are both
    true and not a contradiction: the bands live in the derived variable, and
    the conditions are written against that."""
    from src.core.models import DataMap, Variable, VarType

    spend = Question(
        qid="SS_AD_SPEND",
        text="How much do you spend?",
        type=QuestionType.SINGLE,
        options=[Option(code=1, label="Under $1m"), Option(code=2, label="Over $1m")],
    )
    dm = DataMap()
    dm.add(Variable(name="SS_AD_SPEND", label="How much do you spend?",
                    type=VarType.NUMERIC, values={}))
    dm.add(Variable(name="HID_SS_AD_SPEND",
                    label="Hidden variable: Flag if greater than $1,000,000",
                    type=VarType.SINGLE, values={1: "Flag", 2: "Not Flag"}))

    path = _parquet(tmp_path, {"SS_AD_SPEND": [50000, 2000000]})
    report = check_against_questionnaire(
        _survey([spend]),
        [QuestionVariableLink(qid="SS_AD_SPEND", variable_names=["SS_AD_SPEND"])],
        path,
        datamap=dm,
    )
    finding = next(f for f in report.findings if f.kind == "coded_in_companion")
    assert finding.severity == "info"
    assert "HID_SS_AD_SPEND" in finding.detail
    assert not any(f.kind == "type_mismatch" for f in report.findings)


# --- non-response values are not a disagreement about the instrument ---------


def test_a_refused_punch_the_questionnaire_never_prints_is_not_a_finding() -> None:
    """Every data file carries "Refused"; no questionnaire prints it.

    Matching non-response by code alone only works when the file uses the
    conventional numbers. A label-encoded export has no conventional numbers at
    all, so each refusal became a finding a reviewer had to dismiss by hand.
    """
    from src.engines.qc.discrepancy import _non_response_codes

    labelled = [
        (1, "Much better off"),
        (2, "About the same"),
        (3, "Refused"),
        (4, "Don\u2019t know"),
        (5, "Prefer not to answer"),
        (6, "(Not asked)"),
        (7, "Don't know / Refused"),
    ]
    assert _non_response_codes(labelled) == {3, 4, 5, 6, 7}


def test_a_real_answer_is_never_read_as_a_refusal() -> None:
    from src.engines.qc.discrepancy import _non_response_codes

    labelled = [
        (1, "Refused a loan in the past 12 months"),
        (2, "I don't know how to answer surveys"),
        (3, "Not applicable to my situation"),
    ]
    # The WHOLE label has to be the phrase. "Refused a loan" is an answer about
    # credit; "Not applicable to my situation" is a substantive choice.
    assert _non_response_codes(labelled) == set()


def test_a_question_whose_type_we_could_not_read_is_not_judged_on_its_type() -> None:
    """Unknown type defaults to `single`. Do not report the data for that."""
    from src.engines.qc.discrepancy import _type_is_unresolved

    known = Question(qid="Q1", text="t", type=QuestionType.SINGLE, options=[])
    unread = Question(
        qid="Q2",
        text="t",
        type=QuestionType.SINGLE,
        options=[],
        validation={"unresolved_fields": ["type"]},
    )
    assert _type_is_unresolved(known) is False
    assert _type_is_unresolved(unread) is True


def test_an_under_18_who_answered_the_rest_of_the_survey_is_caught(tmp_path):
    """The screener case a DP shop is actually paid to catch.

    "TERMINATE IF UNDER 18" is a promise about every record in the file: the
    punch must be consistent with the age, and anyone the age screens out must
    have stopped there. Row 2 is 16 and answered three more questions; row 4 is
    16 and stopped, which is what a correctly-scripted terminate looks like.
    """
    questions = [
        Question(
            qid="AGE",
            text="What is your age?",
            type=QuestionType.NUMERIC,
            terminate_if=Expression.from_dict(
                {
                    "op": "lt",
                    "qid": "AGE",
                    "value": 18,
                    "verbatim_text": "TERMINATE IF UNDER 18",
                }
            ),
        ),
        Question(qid="Q2", text="Which brands do you know?", type=QuestionType.NUMERIC),
        Question(qid="Q3", text="How often do you buy?", type=QuestionType.NUMERIC),
    ]
    path = _parquet(
        tmp_path,
        {
            "AGE": [34, 16, 52, 16],
            "Q2": [1, 2, 1, None],
            "Q3": [3, 4, 2, None],
        },
    )
    links = [
        QuestionVariableLink(qid=q, variable_names=[q]) for q in ("AGE", "Q2", "Q3")
    ]

    report = check_against_questionnaire(_survey(questions), links, path)

    violated = next(f for f in report.findings if f.kind == "terminate_violated")
    assert violated.severity == "error"
    assert violated.n_rows == 1
    assert violated.example_rows == [2]  # the 16-year-old who kept going

    held = next(f for f in report.findings if f.kind == "terminate_recorded")
    assert held.n_rows == 1
    assert held.example_rows == [4]  # the 16-year-old who stopped

    # And the check is reported as having run, so a clean file cannot be
    # confused with an unchecked one.
    assert report.checked.get("terminate") == 1


def test_an_age_stored_as_text_still_screens_out(tmp_path):
    """A CSV of ages mixed with "Refused" imports as text, not numbers."""
    questions = [
        Question(
            qid="AGE",
            text="What is your age?",
            type=QuestionType.NUMERIC,
            terminate_if=Expression.from_dict({"op": "lt", "qid": "AGE", "value": 18}),
        ),
        Question(qid="Q2", text="Which brands?", type=QuestionType.NUMERIC),
    ]
    # Written directly: `_parquet` types every column INTEGER, and the whole
    # point of this case is a column that is not one.
    import polars as pl

    path = tmp_path / "text_age.parquet"
    pl.DataFrame({"AGE": ["34", "16", "Refused"], "Q2": [1, 2, 3]}).write_parquet(path)
    links = [QuestionVariableLink(qid=q, variable_names=[q]) for q in ("AGE", "Q2")]

    report = check_against_questionnaire(_survey(questions), links, path)

    violated = next(f for f in report.findings if f.kind == "terminate_violated")
    assert violated.example_rows == [2]


# --- masking and piping, per record ------------------------------------------


def _masked(mode: str, source="Q1", exclude=None) -> Question:
    return Question(
        qid="Q2",
        text="Which of those do you use most?",
        type=QuestionType.MULTI,
        options=[Option(code=c, label=f"Brand {c}") for c in (1, 2, 3)],
        validation={
            "techniques": {
                "masks": [
                    {"source_qid": source, "mode": mode, "exclude_qid": exclude,
                     "verbatim": "SHOW ONLY ITEMS SELECTED AT Q1"}
                ],
                "pipes": [],
                "least_fill": False,
            }
        },
    )


_AWARE = Question(
    qid="Q1",
    text="Which brands are you aware of?",
    type=QuestionType.MULTI,
    options=[Option(code=c, label=f"Brand {c}") for c in (1, 2, 3)],
)
_MASK_LINKS = [
    QuestionVariableLink(qid="Q1", variable_names=["Q1_1", "Q1_2", "Q1_3"]),
    QuestionVariableLink(qid="Q2", variable_names=["Q2_1", "Q2_2", "Q2_3"]),
]


def _mask_parquet(tmp_path):
    """Row 1 answers inside the mask; row 2 answers an option it never chose."""
    import polars as pl

    path = tmp_path / "mask.parquet"
    pl.DataFrame(
        {
            "Q1_1": [1, 1], "Q1_2": [0, 0], "Q1_3": [0, 0],
            "Q2_1": [1, 0], "Q2_2": [0, 1], "Q2_3": [0, 0],
        }
    ).write_parquet(path)
    return path


def test_an_answer_outside_a_selected_mask_is_found(tmp_path):
    report = check_against_questionnaire(
        _survey([_AWARE, _masked("selected")]), _MASK_LINKS, _mask_parquet(tmp_path)
    )
    finding = next(f for f in report.findings if f.kind == "mask_violated")
    assert finding.example_rows == [2]
    assert "did not choose there" in finding.detail


def test_a_not_selected_mask_is_the_same_question_from_the_other_side(tmp_path):
    """"SHOW ITEMS NOT SELECTED AT Q1" was skipped entirely before."""
    report = check_against_questionnaire(
        _survey([_AWARE, _masked("not_selected")]), _MASK_LINKS, _mask_parquet(tmp_path)
    )
    finding = next(f for f in report.findings if f.kind == "mask_violated")
    assert finding.example_rows == [1]  # row 1 answered exactly what it chose at Q1
    assert report.checked.get("mask") == 1


def test_a_full_list_mask_says_it_was_not_checked(tmp_path):
    """Everyone sees the same list, so there is no per-respondent rule to test."""
    report = check_against_questionnaire(
        _survey([_AWARE, _masked("full_list")]), _MASK_LINKS, _mask_parquet(tmp_path)
    )
    assert not [f for f in report.findings if f.kind == "mask_violated"]
    assert any("full list" in s for s in report.skipped)
    assert "mask" not in report.checked


def test_a_question_piping_from_an_unanswered_question_is_found(tmp_path):
    """The respondent was shown "How satisfied are you with ?" — with a hole."""
    import polars as pl

    piped = Question(
        qid="Q9",
        text="What do you like about [INSERT RESPONSE FROM Q8]?",
        type=QuestionType.OPEN_TEXT,
        validation={
            "techniques": {
                "masks": [],
                "pipes": [{"source_qid": "Q8", "name": None,
                           "verbatim": "INSERT RESPONSE FROM Q8"}],
                "least_fill": False,
            }
        },
    )
    source = Question(qid="Q8", text="Favourite brand?", type=QuestionType.SINGLE)
    path = tmp_path / "pipe.parquet"
    pl.DataFrame({"Q8": [1, None, 2], "Q9": [1, 1, None]}).write_parquet(path)
    links = [
        QuestionVariableLink(qid="Q8", variable_names=["Q8"]),
        QuestionVariableLink(qid="Q9", variable_names=["Q9"]),
    ]

    report = check_against_questionnaire(_survey([source, piped]), links, path)

    finding = next(f for f in report.findings if f.kind == "pipe_source_empty")
    assert finding.example_rows == [2]
    assert report.checked.get("pipe") == 1


def test_a_pipe_the_document_names_in_scripter_terms_is_reported_not_passed(tmp_path):
    import polars as pl

    piped = Question(
        qid="Q9",
        text="What do you like about [PHARMACY]?",
        type=QuestionType.OPEN_TEXT,
        validation={
            "techniques": {
                "masks": [],
                "pipes": [{"source_qid": None, "name": "PHARMACY", "verbatim": "[PHARMACY]"}],
                "least_fill": False,
            }
        },
    )
    path = tmp_path / "pipe2.parquet"
    pl.DataFrame({"Q9": [1, 1]}).write_parquet(path)

    report = check_against_questionnaire(
        _survey([piped]), [QuestionVariableLink(qid="Q9", variable_names=["Q9"])], path
    )

    assert not [f for f in report.findings if f.kind == "pipe_source_empty"]
    assert any("PHARMACY" in s for s in report.skipped)

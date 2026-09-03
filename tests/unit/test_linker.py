"""Question<->variable linking and routing<->data cross-validation.

Synthetic parquet fixtures follow the codebase pattern from
`test_crosstab.py` (build a pyarrow table, write with `pyarrow.parquet`).
"""

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.core.models import DataMap, Variable, VarType
from src.core.survey_definition import (
    Expression,
    Option,
    Question,
    QuestionType,
    QuestionVariableLink,
    SurveyDefinition,
)
from src.engines.questionnaire.linker import (
    LABEL_MATCH_THRESHOLD,
    cross_validate_routing,
    link_questions_to_datamap,
)

# --- linking tests ----------------------------------------------------------


def test_exact_name_match():
    survey = SurveyDefinition(
        study_id="s1",
        questions=[Question(qid="gender", text="What is your gender?")],
    )
    datamap = DataMap([Variable("gender", "Gender", VarType.SINGLE, {1: "M", 2: "F"})])

    report = link_questions_to_datamap(survey, datamap)

    assert len(report.links) == 1
    link = report.links[0]
    assert link.qid == "gender"
    assert link.variable_names == ["gender"]
    assert link.match_method == "exact"
    assert link.confidence == 1.0
    assert report.unmatched_questions == []
    assert report.unmatched_variables == []


def test_normalized_case_match_is_exact():
    survey = SurveyDefinition(study_id="s1", questions=[Question(qid="Q1", text="Age")])
    datamap = DataMap([Variable("q1", "Age", VarType.NUMERIC)])

    report = link_questions_to_datamap(survey, datamap)

    assert report.links[0].variable_names == ["q1"]
    # Case-only difference is still an identity match -> "exact".
    assert report.links[0].match_method == "exact"


def test_multi_punch_suffix_pattern():
    """qid Q5 claims Q5r1/Q5r2/Q5r3 as ONE link with three variable_names."""
    survey = SurveyDefinition(
        study_id="s1",
        questions=[Question(qid="Q5", text="Which brands?", type=QuestionType.MULTI)],
    )
    datamap = DataMap(
        [
            Variable("Q5r1", "Brand A", VarType.MULTI),
            Variable("Q5r2", "Brand B", VarType.MULTI),
            Variable("Q5r3", "Brand C", VarType.MULTI),
        ]
    )

    report = link_questions_to_datamap(survey, datamap)

    assert len(report.links) == 1
    link = report.links[0]
    assert link.qid == "Q5"
    assert sorted(link.variable_names) == ["Q5r1", "Q5r2", "Q5r3"]
    assert link.match_method == "fuzzy"
    assert report.unmatched_variables == []


def test_underscore_and_letter_suffixes_also_collapse():
    survey = SurveyDefinition(
        study_id="s1",
        questions=[Question(qid="Q7", text="Pick all", type=QuestionType.MULTI)],
    )
    datamap = DataMap(
        [
            Variable("Q7_1", "Opt 1", VarType.MULTI),
            Variable("Q7a", "Opt 2", VarType.MULTI),
        ]
    )

    report = link_questions_to_datamap(survey, datamap)

    assert sorted(report.links[0].variable_names) == ["Q7_1", "Q7a"]


def test_multiletter_recode_not_folded_into_question():
    """Q5net is a genuine recode, not an option of Q5 — it stays unmatched."""
    survey = SurveyDefinition(
        study_id="s1", questions=[Question(qid="Q5", text="Brands?")]
    )
    datamap = DataMap(
        [
            Variable("Q5r1", "Brand A", VarType.MULTI),
            Variable("Q5net", "Any brand (net)", VarType.SINGLE),
        ]
    )

    report = link_questions_to_datamap(survey, datamap)

    assert report.links[0].variable_names == ["Q5r1"]
    assert report.unmatched_variables == ["Q5net"]


def test_fuzzy_label_match_above_threshold():
    survey = SurveyDefinition(
        study_id="s1",
        questions=[
            Question(qid="AWARE", text="Which of these brands have you heard of?")
        ],
    )
    datamap = DataMap(
        [Variable("v_brand_awareness", "Which of these brands have you heard of")]
    )

    report = link_questions_to_datamap(survey, datamap)

    assert len(report.links) == 1
    link = report.links[0]
    assert link.variable_names == ["v_brand_awareness"]
    assert link.match_method == "fuzzy"
    assert LABEL_MATCH_THRESHOLD / 100.0 <= link.confidence <= 1.0


def test_unrelated_pair_falls_into_unmatched():
    survey = SurveyDefinition(
        study_id="s1",
        questions=[Question(qid="AGE", text="What is your age in years?")],
    )
    datamap = DataMap(
        [Variable("region", "In which country do you currently reside?")]
    )

    report = link_questions_to_datamap(survey, datamap)

    assert report.links == []
    assert report.unmatched_questions == ["AGE"]
    assert report.unmatched_variables == ["region"]


def test_variable_claimed_once_no_double_link():
    survey = SurveyDefinition(
        study_id="s1",
        questions=[
            Question(qid="Q1", text="First"),
            Question(qid="Q1x", text="First"),  # same label as Q1
        ],
    )
    datamap = DataMap([Variable("Q1", "First", VarType.SINGLE)])

    report = link_questions_to_datamap(survey, datamap)

    # Q1 takes the variable exactly; Q1x has nothing left to claim.
    assert len(report.links) == 1
    assert report.links[0].qid == "Q1"
    assert report.unmatched_questions == ["Q1x"]


# --- cross-validation tests -------------------------------------------------


def _brand_survey():
    """Q1 gender screener; Q2 shown only if Q1 == 1 (males)."""
    return SurveyDefinition(
        study_id="s1",
        questions=[
            Question(
                qid="Q1",
                text="Gender",
                type=QuestionType.SINGLE,
                options=[Option(1, "Male"), Option(2, "Female")],
            ),
            Question(
                qid="Q2",
                text="Male-only follow-up",
                type=QuestionType.NUMERIC,
                show_if=Expression(op="eq", qid="Q1", value=1),
            ),
        ],
    )


_LINKS = [
    QuestionVariableLink(qid="Q1", variable_names=["Q1"]),
    QuestionVariableLink(qid="Q2", variable_names=["Q2"]),
]


def test_routing_agrees_with_data_not_flagged(tmp_path):
    # Q2 answered exactly by the males (Q1==1); everyone else has Q2 missing.
    q1 = [1, 1, 2, 2, 1]
    q2 = [10, 20, None, None, 30]  # answered iff male
    path = tmp_path / "data.parquet"
    pq.write_table(pa.table({"Q1": q1, "Q2": q2}), str(path))

    results = cross_validate_routing(_brand_survey(), _LINKS, path)

    assert len(results) == 1
    r = results[0]
    assert r.qid == "Q2"
    assert r.agreement_pct == 100.0
    assert r.flagged is False


def test_routing_disagrees_with_data_flagged(tmp_path):
    # Deliberately WRONG: Q2 was answered by females too (Q1==2), which the
    # show_if (Q1==1) says should NOT have been shown -> low agreement.
    q1 = [1, 2, 2, 2, 2]
    q2 = [10, 20, 30, 40, 50]  # everyone answered, but 4 are female
    path = tmp_path / "data.parquet"
    pq.write_table(pa.table({"Q1": q1, "Q2": q2}), str(path))

    results = cross_validate_routing(_brand_survey(), _LINKS, path)

    r = results[0]
    assert r.qid == "Q2"
    # Only 1 of 5 answered rows satisfies show_if.
    assert r.agreement_pct == pytest.approx(20.0)
    assert r.flagged is True
    assert "should have been skipped" in r.detail


def test_no_parquet_data_does_not_crash(tmp_path):
    missing = tmp_path / "nope.parquet"
    results = cross_validate_routing(_brand_survey(), _LINKS, missing)
    assert results == []


def test_question_without_show_if_is_not_checked(tmp_path):
    survey = SurveyDefinition(
        study_id="s1", questions=[Question(qid="Q1", text="Gender")]
    )
    links = [QuestionVariableLink(qid="Q1", variable_names=["Q1"])]
    path = tmp_path / "data.parquet"
    pq.write_table(pa.table({"Q1": [1, 2]}), str(path))

    results = cross_validate_routing(survey, links, path)
    assert results == []


def test_show_if_referencing_unlinked_qid_is_reported_as_unchecked(tmp_path):
    """Q2's base tests Q1, and Q1 has no column — so the base cannot be checked.

    Dropping it silently let a base nobody could verify read exactly like a
    base that held. It is returned with `checked=False` instead, and its
    agreement percentage is not a score anyone should read.
    """
    survey = _brand_survey()
    links = [QuestionVariableLink(qid="Q2", variable_names=["Q2"])]
    path = tmp_path / "data.parquet"
    pq.write_table(pa.table({"Q2": [10, None]}), str(path))

    results = cross_validate_routing(survey, links, path)

    assert [r.qid for r in results] == ["Q2"]
    assert results[0].checked is False
    assert results[0].flagged is False
    assert "Q1" in results[0].detail

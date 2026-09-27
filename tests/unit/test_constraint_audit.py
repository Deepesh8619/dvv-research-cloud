"""A constraint nobody can satisfy fails where nobody looks.

A dead CONDITION reports zero violations, which is why it is audited before
fielding. A dead CONSTRAINT is worse: the respondent meets it, cannot get past
it, and abandons — which arrives as drop-off on a page, reads as a boring
question rather than a bug, and has already cost the interview.
"""

from src.core.survey_definition import (
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
)
from src.engines.questionnaire.constraint_audit import audit_constraints

_THREE = [Option(code=i, label=f"Brand {i}") for i in (1, 2, 3)]


def _survey(*questions: Question) -> SurveyDefinition:
    return SurveyDefinition(study_id="t", questions=list(questions))


def _kinds(survey) -> list[str]:
    return sorted(i.kind for i in audit_constraints(survey))


def test_a_floor_above_the_ceiling_rejects_every_possible_answer():
    survey = _survey(Question(
        qid="Q1", text="t", type=QuestionType.MULTI, options=_THREE,
        validation={"min_select": 3, "max_select": 2},
    ))
    assert _kinds(survey) == ["impossible_select"]
    assert audit_constraints(survey)[0].severity == "fail"


def test_a_floor_above_the_option_count_strands_everybody():
    survey = _survey(Question(
        qid="Q1", text="t", type=QuestionType.MULTI, options=_THREE,
        validation={"min_select": 5},
    ))
    assert _kinds(survey) == ["floor_exceeds_list"]


def test_a_cap_larger_than_the_list_is_a_warning_not_a_failure():
    """It wastes nothing — it just promises a choice the list cannot offer."""
    survey = _survey(Question(
        qid="Q1", text="t", type=QuestionType.MULTI, options=_THREE,
        validation={"max_select": 5},
    ))
    issues = audit_constraints(survey)
    assert [i.kind for i in issues] == ["cap_exceeds_list"]
    assert issues[0].severity == "warn"


def test_an_inverted_numeric_range_rejects_every_number():
    survey = _survey(Question(
        qid="Q1", text="t", type=QuestionType.NUMERIC,
        validation={"min": 100, "max": 10},
    ))
    assert _kinds(survey) == ["inverted_range"]


def test_a_way_out_the_range_refuses_is_a_dead_end():
    """"99 — Prefer not to answer" beside a box that only accepts 0-50.

    The respondent is offered an out and then told the out is invalid, which is
    the one place a survey can trap somebody who is trying to cooperate.
    """
    survey = _survey(Question(
        qid="Q1", text="How many?", type=QuestionType.NUMERIC,
        options=[Option(code=99, label="Prefer not to answer")],
        validation={"min": 0, "max": 50},
    ))
    issues = audit_constraints(survey)
    assert [i.kind for i in issues] == ["blocked_opt_out"]
    assert "99" in issues[0].detail


def test_an_opt_out_inside_the_range_is_fine():
    survey = _survey(Question(
        qid="Q1", text="How many?", type=QuestionType.NUMERIC,
        options=[Option(code=99, label="Prefer not to answer")],
        validation={"min": 0, "max": 100},
    ))
    assert audit_constraints(survey) == []


def test_an_ordinary_questionnaire_reports_nothing():
    """The check has to stay quiet on a survey that is fine, or it is noise
    and a reviewer learns to scroll past the panel."""
    survey = _survey(
        Question(qid="Q1", text="t", type=QuestionType.MULTI, options=_THREE,
                 validation={"max_select": 2}),
        Question(qid="Q2", text="t", type=QuestionType.SINGLE, options=_THREE),
        Question(qid="Q3", text="t", type=QuestionType.NUMERIC,
                 validation={"min": 18, "max": 99}),
        Question(qid="Q4", text="Intro", type=QuestionType.INFO),
    )
    assert audit_constraints(survey) == []

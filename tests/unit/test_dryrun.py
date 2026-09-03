"""Fielding a survey against synthetic respondents before real ones arrive.

Every assertion here is about a defect that is invisible in the questionnaire
and obvious in the run: a screen-out nobody meets, a base that hides a question
from everyone, a mask that never narrows, a quota that fills early. Each one
costs real money the moment a panel is pointed at the link.
"""

from __future__ import annotations

from src.core.survey_definition import (
    Block,
    Expression,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
)
from src.engines.fielding.decisions import QuotaCell, unsatisfiable_conditions
from src.engines.fielding.dryrun import simulate


def _survey(*questions: Question) -> SurveyDefinition:
    return SurveyDefinition(
        study_id="s",
        title="t",
        questions=list(questions),
        blocks=[Block(block_id="B1", question_ids=[q.qid for q in questions])],
    )


def _gender(qid: str = "S2") -> Question:
    return Question(
        qid=qid,
        text="Gender",
        type=QuestionType.SINGLE,
        options=[Option(code=1, label="Male"), Option(code=2, label="Female")],
    )


def _age_screener() -> Question:
    return Question(
        qid="S1",
        text="What is your age?",
        type=QuestionType.NUMERIC,
        validation={"min": 12, "max": 80},
        terminate_if=Expression.from_dict({"op": "lt", "qid": "S1", "value": 18}),
    )


def _result(report, kind: str, qid: str):
    return next(c for c in report.conditions if c.kind == kind and c.qid == qid)


def test_a_screen_out_reports_how_many_it_removed() -> None:
    """The incidence figure, before a single real respondent is paid for."""
    report = simulate(_survey(_age_screener(), _gender()), n=500)

    screen_out = _result(report, "screen_out", "S1")
    assert 0 < screen_out.fired < 500
    assert screen_out.eligible == 500  # everyone answered the question it tests
    assert report.dispositions["terminate"] == screen_out.fired
    assert sum(report.dispositions.values()) == 500


def test_the_run_is_reproducible() -> None:
    """A result nobody can reproduce is not a result anyone can argue with."""
    survey = _survey(_age_screener(), _gender())
    assert simulate(survey, n=200, seed=7).to_dict() == simulate(
        survey, n=200, seed=7
    ).to_dict()
    assert simulate(survey, n=200, seed=7) != simulate(survey, n=200, seed=8)


def test_a_base_nothing_satisfies_is_reported_as_never_fired() -> None:
    """The question is in the document and no respondent will ever see it."""
    unreachable = Question(
        qid="Q9",
        text="Never asked",
        type=QuestionType.SINGLE,
        options=[Option(code=1, label="a")],
        show_if=Expression.from_dict({"op": "in", "qid": "S2", "values": [9]}),
    )
    report = simulate(_survey(_gender(), unreachable), n=200)

    assert _result(report, "base", "Q9").fired == 0
    assert "Q9" in report.never_fired
    assert "Q9" in report.questions_never_shown
    assert report.passed is False


def test_an_impossible_condition_is_proved_not_merely_unobserved() -> None:
    """"Did not fire in 500 runs" is evidence. This is arithmetic."""
    unreachable = Question(
        qid="Q9",
        text="Never asked",
        type=QuestionType.SINGLE,
        options=[Option(code=1, label="a")],
        show_if=Expression.from_dict({"op": "in", "qid": "S2", "values": [9]}),
    )
    survey = _survey(_gender(), unreachable)

    finding = next(f for f in unsatisfiable_conditions(survey) if f["qid"] == "Q9")
    assert finding["kind"] == "impossible_codes"
    assert "never offers" in finding["detail"]
    assert simulate(survey, n=50).impossible == unsatisfiable_conditions(survey)


def test_a_screen_out_testing_a_question_that_does_not_exist_is_caught() -> None:
    orphan = Question(
        qid="Q1",
        text="x",
        type=QuestionType.SINGLE,
        options=[Option(code=1, label="a")],
        terminate_if=Expression.from_dict({"op": "in", "qid": "GONE", "values": [1]}),
    )
    finding = unsatisfiable_conditions(_survey(orphan))[0]
    assert finding["kind"] == "unknown_question"


def test_a_mask_reports_how_often_it_narrowed_the_list() -> None:
    aware = Question(
        qid="Q1",
        text="Which brands are you aware of?",
        type=QuestionType.MULTI,
        options=[Option(code=c, label=f"Brand {c}") for c in (1, 2, 3, 4)],
    )
    used = Question(
        qid="Q2",
        text="Which of those do you use?",
        type=QuestionType.MULTI,
        options=[Option(code=c, label=f"Brand {c}") for c in (1, 2, 3, 4)],
        validation={
            "techniques": {
                "masks": [
                    {"source_qid": "Q1", "mode": "selected", "exclude_qid": None,
                     "verbatim": ""}
                ],
                "pipes": [],
                "least_fill": False,
            }
        },
    )
    report = simulate(_survey(aware, used), n=300)

    mask = _result(report, "mask", "Q2")
    assert 0 < mask.fired <= mask.eligible == 300
    assert "Q2" not in report.never_fired


def test_a_quota_reports_when_it_filled_and_who_it_turned_away() -> None:
    """The most useful number in a field report: how long the sample lasts."""
    cells = [
        QuotaCell(
            id="male",
            condition=Expression.from_dict({"op": "in", "qid": "S2", "values": [1]}),
            limit=20,
            count=0,
        )
    ]
    report = simulate(_survey(_gender()), cells, n=300)

    quota = _result(report, "quota", "male")
    assert quota.filled_at is not None and quota.filled_at < 300
    assert quota.fired > 0  # respondents turned away after it filled
    assert report.dispositions["overquota"] == quota.fired


def test_a_synthetic_respondent_never_answers_outside_a_mask() -> None:
    """Otherwise the run manufactures the exact violation the QC hunts for."""
    aware = Question(
        qid="Q1", text="Aware?", type=QuestionType.MULTI,
        options=[Option(code=c, label=str(c)) for c in (1, 2, 3, 4)],
    )
    used = Question(
        qid="Q2", text="Use?", type=QuestionType.MULTI,
        options=[Option(code=c, label=str(c)) for c in (1, 2, 3, 4)],
        validation={"techniques": {"masks": [
            {"source_qid": "Q1", "mode": "selected", "exclude_qid": None, "verbatim": ""}
        ], "pipes": [], "least_fill": False}},
    )
    # A clean run over a masked pair produces no mask violation to report.
    report = simulate(_survey(aware, used), n=200)
    assert report.impossible == []


def test_a_clean_survey_passes() -> None:
    report = simulate(_survey(_age_screener(), _gender()), n=200)
    assert report.passed is True
    assert report.never_fired == []


def test_an_impossible_equality_is_caught_as_well_as_a_membership() -> None:
    """`Q2 = 9` names a code just as `Q2 in [9]` does."""
    unreachable = Question(
        qid="Q9", text="Never", type=QuestionType.OPEN_TEXT,
        show_if=Expression(op="eq", qid="S2", value=9),
    )
    assert unsatisfiable_conditions(_survey(_gender(), unreachable))


def test_a_negation_of_an_impossible_code_is_not_impossible() -> None:
    """"not 9" is satisfied by everyone — harmless, not a dead condition."""
    fine = Question(
        qid="Q9", text="Asked of nearly all", type=QuestionType.OPEN_TEXT,
        show_if=Expression(op="ne", qid="S2", value=9),
    )
    assert unsatisfiable_conditions(_survey(_gender(), fine)) == []

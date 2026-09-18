"""`path_to`: the answers that take one respondent to a target question the way
a qualifying respondent would — the engine behind jump-to-question on a test
link. See src/engines/fielding/dryrun.py's docstring for the contract.
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
from src.engines.fielding.dryrun import path_to


def _survey(*questions: Question) -> SurveyDefinition:
    return SurveyDefinition(
        study_id="s",
        title="t",
        questions=list(questions),
        blocks=[Block(block_id="B1", question_ids=[q.qid for q in questions])],
    )


def _age_screener() -> Question:
    return Question(
        qid="S1",
        text="What is your age?",
        type=QuestionType.NUMERIC,
        terminate_if=Expression.from_dict({"op": "lt", "qid": "S1", "value": 18}),
    )


def _gender() -> Question:
    return Question(
        qid="S2",
        text="Gender",
        type=QuestionType.SINGLE,
        options=[Option(code=1, label="Male"), Option(code=2, label="Female")],
    )


def _male_only_question() -> Question:
    """A base gated behind S2 — only reachable for a respondent who said Male."""
    return Question(
        qid="Q1",
        text="Which razor brand?",
        type=QuestionType.SINGLE,
        options=[Option(code=1, label="Gillette"), Option(code=2, label="Harry's")],
        show_if=Expression.from_dict({"op": "eq", "qid": "S2", "value": 1}),
    )


def _survey_with_screener_and_base() -> SurveyDefinition:
    return _survey(_age_screener(), _gender(), _male_only_question())


def test_reaches_a_question_behind_a_screener_and_a_base() -> None:
    """Q1 sits behind both the age screener and the male-only base — a path
    must clear both: S1 >= 18 (not terminated) and S2 == 1 (Q1 visible)."""
    survey = _survey_with_screener_and_base()
    result = path_to(survey, "Q1")
    assert result is not None
    answers, _kept = result
    assert answers["S1"] >= 18
    assert answers["S2"] == 1
    assert "Q1" not in answers  # the target itself is never answered by the path
    from src.engines.fielding.decisions import is_visible, terminating_question

    assert terminating_question(survey, answers) is None
    assert is_visible(survey["Q1"], answers)


def test_keeps_a_tester_answer_that_still_allows_the_target() -> None:
    """A tester who already answered S2=1 (Male) keeps that answer — it still
    lets the target be reached, so a fresh path is not manufactured instead."""
    survey = _survey_with_screener_and_base()
    result = path_to(survey, "Q1", given={"S2": 1})
    assert result is not None
    answers, kept = result
    assert kept is True
    assert answers["S2"] == 1


def test_replaces_a_tester_answer_that_would_screen_out() -> None:
    """A tester who answered S1=10 (under 18) would never reach Q1 — that
    answer is dropped and a fresh path is built instead."""
    survey = _survey_with_screener_and_base()
    result = path_to(survey, "Q1", given={"S1": 10})
    assert result is not None
    answers, kept = result
    assert kept is False
    assert answers["S1"] >= 18


def test_returns_none_when_the_base_can_never_be_met() -> None:
    """Q2's base tests a code S2 never offers — nobody can ever be shown it,
    so no answer path can reach it either."""
    unreachable = Question(
        qid="Q2",
        text="Never asked",
        type=QuestionType.OPEN_TEXT,
        show_if=Expression.from_dict({"op": "eq", "qid": "S2", "value": 9}),
    )
    survey = _survey(_gender(), unreachable)
    assert path_to(survey, "Q2") is None


def test_returns_none_for_an_unknown_qid() -> None:
    survey = _survey_with_screener_and_base()
    assert path_to(survey, "NOPE") is None

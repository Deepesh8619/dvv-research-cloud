"""Two defects that made a fielding link unusable.

Both were invisible from the questionnaire review screen and only appeared when
a real respondent opened the link.
"""

from __future__ import annotations

from src.core.survey_definition import (
    Block,
    Expression,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
    evaluate,
)
from src.renderers.surveyjs_compiler import compile_to_surveyjs

# --- 1. Every respondent was screened out on their first submit -------------


def _negated_terminate() -> Expression:
    """How "TERMINATE if 1 not selected" is stored: not(in ...)."""
    return Expression.from_dict(
        {"op": "not", "children": [{"op": "in", "qid": "S1", "values": [1]}]}
    )


def test_a_negated_terminate_is_true_before_anything_is_answered():
    """The trap. `in` is false against an empty answer set, and `not false` is
    true — so the screen-out fires on a respondent who has answered nothing."""
    assert evaluate(_negated_terminate(), {}) is True


def test_the_guard_is_whether_every_referenced_question_was_answered():
    """The fix applied in the fielding route, asserted directly.

    A terminate must not be judged until the questions it depends on have
    actually been answered — the same rule the quota check already used.
    """
    expression = _negated_terminate()
    answers_so_far: dict = {}
    decidable = expression.referenced_qids().issubset(answers_so_far.keys())
    assert decidable is False, "must not be judged yet"

    answered = {"S1": 1}
    assert expression.referenced_qids().issubset(answered.keys()) is True
    assert evaluate(expression, answered) is False  # picked 1 -> continue

    screened = {"S1": 2}
    assert evaluate(expression, screened) is True  # did not -> screen out


# --- 2. Scripter-only content was being shown to respondents ---------------


def _survey(questions: list[Question]) -> SurveyDefinition:
    return SurveyDefinition(
        study_id="s1",
        title="T",
        source="extracted",
        status="approved",
        blocks=[Block(block_id="B1", question_ids=[q.qid for q in questions])],
        questions=questions,
    )


def test_a_derived_variable_is_never_rendered_to_a_respondent():
    """"CODE AGE - BASED ON RESPONSES AT S1" is an instruction to the scripter.
    A member of the public must never see it."""
    survey = _survey([
        Question(
            qid="C1",
            text="CODE AGE - BASED ON RESPONSES AT S1",
            type=QuestionType.INFO,
            flags=["DERIVED_VARIABLE"],
        ),
        Question(
            qid="Q1",
            text="How old are you?",
            type=QuestionType.NUMERIC,
        ),
    ])
    elements = compile_to_surveyjs(survey)["pages"][0]["elements"]
    rendered = " ".join(str(e) for e in elements)
    assert "CODE AGE" not in rendered
    assert len(elements) == 1
    assert elements[0]["name"] == "Q1"


def test_genuine_display_text_is_still_shown():
    """`info` alone must not exclude — an intro screen is info and belongs in
    the survey. Only the scripter-only flag removes it."""
    survey = _survey([
        Question(qid="INTRO", text="Thank you for taking part.", type=QuestionType.INFO),
        Question(
            qid="Q1",
            text="Pick one",
            type=QuestionType.SINGLE,
            options=[Option(code=1, label="A"), Option(code=2, label="B")],
        ),
    ])
    elements = compile_to_surveyjs(survey)["pages"][0]["elements"]
    assert len(elements) == 2
    assert elements[0]["type"] == "html"
    assert "Thank you" in elements[0]["html"]


# --- 3. Routing that exists only as prose is not applied in the field -------


def test_a_question_whose_routing_is_only_prose_is_named_before_launch():
    """The silent failure this prevents.

    When extraction cannot resolve "ASK IF AWARE OF BRAND AT Q4" into a
    question and codes, it keeps the sentence so a human can act on it. A
    sentence cannot be evaluated, so the question is shown to EVERY
    respondent — the questionnaire looks routed, the data comes back wrong,
    and nothing in between says so.
    """
    from api.routes.fielding import _unenforced_routing

    routed = Question(
        qid="Q5",
        text="Which brand do you prefer?",
        type=QuestionType.SINGLE,
        options=[Option(code=1, label="A")],
        validation={"routing_verbatim": "ASK IF AWARE OF BRAND AT Q4"},
    )
    plain = Question(qid="Q1", text="How old are you?", type=QuestionType.NUMERIC)
    assert _unenforced_routing(_survey([routed, plain])) == ["Q5"]


def test_a_question_with_real_structured_routing_is_not_flagged():
    """Resolved routing IS compiled and enforced — it must not be warned about."""
    from api.routes.fielding import _unenforced_routing

    resolved = Question(
        qid="Q5",
        text="Which brand?",
        type=QuestionType.SINGLE,
        options=[Option(code=1, label="A")],
        show_if=Expression.from_dict({"op": "in", "qid": "Q4", "values": [1]}),
        validation={"routing_verbatim": "ASK IF AWARE OF BRAND AT Q4"},
    )
    assert _unenforced_routing(_survey([resolved])) == []


def test_prose_routing_really_does_not_reach_the_respondent():
    """Proves the premise rather than assuming it: no visibleIf is emitted."""
    routed = Question(
        qid="Q5",
        text="Which brand do you prefer?",
        type=QuestionType.SINGLE,
        options=[Option(code=1, label="A")],
        validation={"routing_verbatim": "ASK IF AWARE OF BRAND AT Q4"},
    )
    element = compile_to_surveyjs(_survey([routed]))["pages"][0]["elements"][0]
    assert "visibleIf" not in element

"""A question asked about EACH of several things is a grid, not one answer.

Q4a of the live pharmacy study: "…have you ever personally used each one?"
with Yes and No underneath. Two answers, thirteen pharmacies, and one data
column where there should be thirteen.
"""

from src.core.survey_definition import (
    Block,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
)
from src.engines.questionnaire.per_item import apply


def _survey(*questions: Question) -> SurveyDefinition:
    return SurveyDefinition(
        study_id="T", source="extracted", status="approved",
        blocks=[Block(block_id="B", question_ids=[q.qid for q in questions])],
        questions=list(questions),
    )


def test_each_one_answered_yes_no_becomes_a_grid_waiting_for_its_rows():
    q4a = Question(
        qid="Q4a",
        text="Looking again at these pharmacies you've heard of — have you ever "
             "personally used each one?",
        type=QuestionType.SINGLE,
        options=[Option(code=1, label="Yes"), Option(code=2, label="No")],
    )
    assert apply(_survey(q4a)) == ["Q4a"]
    assert q4a.type is QuestionType.GRID_SINGLE
    assert [o.label for o in q4a.cols] == ["Yes", "No"]
    assert q4a.rows == []          # fill_lists supplies them from Q3
    assert q4a.options == []
    assert "EACH" in q4a.extraction_evidence["type"]


def test_a_question_whose_answers_ARE_the_items_is_left_alone():
    """"Which of these pharmacies have you seen advertising for" is answered
    WITH the pharmacy list. Converting it would produce a grid with no rows
    and throw the list away — the failure this file exists to avoid."""
    q4 = Question(
        qid="Q4", text="And which, if any, of these pharmacies have you seen "
                       "advertising for in the past few weeks?",
        type=QuestionType.MULTI,
        options=[Option(code=i, label=n) for i, n in enumerate(
            ["CVS", "Walgreens", "Amazon Pharmacy", "Capsule"], start=1)],
    )
    assert apply(_survey(q4)) == []
    assert q4.type is QuestionType.MULTI and len(q4.options) == 4


def test_wording_that_is_not_per_item_is_left_alone():
    one_choice = Question(
        qid="Q8", text="Which of these pharmacies would you prefer to use?",
        type=QuestionType.SINGLE,
        options=[Option(code=1, label="Yes"), Option(code=2, label="No")],
    )
    assert apply(_survey(one_choice)) == []
    assert one_choice.type is QuestionType.SINGLE


def test_a_grid_that_already_has_its_items_is_not_touched():
    grid = Question(
        qid="Q13", text="When did you last use each of these pharmacies?",
        type=QuestionType.SINGLE,
        rows=[Option(code=1, label="CVS")],
        options=[Option(code=1, label="Recently"), Option(code=2, label="Longer ago")],
    )
    assert apply(_survey(grid)) == []


def test_a_long_answer_list_is_never_read_as_a_scale():
    many = Question(
        qid="Q9", text="Which of the following do you use for each of these needs?",
        type=QuestionType.MULTI,
        options=[Option(code=i, label=f"Never {i}") for i in range(1, 15)],
    )
    assert apply(_survey(many)) == []


def test_a_question_mistyped_as_a_grid_still_gets_its_items():
    """Q4a arrived typed grid_single with one answer and neither rows nor
    columns — the same shape wearing a different type. Skipping it on type
    alone left the question with "No" as its only answer."""
    mistyped = Question(
        qid="Q4a", text="…have you ever personally used each one?",
        type=QuestionType.GRID_SINGLE,
        options=[Option(code=1, label="Yes"), Option(code=2, label="No")],
    )
    assert apply(_survey(mistyped)) == ["Q4a"]
    assert [o.label for o in mistyped.cols] == ["Yes", "No"]
    assert mistyped.options == []

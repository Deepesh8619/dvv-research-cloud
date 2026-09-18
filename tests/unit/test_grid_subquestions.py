"""Q2a and Q2b side by side over one brand list are two questions, not a grid."""

from src.core.survey_definition import (
    Block,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
)
from src.engines.questionnaire.grid_subquestions import split_subquestion_grids
from src.renderers.surveyjs_compiler import compile_to_surveyjs

_BRANDS = [Option(code=i, label=b) for i, b in enumerate(["Bubble", "Anua", "Byoma"], 1)]


def _survey(cols: list[str]) -> SurveyDefinition:
    return SurveyDefinition(
        study_id="T",
        source="extracted",
        status="approved",
        blocks=[Block(block_id="B1", question_ids=["Q2"])],
        questions=[
            Question(
                qid="Q2",
                text="Which of these skincare brands have you ....",
                type=QuestionType.GRID_SINGLE,
                rows=list(_BRANDS),
                cols=[Option(code=i, label=c) for i, c in enumerate(cols, 1)],
            )
        ],
    )


def test_columns_that_are_questions_become_questions():
    survey = _survey(["Q2a. ever heard of?", "Q2b. Ever purchased from?"])
    split_subquestion_grids(survey)
    assert [q.qid for q in survey.questions] == ["Q2a", "Q2b"]
    assert survey.blocks[0].question_ids == ["Q2a", "Q2b"]
    heard, bought = survey.questions
    # A brand can be both heard of and bought — one answer per brand was wrong.
    assert heard.type is QuestionType.MULTI
    assert heard.text == "Which of these skincare brands have you ever heard of?"
    assert bought.text == "Which of these skincare brands have you ever purchased from?"
    assert [o.label for o in bought.options] == ["Bubble", "Anua", "Byoma"]


def test_a_scale_grid_stays_a_grid():
    survey = _survey(["Very satisfied", "Satisfied", "Not satisfied"])
    assert split_subquestion_grids(survey) == []
    assert survey.questions[0].type is QuestionType.GRID_SINGLE


def test_an_old_stored_grid_never_shows_the_label():
    """A definition saved before the split still renders — without "Q2a."."""
    pages = compile_to_surveyjs(_survey(["Q2a. ever heard of?", "Q2b. Ever purchased from?"]))
    columns = pages["pages"][0]["elements"][0]["columns"]
    assert [c["text"] for c in columns] == ["ever heard of?", "Ever purchased from?"]

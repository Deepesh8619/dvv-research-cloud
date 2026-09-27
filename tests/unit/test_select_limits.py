"""A cap nobody records is a cap nobody enforces.

"Select up to 2." was carried in the question wording, shown to the respondent
and enforced nowhere — so the respondent could tick the whole list, and the
data came back with more punches than the questionnaire allows. It does not
look like a bug: the sentence is right there on screen.
"""

from src.core.survey_definition import Block, Option, Question, QuestionType, SurveyDefinition
from src.engines.questionnaire.extractor import extract_question
from src.engines.questionnaire.prose_directives import parse_prose_question
from src.engines.questionnaire.select_limits import read_limits
from src.intake.questionnaire_loader import DocumentBlock, segment_into_questions
from src.renderers.surveyjs_compiler import compile_to_surveyjs


def test_the_cap_is_read_from_the_sentence_the_respondent_reads():
    assert read_limits("Select up to 2.") == {"max_select": 2}
    assert read_limits("Please choose up to three brands") == {"max_select": 3}
    assert read_limits("Select your top 3") == {"max_select": 3}
    assert read_limits("Select exactly 3 options") == {"min_select": 3, "max_select": 3}
    assert read_limits("Select at least 2 and no more than 5") == {
        "min_select": 2,
        "max_select": 5,
    }


def test_questions_that_state_no_limit_stay_silent():
    """Most questions say nothing, and a limit invented from the option count
    would cap questions the document deliberately left open."""
    assert read_limits("Which brands do you know?") == {}
    assert read_limits("Select all that apply") == {}
    assert read_limits("Select one") == {}
    assert read_limits("") == {}


def test_a_contradiction_is_preserved_not_resolved():
    """Guessing which bound the scripter meant would field the wrong one."""
    assert read_limits("Select at least 5, up to 2") == {
        "min_select": 5,
        "max_select": 2,
    }


def test_a_cap_types_the_question_without_a_model_call():
    """"Select up to 2" over a list is a multi-punch, and the sentence says so.

    Before this the type was unstated, so a question whose own wording settles
    it was escalated to the language model.
    """
    fields = parse_prose_question(
        "Q1. Which have you used? Select up to 2.\nTMS\nSpravato\nKetamine", "Q1"
    )
    assert fields.qtype is QuestionType.MULTI
    assert fields.select_limits == {"max_select": 2}
    assert "type" not in fields.missing


def test_the_cap_reaches_the_respondent_s_browser():
    survey = SurveyDefinition(
        study_id="t",
        blocks=[Block(block_id="B1", question_ids=["Q1"])],
        questions=[
            Question(
                qid="Q1",
                text="Which have you used?",
                type=QuestionType.MULTI,
                options=[Option(code=i, label=f"Brand {i}") for i in (1, 2, 3)],
                validation={"max_select": 2},
            )
        ],
    )
    element = compile_to_surveyjs(survey)["pages"][0]["elements"][0]
    assert element["maxSelectedChoices"] == 2


def test_grid_wording_about_each_row_is_not_a_cap():
    """"Select one answer per row." — found on a real client grid.

    Read as a cap it says the WHOLE question takes one answer, which is the
    opposite of what a grid is.
    """
    assert read_limits("Select one answer per row.") == {}
    assert read_limits("Please give one response for each statement") == {}


def test_a_grid_never_keeps_a_select_cap_whatever_its_wording_says():
    """The guard has to sit where the type is FINAL, not where the cap is read.

    A grid written as a Word table — which is how real documents write them —
    is still typed as a flat choice list while its prose is parsed, and only
    becomes a grid once the table's shape is applied. A cap recorded before
    that survives onto the grid unless something strips it afterwards.
    """
    blocks = [
        DocumentBlock("paragraph", "Q5. For these brands, select up to 3 that apply.", 0),
        DocumentBlock(
            "table",
            " | [PN: RANDOMIZE ROWS] | Yes | No | Unsure",
            1,
            rows=[
                [" ", "[PN: RANDOMIZE ROWS]", "Yes", "No", "Unsure"],
                [" ", " ", "1", "2", "3"],
                ["1", "Nike", "", "", ""],
                ["2", "Adidas", "", "", ""],
            ],
        ),
    ]
    question = extract_question(
        segment_into_questions(blocks)[0], None, None, None, None
    ).question
    # Guard the premise: the wording really does state a cap, and the prose
    # parser really does record one — otherwise this passes for the wrong reason.
    assert read_limits("Q5. For these brands, select up to 3 that apply.") == {
        "max_select": 3
    }
    assert question.type is QuestionType.GRID_SINGLE
    assert "max_select" not in (question.validation or {})


def _rank_survey(options: int, **validation):
    return SurveyDefinition(
        study_id="t",
        blocks=[Block(block_id="B1", question_ids=["Q7"])],
        questions=[Question(
            qid="Q7", text="Rank your top three.", type=QuestionType.RANK,
            options=[Option(code=i, label=f"Brand {i}") for i in range(1, options + 1)],
            validation=validation or None,
        )],
    )


def test_rank_your_top_three_asks_for_three_not_eleven():
    """Found on a real client questionnaire: "Rank your top three" over 11 brands.

    A plain SurveyJS ranking has no notion of a top N, so the respondent was
    made to order all eleven — which is not what the question asks, and ranks
    4-11 are data the document never requested from anybody.
    """
    assert read_limits("Rank your top three.") == {"max_select": 3}
    element = compile_to_surveyjs(_rank_survey(11, max_select=3))["pages"][0]["elements"][0]
    assert element["selectToRankEnabled"] is True
    assert element["maxSelectedChoices"] == 3


def test_a_ranking_with_no_cap_keeps_the_interaction_it_has_today():
    """The change is gated, so every existing rank question is untouched."""
    element = compile_to_surveyjs(_rank_survey(11))["pages"][0]["elements"][0]
    assert element["type"] == "ranking"
    assert "selectToRankEnabled" not in element
    assert "maxSelectedChoices" not in element


def test_a_cap_that_covers_the_whole_list_changes_nothing():
    """"Rank your top 3" over exactly three options is an ordinary full ranking
    — switching the widget there would be churn for no behaviour change."""
    element = compile_to_surveyjs(_rank_survey(3, max_select=3))["pages"][0]["elements"][0]
    assert "selectToRankEnabled" not in element

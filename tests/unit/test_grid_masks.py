"""A mask whose source is a grid — the shape that offered the respondent nothing.

"ONLY DISPLAY BRANDS AWARE FROM Q2a" where Q2a is a one-row-per-brand grid
compiled to `{Q2a} = {item}`: an object compared to an option code, never
true. The masked question rendered with no answers at all, which reads like a
question nobody wrote options for rather than like a bug.
"""

from src.core.survey_definition import (
    Block,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
)
from src.engines.questionnaire.grid_masks import affirmative_codes, mask_codes
from src.renderers.surveyjs_compiler import compile_to_surveyjs


def _grid(*columns: str) -> Question:
    return Question(
        qid="Q5", text="Which of these pharmacies have you heard of?",
        type=QuestionType.GRID_SINGLE,
        rows=[Option(code=1, label="CVS"), Option(code=2, label="Walgreens")],
        cols=[Option(code=i, label=label) for i, label in enumerate(columns, start=1)],
    )


def test_the_yes_column_is_found_without_being_told():
    assert affirmative_codes(_grid("Yes", "No")) == [1]
    assert affirmative_codes(_grid("No", "Yes")) == [2]


def test_a_frequency_grid_counts_everything_except_never():
    """"Brands they use" means every row they did not answer Never."""
    assert affirmative_codes(_grid("Daily", "Weekly", "Monthly", "Never")) == [1, 2, 3]


def test_a_grid_with_nothing_negative_counts_every_answer():
    """A 0-10 rating per brand: "the ones they rated" is the only reading."""
    assert affirmative_codes(_grid("0", "5", "10")) == [1, 2, 3]


def test_a_grid_where_every_column_reads_negative_still_picks_something():
    """Deciding nobody chose anything would empty the masked list — the exact
    failure this file exists to stop."""
    assert affirmative_codes(_grid("No", "Never")) == [1, 2]


def test_a_mask_reads_the_rows_picked_not_the_columns_answered():
    """Saying Yes to CVS and No to Walgreens means the mask allows CVS. A
    reading that returns the COLUMN codes answers a different question and
    masks the list against numbers that mean nothing to it."""
    grid = _grid("Yes", "No")
    assert mask_codes(grid, {"1": 1, "2": 2}) == {1}
    assert mask_codes(grid, {"1": 1, "2": 1}) == {1, 2}
    assert mask_codes(grid, {"1": 2, "2": 2}) == set()


def test_a_mask_still_reads_a_list_and_a_single_answer():
    grid = _grid("Yes", "No")
    assert mask_codes(grid, [3, 4]) == {3, 4}
    assert mask_codes(grid, 7) == {7}
    assert mask_codes(grid, None) is None


def test_the_masked_question_gets_a_condition_the_browser_can_evaluate():
    source = _grid("Yes", "No")
    ranked = Question(
        qid="Q6", text="Rank your top 3", type=QuestionType.RANK,
        options=[Option(code=1, label="CVS"), Option(code=2, label="Walgreens")],
        validation={"techniques": {"masks": [{"mode": "selected", "source_qid": "Q5"}]}},
    )
    survey = SurveyDefinition(
        study_id="T", source="extracted", status="approved",
        blocks=[Block(block_id="B", question_ids=["Q5", "Q6"])], questions=[source, ranked],
    )
    compiled = compile_to_surveyjs(survey)
    element = {e["name"]: e for p in compiled["pages"] for e in p["elements"] if "name" in e}

    assert element["Q6"]["choicesVisibleIf"] == "{Q5_picked} contains {item}"
    # …and the page is told how to fill that variable, since SurveyJS has no
    # way to say "the rows of Q5 whose cell says Yes".
    assert compiled["gridMaskSources"] == [{"name": "Q5_picked", "qid": "Q5", "positive": [1]}]


def test_a_mask_on_a_multi_select_is_unchanged():
    """The shapes that already worked must keep working, byte for byte."""
    source = Question(qid="Q5", text="Which have you heard of?", type=QuestionType.MULTI,
                      options=[Option(code=1, label="CVS")])
    target = Question(
        qid="Q6", text="Which have you used?", type=QuestionType.MULTI,
        options=[Option(code=1, label="CVS")],
        validation={"techniques": {"masks": [{"mode": "selected", "source_qid": "Q5"}]}},
    )
    survey = SurveyDefinition(
        study_id="T", source="extracted", status="approved",
        blocks=[Block(block_id="B", question_ids=["Q5", "Q6"])], questions=[source, target],
    )
    compiled = compile_to_surveyjs(survey)
    element = {e["name"]: e for p in compiled["pages"] for e in p["elements"] if "name" in e}
    assert element["Q6"]["choicesVisibleIf"] == "{Q5} contains {item}"
    assert "gridMaskSources" not in compiled

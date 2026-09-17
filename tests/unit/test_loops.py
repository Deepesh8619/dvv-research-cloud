"""A question asked once per brand is not one question.

Eight rated statements asked of five brands is forty data columns and five sets
of tabs. Held as one question it is eight columns with no way to tell one
brand's answers from another's.
"""

from src.core.survey_definition import (
    Block,
    Expression,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
)
from src.engines.questionnaire import schema
from src.engines.questionnaire.loops import expand_loops, parse_loop

LOOP_NOTE = (
    "Loop through in randomized order and ask among aware Current or Lapsed "
    "Users for each of these brands: Google Ads, Meta Ads, LinkedIn, Amazon "
    "Ads, and TikTok"
)


def _survey() -> SurveyDefinition:
    template = Question(
        qid="DA_ATTITUDES",
        text="Indicate your degree of agreement with this statement about [INSERT PROVIDER]",
        type=QuestionType.GRID_SINGLE,
        rows=[
            Option(code=1, label="Is a leader in AI tools"),
            Option(code=2, label="Delivers a strong return on investment"),
        ],
        cols=[Option(code=5, label="Strongly Agree"), Option(code=1, label="Strongly Disagree")],
        show_if=Expression(op="in", qid="HID_AWARE", values=[1]),
        validation={"notes": LOOP_NOTE},
    )
    after = Question(qid="Q99", text="Anything else?", type=QuestionType.OPEN_TEXT)
    return SurveyDefinition(
        study_id="T",
        source="extracted",
        status="draft",
        blocks=[Block(block_id="B1", question_ids=["DA_ATTITUDES", "Q99"])],
        questions=[template, after],
    )


def test_a_loop_is_read_from_the_note_that_states_it():
    loop = parse_loop(LOOP_NOTE)
    assert loop is not None
    assert loop.labels == ["Google Ads", "Meta Ads", "LinkedIn", "Amazon Ads", "TikTok"]
    assert loop.randomised is True


def test_a_grids_row_list_is_not_a_loop():
    """"ask for each of the following statements" introduces a grid's rows. Read
    as a loop it would turn one question into one question per statement."""
    assert parse_loop("Ask for each of the following statements: agree, disagree") is None
    assert parse_loop("Please loop through the grid rows in order") is None
    assert parse_loop("Loop for each of these: only one") is None  # one is not a loop


def test_every_iteration_becomes_its_own_question():
    survey = _survey()
    applied = expand_loops(survey)
    assert [q.qid for q in survey.questions] == [
        "DA_ATTITUDES_1",
        "DA_ATTITUDES_2",
        "DA_ATTITUDES_3",
        "DA_ATTITUDES_4",
        "DA_ATTITUDES_5",
        "Q99",
    ]
    assert applied[0]["labels"][1] == "Meta Ads"
    # The block order is the questionnaire's order.
    assert survey.blocks[0].question_ids[:5] == [q.qid for q in survey.questions[:5]]
    assert "DA_ATTITUDES_2" in survey  # addressable, not just present


def test_the_placeholder_is_where_the_name_goes():
    survey = _survey()
    expand_loops(survey)
    second = survey["DA_ATTITUDES_2"]
    assert second.text.endswith("about Meta Ads")
    assert "[INSERT" not in second.text


def test_each_iteration_carries_the_whole_grid():
    survey = _survey()
    expand_loops(survey)
    for index in range(1, 6):
        question = survey[f"DA_ATTITUDES_{index}"]
        assert len(question.rows) == 2 and len(question.cols) == 2
        assert question.validation["loop"]["index"] == index


def test_the_template_is_gone():
    """Left in place it is a question nobody is asked that still emits its own
    columns, and every answer would be double-counted against the iterations
    that replaced it."""
    survey = _survey()
    expand_loops(survey)
    assert "DA_ATTITUDES" not in [q.qid for q in survey.questions]


def test_the_base_is_not_copied_onto_every_brand():
    """The document defines who sees each brand in prose naming several user
    states across two questions. Copying the template's base would ask every
    brand of everybody, and nothing downstream could tell."""
    survey = _survey()
    expand_loops(survey)
    for index in range(1, 6):
        question = survey[f"DA_ATTITUDES_{index}"]
        assert question.show_if is None
        assert schema.FLAG_AMBIGUOUS_ROUTING in question.flags


def test_a_questionnaire_with_no_loop_is_untouched():
    plain = SurveyDefinition(
        study_id="T",
        source="extracted",
        status="draft",
        blocks=[Block(block_id="B1", question_ids=["Q1"])],
        questions=[Question(qid="Q1", text="Favourite colour?",
                            options=[Option(code=1, label="Red")])],
    )
    before = plain.to_json()
    assert expand_loops(plain) == []
    assert plain.to_json() == before

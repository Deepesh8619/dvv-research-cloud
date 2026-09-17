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
    evaluate,
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


BASE_NOTE = (
    "Provider Current or Lapsed User based on DA_PLATFORM_USAGE "
    "[Google Ads current user (DA_PLATFORM_USAGE 1,2,3, or 4=5), "
    "Google ads Lapsed User (NOT Google Ads Current User AND "
    "DA_PLATFORM_USAGE 1, 2, 3, or 4 = 4), "
    "Meta Ads Current User (DA_PLATFORM_USAGE 5, 6, 7, or 8 = 5), "
    "LinkedIn Current Users (DA_PLATFORM_USAGE 10 = 5), "
    "Amazon Ads Current Users (DA_PLATFORM_USAGE 11 = 5), "
    "OR  TikTok Current Users (DA_PLATFORM_USAGE 12 = 5)]"
)


def test_each_brand_gets_the_base_the_document_states_for_it():
    """The template's base describes the loop as a whole. Copied onto every
    iteration it asks every brand of everybody; the document defines them one
    brand at a time, against rows of an earlier grid."""
    survey = _survey()
    survey.questions[0].validation["base"] = BASE_NOTE
    expand_loops(survey)

    google = survey["DA_ATTITUDES_1"].show_if
    assert google is not None
    # Google Ads spans rows 1-4, current (5) or lapsed (4).
    assert evaluate(google, {"DA_PLATFORM_USAGE": {3: 5}}) is True
    assert evaluate(google, {"DA_PLATFORM_USAGE": {2: 4}}) is True
    # ...and a Meta-only user is not asked about Google Ads.
    assert evaluate(google, {"DA_PLATFORM_USAGE": {6: 5}}) is False

    linkedin = survey["DA_ATTITUDES_3"].show_if
    assert evaluate(linkedin, {"DA_PLATFORM_USAGE": {10: 5}}) is True
    assert evaluate(linkedin, {"DA_PLATFORM_USAGE": {11: 5}}) is False


def test_a_brand_the_document_never_defines_stays_unrouted():
    """Asking every brand of everybody is the failure this exists to prevent,
    and a guess is indistinguishable from a correct answer once data is in."""
    survey = _survey()
    survey.questions[0].validation["base"] = (
        "Google Ads current user (DA_PLATFORM_USAGE 1 = 5)"
    )
    applied = expand_loops(survey)

    assert survey["DA_ATTITUDES_1"].show_if is not None
    assert applied[0]["unrouted"] == ["Meta Ads", "LinkedIn", "Amazon Ads", "TikTok"]
    for index in range(2, 6):
        question = survey[f"DA_ATTITUDES_{index}"]
        assert question.show_if is None
        assert schema.FLAG_AMBIGUOUS_ROUTING in question.flags


def test_with_no_definitions_at_all_nothing_is_routed():
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

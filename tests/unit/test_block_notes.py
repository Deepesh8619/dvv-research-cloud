"""The study's own structure, written in a scripter note."""

from __future__ import annotations

from src.core.survey_definition import Option, Question, QuestionType, SurveyDefinition
from src.engines.questionnaire.block_notes import apply, read

NOTE = (
    "SCRIPTER NOTE : Please group the Google Ads questions and Meta Ads questions "
    "into two separate blocks. Block order: Rotate the presentation of the two blocks "
    "evenly (50% see Google Ads first, 50% see Meta Ads first). Internal order: Do NOT "
    "randomize the questions within the blocks. The internal question order must remain "
    "static/fixed. "
    "Google Ads Block of questions : GA_ONE, GA_TWO, GA_THREE "
    "Meta Ads Block of questions: META_ONE, META_TWO"
)

YES_NO = [Option(code=1, label="Yes"), Option(code=2, label="No")]


def _survey(qids, note_on=None):
    questions = [
        Question(qid=q, text=f"{q}?", type=QuestionType.SINGLE, options=list(YES_NO),
                 source_text=NOTE if q == (note_on or qids[0]) else f"{q}?")
        for q in qids
    ]
    return SurveyDefinition(study_id="T", questions=questions)


def test_a_note_names_the_blocks_and_their_questions():
    found = read(NOTE)
    assert [label for label, _ in found["blocks"]] == ["Google Ads", "Meta Ads"]
    assert found["blocks"][0][1] == ["GA_ONE", "GA_TWO", "GA_THREE"]
    assert found["blocks"][1][1] == ["META_ONE", "META_TWO"]
    assert found["fix_internal_order"] is True
    assert "Rotate" in found["rotate_verbatim"]


def test_the_blocks_are_built_in_the_order_the_document_gives():
    survey = _survey(["S1", "GA_ONE", "GA_TWO", "GA_THREE", "META_ONE", "META_TWO", "Q99"])
    applied = apply(survey)

    labels = [b.label for b in survey.blocks]
    assert "Google Ads" in labels and "Meta Ads" in labels
    google = next(b for b in survey.blocks if b.label == "Google Ads")
    assert google.question_ids == ["GA_ONE", "GA_TWO", "GA_THREE"]
    # The note says fixed, and saying so is what stops it being switched on.
    assert google.randomize_questions is False
    assert applied["internal_order_fixed"] is True


def test_no_question_falls_out_of_every_block():
    """A question in no block is a question nobody is asked.

    The note names fifteen of eighty. The other sixty-five keep their place
    around the named blocks, in document order.
    """
    qids = ["S1", "S2", "GA_ONE", "GA_TWO", "GA_THREE", "META_ONE", "META_TWO", "Q98", "Q99"]
    survey = _survey(qids)
    apply(survey)

    placed = [q for b in survey.blocks for q in b.question_ids]
    assert sorted(placed) == sorted(qids)
    assert len(placed) == len(set(placed)), "a question must not be in two blocks"
    # Order is preserved end to end.
    assert placed == qids


def test_a_rotation_this_platform_cannot_do_is_recorded_not_dropped():
    """The half of the instruction that decides whether the study is sound.

    Fielded in fixed order every respondent meets Google first, and the
    Google-versus-Meta difference the study is FOR carries an order effect
    nobody can separate out afterwards.
    """
    survey = _survey(["GA_ONE", "GA_TWO", "GA_THREE", "META_ONE", "META_TWO"])
    applied = apply(survey)
    assert "Rotate" in applied["rotation_unsupported"]


def test_a_note_about_questions_this_survey_does_not_have_builds_nothing():
    """Last wave's note, or a typo. Either way not this survey's structure."""
    survey = _survey(["Q1", "Q2", "Q3"], note_on="Q1")
    assert apply(survey) == {}
    # And the survey is left exactly as it was.
    assert survey.blocks == []


def test_a_document_with_no_such_note_is_untouched():
    survey = SurveyDefinition(study_id="T", questions=[
        Question(qid="Q1", text="Age?", options=list(YES_NO), source_text="Q1. Age?"),
    ])
    assert apply(survey) == {}
    assert survey.blocks == []


def test_a_near_miss_in_the_note_is_suggested_never_applied():
    """The Google Ads questionnaire's own note says GO_AI_GOOGLE.

    The document's Q Name is GA_AI_GOOGLE — one character, and the error is in
    the instruction rather than in the question. The block is then short by
    one and nothing says why.

    The suggestion is offered to a reviewer and NOT acted on: a qid decides a
    data column, and quietly moving a question into a block because the name
    was nearly right is how a study ends up with an arm the analyst cannot
    reproduce from the document.
    """
    note = (
        "Google Ads Block of questions: GO_AI_GOOGLE, GA_TWO, GA_THREE "
        "Meta Ads Block of questions: META_ONE, META_TWO"
    )
    questions = [
        Question(qid=q, text=f"{q}?", type=QuestionType.SINGLE, options=list(YES_NO),
                 source_text=note if q == "GA_AI_GOOGLE" else f"{q}?")
        for q in ["GA_AI_GOOGLE", "GA_TWO", "GA_THREE", "META_ONE", "META_TWO"]
    ]
    survey = SurveyDefinition(study_id="T", questions=questions)

    applied = apply(survey)

    assert applied["named_but_absent"] == ["GO_AI_GOOGLE"]
    assert applied["did_you_mean"] == {"GO_AI_GOOGLE": "GA_AI_GOOGLE"}
    # NOT applied: the block holds only what the note actually named.
    google = next(b for b in survey.blocks if b.label == "Google Ads")
    assert "GA_AI_GOOGLE" not in google.question_ids
    # And the question is still asked, in its own place.
    placed = [q for b in survey.blocks for q in b.question_ids]
    assert "GA_AI_GOOGLE" in placed

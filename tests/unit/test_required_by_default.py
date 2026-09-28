"""A respondent could click through an entire interview answering nothing.

The compiler emits `isRequired` straight from `Question.required`, and every
deterministic path set it False — so on the three real client documents 0%, 6%
and 0% of asked questions had to be answered. The file comes back full of holes
that look exactly like questions people chose to decline.
"""

from src.core.survey_definition import (
    Block,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
)
from src.engines.questionnaire.extractor import extract_question
from src.intake.questionnaire_loader import DocumentBlock, segment_into_questions
from src.renderers.surveyjs_compiler import compile_to_surveyjs


def _extract(lines):
    blocks = [DocumentBlock("paragraph", line, i) for i, line in enumerate(lines)]
    return extract_question(
        segment_into_questions(blocks)[0], None, None, None, None
    ).question


def test_an_extracted_question_must_be_answered():
    # "Select all that apply" states the type. Without it the parser refuses to
    # guess single vs multi from an option list — deliberately — and escalates,
    # which is a slower test of nothing.
    question = _extract([
        "Q1. Which brands do you know? Select all that apply.",
        "1. Alpha",
        "2. Beta",
    ])
    assert question.type is QuestionType.MULTI
    assert question.required is True


def test_an_open_end_is_not_forced():
    """"Anything else you would like to add?" genuinely has nothing to add, and
    forcing text there buys a file full of "n/a" and "none"."""
    question = _extract(["Q9. Anything else you would like to add? [OPEN END]"])
    assert question.type is QuestionType.OPEN_TEXT
    assert question.required is False


def test_display_content_is_never_required():
    question = _extract(["S0. CODE SAMPLE SOURCE", "1. Panel", "2. CRM"])
    assert question.type is QuestionType.INFO
    assert question.required is False


def test_the_requirement_reaches_the_respondent_s_browser():
    """Verified against survey-core 1.12.63: with this set, a page with an
    unanswered required question refuses to advance, answering it does not
    auto-advance, and Next works."""
    survey = SurveyDefinition(
        study_id="t",
        blocks=[Block(block_id="b", question_ids=["S1", "Q9"])],
        questions=[
            Question(
                qid="S1", text="Age group?", type=QuestionType.SINGLE,
                options=[Option(code=1, label="18-34")], required=True,
            ),
            Question(
                qid="Q9", text="Anything else?", type=QuestionType.OPEN_TEXT,
                required=False,
            ),
        ],
    )
    elements = compile_to_surveyjs(survey)["pages"][0]["elements"]
    assert [(e["name"], e["isRequired"]) for e in elements] == [
        ("S1", True), ("Q9", False),
    ]

"""What a respondent is shown, and what they must never be shown.

Every case here was found by opening a live link and reading the screen: an
instruction to the research team offered as an answer, a computed variable
rendered as a page, and a list that put "None of these" in the middle.
"""

from src.core.survey_definition import (
    Block,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
)
from src.engines.questionnaire.definition_audit import audit_definition
from src.engines.questionnaire.prose_directives import (
    is_quota_instruction,
    is_terminal_option,
    order_terminal_options,
    parse_prose_question,
)
from src.renderers.surveyjs_compiler import compile_to_surveyjs


def _rendered(question: Question) -> list[str]:
    survey = SurveyDefinition(
        study_id="T",
        source="extracted",
        status="approved",
        blocks=[Block(block_id="B1", question_ids=[question.qid])],
        questions=[question],
    )
    return [
        element.get("type")
        for page in compile_to_surveyjs(survey)["pages"]
        for element in page["elements"]
    ]


def test_an_instruction_to_the_team_is_not_an_answer():
    """"MONITOR S6 & S7 FALLOUT FOR CONSISTENCY WITH WAVE 1:" sits under the
    answers because that is what it is about. Offered as one, a respondent could
    choose it."""
    fields = parse_prose_question(
        "S7. Which of the following is your PRIMARY form of health insurance?\n"
        "Private insurance\nMedicare\nMedicaid\nVA or military insurance coverage\n"
        "MONITOR S6 & S7 FALLOUT FOR CONSISTENCY WITH WAVE 1:",
        "S7",
    )
    assert [o.label for o in fields.options] == [
        "Private insurance",
        "Medicare",
        "Medicaid",
        "VA or military insurance coverage",
    ]
    assert is_quota_instruction("MONITOR S6 & S7 FALLOUT FOR CONSISTENCY WITH WAVE 1:")
    assert is_quota_instruction("Medicare") is False


def test_the_closing_answers_end_the_list():
    """A respondent reads "Other" and "None of these" last wherever the document
    typed them, and a list offering "None" in the middle collects worse data."""
    options = [
        Option(code=1, label="New York-Presbyterian"),
        Option(code=2, label="Other (Specify)"),
        Option(code=3, label="NYU Langone"),
        Option(code=4, label="None - not affiliated"),
        Option(code=5, label="I don't know"),
    ]
    ordered = order_terminal_options(options)
    assert [o.label for o in ordered][:2] == ["New York-Presbyterian", "NYU Langone"]
    assert [o.label for o in ordered][2:] == [
        "Other (Specify)",
        "None - not affiliated",
        "I don't know",
    ]
    # Pinned, so randomisation never scatters them back into the list...
    assert all(o.anchored for o in ordered[2:])
    # ...and the CODES are untouched, because the data is keyed on them.
    assert sorted(o.code for o in ordered) == [1, 2, 3, 4, 5]
    assert next(o.code for o in ordered if o.label == "I don't know") == 5


def test_i_dont_know_closes_a_list_too():
    assert is_terminal_option("I don't know") is True
    assert is_terminal_option("Prefer not to answer") is True
    assert is_terminal_option("Northwell Health") is False
    assert is_terminal_option("Nothing else") is False


def test_a_computed_variable_is_never_a_page():
    """A definition stored BEFORE the derived rule existed carries no flag, and
    a respondent was shown a page reading "CODE AGE". The wording is the same
    signal the extractor uses, so an old definition stops leaking without
    needing a re-extraction first."""
    assert _rendered(Question(qid="S3a", text="CODE AGE", type=QuestionType.INFO)) == []
    assert _rendered(
        Question(
            qid="S1b",
            text="CODE PRIMARY/SECONDARY AREA BASED ON ZIP CODE",
            type=QuestionType.INFO,
        )
    ) == []


def test_a_welcome_note_is_still_shown():
    """The rule must not swallow the screens a respondent is meant to read."""
    assert _rendered(
        Question(
            qid="INTRO",
            text="Welcome to the survey. Please answer honestly.",
            type=QuestionType.INFO,
        )
    ) == ["html"]


def test_a_base_stated_in_words_is_reported_as_its_own_thing():
    """"[ASK IF MULTIPLE AT S6 SELECTED]" gates the question in words, not codes.
    No parser should invent a condition from it — and it was invisible to the
    exact-comparison check, so the question went to everyone with nothing said."""
    question = Question(
        qid="S7",
        text="Which is your PRIMARY form of health insurance?",
        options=[Option(code=1, label="Private insurance")],
        source_text="S7. [ASK IF MULTIPLE AT S6 SELECTED] Which is your PRIMARY form?",
    )
    survey = SurveyDefinition(
        study_id="T",
        source="extracted",
        status="draft",
        blocks=[Block(block_id="B1", question_ids=["S7"])],
        questions=[question],
    )
    checks = {f.check for f in audit_definition(survey)}
    assert "base_stated_in_words_only" in checks
    # Not confused with the parser's own failure to apply an exact condition.
    assert "base_stated_but_not_applied" not in checks

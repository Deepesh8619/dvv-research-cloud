"""Things a document states that nobody is asked, and that still reach the data.

Both cases here are from a live screener page: a sampling target read as an
answer, and a computed age band that produced no column at all.
"""

from src.core.survey_definition import (
    Block,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
)
from src.engines.fielding.materialize import plan_datamap
from src.engines.questionnaire import schema
from src.engines.questionnaire.extractor import ExtractionResult, _flag_derived_variable
from src.engines.questionnaire.prose_directives import (
    is_quota_instruction,
    parse_prose_question,
)
from src.intake.questionnaire_loader import QuestionChunk


def _chunk(qid: str, text: str = "") -> QuestionChunk:
    return QuestionChunk(
        chunk_text=text, candidate_qid=qid, source_blocks=[], provenance="para 1"
    )


def test_a_sampling_target_is_not_an_answer():
    """"TARGET 50/50 GENDER SPLIT" sits in the option list because that is where
    it is relevant. Read as an answer it became a punch a respondent could pick
    and a code in the data that means nothing."""
    fields = parse_prose_question(
        "S2. Do you identify as...?\n"
        "Male\nFemale\nNon-Binary\nPrefer to self-describe (Specify)\n"
        "TARGET 50/50 GENDER SPLIT",
        "S2",
    )
    assert [o.label for o in fields.options] == [
        "Male",
        "Female",
        "Non-Binary",
        "Prefer to self-describe (Specify)",
    ]
    # Kept, not dropped: it is how many of each kind the study needs.
    assert fields.quota_hints == ["TARGET 50/50 GENDER SPLIT"]


def test_an_answer_that_merely_mentions_a_target_is_untouched():
    """The keyword is not enough — a scripter SHOUTS an instruction, and an
    ordinary answer containing the word "target" is still an answer."""
    assert is_quota_instruction("I target a budget each month") is False
    assert is_quota_instruction("Prefer to self-describe (Specify)") is False
    assert is_quota_instruction("55 or older") is False
    assert is_quota_instruction("TARGET 50/50 GENDER SPLIT") is True
    assert is_quota_instruction("N=400 QUOTA") is True


def test_a_coded_hidden_variable_reaches_the_data_file():
    """"CODE AGE" with its bands under it is computed, not asked — and it is the
    variable every banner cut by age reads. Typed as display text it emitted no
    column, so the thing the tables are built on was absent from the file."""
    bands = [
        Option(code=index, label=label)
        for index, label in enumerate(
            ["Under 25", "25-34", "35-44", "45-54", "55-64", "65+"], start=1
        )
    ]
    survey = SurveyDefinition(
        study_id="T",
        source="extracted",
        status="approved",
        blocks=[Block(block_id="B1", question_ids=["S3", "S3a"])],
        questions=[
            Question(qid="S3", text="What is your age?", type=QuestionType.NUMERIC),
            Question(
                qid="S3a",
                text="CODE AGE",
                type=QuestionType.INFO,
                options=bands,
                flags=[schema.FLAG_DERIVED_VARIABLE],
            ),
        ],
    )
    variables = {v.name: v for v in plan_datamap(survey).variables}
    assert "S3a" in variables
    assert variables["S3a"].provenance == "derived"
    assert variables["S3a"].values[1] == "Under 25"
    assert variables["S3a"].values[6] == "65+"


def test_display_text_is_still_not_a_variable():
    """The rule must not turn every info screen into a column."""
    survey = SurveyDefinition(
        study_id="T",
        source="extracted",
        status="approved",
        blocks=[Block(block_id="B1", question_ids=["INTRO"])],
        questions=[
            Question(qid="INTRO", text="Welcome to the survey.", type=QuestionType.INFO)
        ],
    )
    assert "INTRO" not in {v.name for v in plan_datamap(survey).variables}


def test_wording_alone_is_asked_about_rather_than_assumed():
    """An all-caps "CODE AGE" with no HID_ prefix and no named source is usually
    a hidden variable and occasionally a real question somebody shouted. The
    difference decides whether anybody is asked it, so the reviewer confirms."""
    result = ExtractionResult(question=Question(qid="S3a", text="CODE AGE"))
    _flag_derived_variable(result, _chunk("S3a"))
    assert schema.FLAG_DERIVED_VARIABLE in result.flags
    assert schema.FLAG_DERIVED_VARIABLE_UNCONFIRMED in result.flags
    assert result.question.type is QuestionType.INFO


def test_a_named_source_or_a_hid_prefix_needs_no_confirmation():
    named = ExtractionResult(
        question=Question(qid="C1", text="CODE AGE - BASED ON RESPONSES AT S1")
    )
    _flag_derived_variable(named, _chunk("C1"))
    assert schema.FLAG_DERIVED_VARIABLE in named.flags
    assert schema.FLAG_DERIVED_VARIABLE_UNCONFIRMED not in named.flags

    prefixed = ExtractionResult(question=Question(qid="HID_SEGMENT", text="Segment"))
    _flag_derived_variable(prefixed, _chunk("HID_SEGMENT"))
    assert schema.FLAG_DERIVED_VARIABLE_UNCONFIRMED not in prefixed.flags
    assert "derived" in prefixed.question.extraction_evidence

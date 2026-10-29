"""The brief, read first and ticked off: each need covered, missing or to confirm."""

from src.core.survey_definition import (
    Block,
    Expression,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
)
from src.engines.questionnaire import schema
from src.engines.questionnaire.brief_checklist import brief_checklist


def _survey() -> SurveyDefinition:
    questions = [
        Question(qid="INTRO", text="Thank you for taking part.", type=QuestionType.INFO),
        Question(qid="S1", text="What is your age?", type=QuestionType.NUMERIC,
                 terminate_if=Expression(op="lt", qid="S1", value=25),
                 source_text="S1. What is your age?\n[PN: TERMINATE IF UNDER 25]"),
        Question(qid="S2", text="How many employees?",
                 options=[Option(code=1, label="2-9"), Option(code=2, label="10-99")]),
        Question(qid="C3", text="CODE GENDER – BASED ON S3"),  # read as an asked question
        Question(qid="HID_SIZE", text="Company size", flags=[schema.FLAG_DERIVED_VARIABLE]),
    ]
    survey = SurveyDefinition(
        study_id="T", source="extracted", status="draft",
        blocks=[Block(block_id="B1", question_ids=[q.qid for q in questions])],
        questions=questions,
    )
    survey.metadata["study_overview"] = {
        "screening_requirements": ["Age 25+", "Revenue under 5000", "Decision maker"],
        "quotas": [
            {"group": "Total N", "definition": "", "target_n": 750},
            {"group": "Micro", "definition": "2-9 Employees", "min_n": 75},
            {"group": "Enterprise", "definition": "1000+ Employees"},
        ],
        "programming_notes": ["Grid question:", "Ask Q99 of everyone who qualifies"],
    }
    return survey


def test_each_need_is_ticked_off_against_the_instrument():
    result = brief_checklist(_survey())
    by_text = {i["text"]: i for i in result["items"]}

    assert by_text["Micro 2-9 Employees"]["status"] == "covered"
    assert by_text["Micro 2-9 Employees"]["where"] == ["S2"]  # carries both numbers
    assert by_text["Enterprise 1000+ Employees"]["status"] == "missing"
    assert not any(t.startswith("Total") for t in by_text)  # the total is not a cell

    assert by_text["Age 25+"]["status"] == "covered"
    assert by_text["Revenue under 5000"]["status"] == "missing"
    assert by_text["Decision maker"]["status"] == "check"

    variables = {i["where"][0]: i["status"] for i in result["items"] if i["kind"] == "variable"}
    assert variables == {"C3": "missing", "HID_SIZE": "covered"}

    notes = [i for i in result["items"] if i["kind"] == "note"]
    assert len(notes) == 1 and notes[0]["status"] == "missing"  # Q99 does not exist

    assert result["screens"] == ["INTRO"]
    assert result["conventions"]["pn_markers"] == {"PN": 1}
    assert result["conventions"]["label_families"]["S"] == 2


def test_a_document_without_a_brief_still_reports_its_conventions():
    survey = _survey()
    survey.metadata.pop("study_overview")
    result = brief_checklist(survey)
    assert {i["kind"] for i in result["items"]} == {"variable"}
    assert result["conventions"]["label_families"]

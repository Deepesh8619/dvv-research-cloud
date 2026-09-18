"""Every question's kind, and the links the questionnaire itself states."""

from src.core.survey_definition import (
    Block,
    Expression,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
)
from src.engines.fielding.field_report import build_field_report
from src.engines.questionnaire import schema
from src.engines.questionnaire.buckets import bucket_of, links

YN = [Option(code=1, label="Yes"), Option(code=2, label="No")]
BRANDS = [Option(code=1, label="Bubble"), Option(code=2, label="Anua")]


def _survey() -> SurveyDefinition:
    questions = [
        Question(qid="S1", text="Do you work in marketing?", options=YN,
                 terminate_if=Expression(op="eq", qid="S1", value=2)),
        Question(qid="S2", text="What is your age?", type=QuestionType.NUMERIC),
        Question(qid="HID_AGE", text="CODE AGE - BASED ON RESPONSES AT S2",
                 type=QuestionType.INFO, options=YN, flags=[schema.FLAG_DERIVED_VARIABLE]),
        Question(qid="Q2a", text="Which brands have you heard of?", type=QuestionType.MULTI,
                 options=BRANDS),
        Question(qid="Q2b", text="Which have you bought?", type=QuestionType.MULTI,
                 options=BRANDS,
                 validation={"techniques": {"masks": [{"source_qid": "Q2a", "mode": "selected"}]}}),
        Question(qid="Q3", text="Why did you choose it?", type=QuestionType.OPEN_TEXT,
                 show_if=Expression(op="in", qid="Q2b", values=[1], verbatim_text="Q2b=1")),
    ]
    return SurveyDefinition(
        study_id="T", source="extracted", status="approved",
        blocks=[Block(block_id="B1", question_ids=[q.qid for q in questions])],
        questions=questions,
    )


def test_each_question_lands_in_the_bucket_a_researcher_reads_it_by():
    by = {q.qid: bucket_of(q) for q in _survey().questions}
    assert by["S1"][0] == "screener" and by["S1"][2] is False  # it screens out: certain
    assert by["S2"][:1] == ("profile",) and by["S2"][2] is True  # wording: asked to confirm
    assert by["HID_AGE"][0] == "hidden"
    assert by["Q2a"][0] == "core"
    assert by["Q3"][0] == "open_end"


def test_links_follow_bases_masks_and_derivations_both_ways():
    graph = links(_survey())
    assert graph["Q2b"]["options_from"] == ["Q2a"]
    assert graph["Q3"]["asked_if"] == ["Q2b"]
    assert graph["HID_AGE"]["computed_from"] == ["S2"]
    # The reverse edge: what each answer decides.
    assert graph["Q2a"]["used_by"] == ["Q2b"]
    assert graph["Q2b"]["used_by"] == ["Q3"]
    assert graph["S2"]["used_by"] == ["HID_AGE"]


def test_the_report_cards_carry_bucket_and_links():
    report = build_field_report(_survey(), [], {})
    cards = {c["qid"]: c for c in report.questions}
    assert cards["Q2b"]["bucket"] == "core"
    assert cards["Q2b"]["links"]["options_from"] == ["Q2a"]
    assert "HID_AGE" in cards  # computed, but a variable — it gets a card

"""A question asked once per category, a brand list named once, and numbers
that must add up — three shapes from a real consumer questionnaire, each of
which fielded with nothing for a respondent to answer."""

import random

from src.core.survey_definition import (
    Block,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
    evaluate,
)
from src.engines.fielding.dryrun import _answer
from src.engines.fielding.materialize import plan_datamap
from src.engines.questionnaire import carousel, constant_sum
from src.engines.questionnaire.list_sources import fill_lists
from src.renderers.surveyjs_compiler import compile_to_surveyjs

AWARE = [Option(code=c, label=b) for c, b in
         [(1, "Bubble"), (5, "CeraVe"), (14, "Neutrogena"), (18, "Rhode"),
          (98, "Other (Specify)"), (99, "None of the above")]]
CORE_TABLE = [["PROGRAMMING: FOR Q6-Q10 REFER TO CORE COMPETITOR SET OF 4 BRANDS BELOW ONLY\n"
               "Bubble\nCeraVe\nNeutrogena\nRhode"]]
CATEGORY_TABLES = [
    [["", "Cleansers", "Sunscreens (for the face)", "Lip products"]],
    [["FOR PROGRAMMERS"],
     ["[PN: DISPLAY CATEGORY IF]", "Q3.1=1-4", "Q3.3=1-4", "OR 6 OR 7=1-4"],
     ["[PN: CORE BRANDS TO DISPLAY BASED ON SELLING PRODUCTS IN CATEGORY]",
      "ALL 4 BRANDS", "ALL BRANDS EXCEPT RHODE", "ONLY SHOW\nBubble\nRhode"]],
]


def _survey() -> SurveyDefinition:
    q2a = Question(qid="Q2a", text="Which of these skincare brands have you heard of?",
                   type=QuestionType.MULTI, options=list(AWARE))
    q3 = Question(qid="Q3", text="How often do you use each product?",
                  type=QuestionType.GRID_SINGLE,
                  rows=[Option(code=i, label=f"p{i}") for i in range(1, 8)],
                  cols=[Option(code=i, label=f"f{i}") for i in range(1, 6)])
    q5 = Question(qid="Q5", text="Describe yourself", type=QuestionType.OPEN_TEXT)
    q6 = Question(
        qid="Q6", text="Which brands have you used in the last 6 months?",
        type=QuestionType.GRID_SINGLE,
        options=[Option(code=1, label="98 Another brand not listed (Specify)")],
        source_text="[PN: SHOW CORE BRANDS ABOVE. ONLY DISPLAY THOSE AWARE FROM Q2a]",
    )
    q8 = Question(qid="Q8", text="How do you feel about each of the following skincare brands?",
                  type=QuestionType.GRID_SINGLE,
                  cols=[Option(code=i, label=str(i)) for i in range(0, 11)])
    carousel.capture(q5, [CORE_TABLE])
    carousel.capture(q6, CATEGORY_TABLES)
    survey = SurveyDefinition(
        study_id="T", source="extracted", status="approved",
        blocks=[Block(block_id="B", question_ids=["Q2a", "Q3", "Q5", "Q6", "Q8"])],
        questions=[q2a, q3, q5, q6, q8],
    )
    carousel.apply(survey)
    fill_lists(survey)
    return survey


def test_each_category_is_its_own_question_with_its_own_brands():
    survey = _survey()
    assert [q.qid for q in survey.questions if q.qid.startswith("Q6")] == ["Q6_1", "Q6_2", "Q6_3"]
    labels = {q.qid: [o.label for o in q.options] for q in survey.questions}
    assert labels["Q6_1"][:4] == ["Bubble", "CeraVe", "Neutrogena", "Rhode"]
    assert "Rhode" not in labels["Q6_2"]  # ALL BRANDS EXCEPT RHODE
    assert labels["Q6_3"][:2] == ["Bubble", "Rhode"]  # ONLY SHOW
    # The write-in keeps its written code, not "98 Another brand".
    assert survey["Q6_1"].options[-1].code == 98
    assert survey["Q6_1"].options[-1].label == "Another brand not listed (Specify)"


def test_brand_codes_follow_the_list_the_document_names():
    """The client's script coded "the core set of brands below" 1..N in order
    (Bubble: CeraVe 2, Neutrogena 3), not with the awareness list's codes."""
    assert {o.label: o.code for o in _survey()["Q6_1"].options} == {
        "Bubble": 1, "CeraVe": 2, "Neutrogena": 3, "Rhode": 4,
        "Another brand not listed (Specify)": 98}


def test_a_category_shows_only_to_users_of_it_and_only_brands_they_know():
    survey = _survey()
    q = survey["Q6_1"]
    assert evaluate(q.show_if, {"Q3": {"1": 2}}) is True
    assert evaluate(q.show_if, {"Q3": {"1": 5}}) is False
    element = {e["name"]: e for p in compile_to_surveyjs(survey)["pages"]
               for e in p["elements"] if "name" in e}["Q6_1"]
    # Matched by brand name: Neutrogena is 3 here and 14 in Q2a.
    shown_if = {c["value"]: c.get("visibleIf") for c in element["choices"]}
    assert shown_if[3] == "{Q2a} contains 14"
    # The write-in is never hidden by awareness of Q2a's own "Other".
    assert shown_if[98] is None


def test_a_condition_written_without_its_question_is_flagged_not_guessed():
    q = _survey()["Q6_3"]
    assert "AMBIGUOUS_ROUTING" in q.flags
    assert "names no question" in q.extraction_evidence["show_if"]


def test_a_list_named_for_a_range_is_used_there_and_needs_no_confirmation():
    q8 = _survey()["Q8"]
    assert [r.label for r in q8.rows] == ["Bubble", "CeraVe", "Neutrogena", "Rhode"]
    assert "LIST_INHERITED" not in q8.flags


def _constant_sum_survey() -> SurveyDefinition:
    q = Question(
        qid="Q37", text="What percentage goes to each?",
        options=[Option(code=1, label="Total: 100%")],
        source_text=("Q37. What percentage goes to each?\n"
                     "___ % Brands I already know and trust\n"
                     "___ % Brands that are new to me\n"
                     "Total: 100% [PN: MUST SUM TO 100]"),
    )
    survey = SurveyDefinition(study_id="T", source="extracted", status="approved",
                              blocks=[Block(block_id="B", question_ids=["Q37"])], questions=[q])
    constant_sum.apply(survey)
    return survey


def test_numbers_that_must_add_up_are_boxes_with_a_total():
    survey = _constant_sum_survey()
    q = survey["Q37"]
    assert q.type is QuestionType.NUMERIC and q.options == []
    assert [r.label for r in q.rows] == [
        "Brands I already know and trust", "Brands that are new to me"]
    element = compile_to_surveyjs(survey)["pages"][0]["elements"][0]
    assert element["type"] == "multipletext"
    assert element["validators"][0]["expression"] == "{Q37.1} + {Q37.2} = 100"
    # One data column per box.
    assert [v.name for v in plan_datamap(survey).variables if v.name.startswith("Q37")] == [
        "Q37r1", "Q37r2"]


def test_the_dry_run_answers_a_constant_sum_with_shares_that_reach_the_total():
    q = _constant_sum_survey()["Q37"]
    for seed in range(20):
        answer = _answer(q, random.Random(seed), None)
        assert sum(answer.values()) == 100 and all(v >= 0 for v in answer.values())


def test_the_dry_run_masks_by_brand_name_too():
    from src.engines.fielding.dryrun import _mask_allowed_codes

    survey = _survey()
    by_qid = {q.qid: q for q in survey.questions}
    # Aware of Neutrogena (14 in Q2a) only: Q6_1 may offer Neutrogena (3 here).
    assert _mask_allowed_codes(survey["Q6_1"], {"Q2a": [14]}, by_qid) == {3}

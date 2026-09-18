"""Lists a questionnaire states once and uses again, and what a respondent is shown.

Every case is a shape from a real consumer questionnaire that fielded a
question with NO answers, a computed variable as a question, or a screen-out
left in an answer's label.
"""

from src.core.survey_definition import (
    Block,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
    evaluate,
)
from src.engines.questionnaire import schema
from src.engines.questionnaire.list_sources import fill_lists
from src.engines.questionnaire.prose_directives import is_derived_wording, parse_prose_question
from src.engines.questionnaire.table_questions import question_from_tables, scale_strip
from src.renderers.surveyjs_compiler import compile_to_surveyjs

BRANDS = ["Bubble", "CeraVe", "Neutrogena", "Rhode", "Another brand (Specify)"]
BRAND_OPTIONS = [Option(code=i, label=b) for i, b in enumerate(BRANDS, 1)]
YES_NO = [Option(code=1, label="Yes"), Option(code=2, label="No")]


def _mask(source: str, mode: str, exclude: str | None = None) -> dict:
    return {"techniques": {"masks": [
        {"source_qid": source, "mode": mode, "exclude_qid": exclude}]}}


ZERO_TEN = [Option(code=0, label="Very Unlikely")] + [
    Option(code=i, label=str(i)) for i in range(1, 10)
] + [Option(code=10, label="Very Likely")]


def _survey(*questions: Question) -> SurveyDefinition:
    return SurveyDefinition(
        study_id="T", source="extracted", status="approved",
        blocks=[Block(block_id="B1", question_ids=[q.qid for q in questions])],
        questions=list(questions),
    )


def _elements(survey: SurveyDefinition) -> dict:
    return {e.get("name"): e for p in compile_to_surveyjs(survey)["pages"] for e in p["elements"]}


def test_a_named_list_is_copied_and_the_mask_still_narrows_it():
    """Q27 "[SHOW Q26 LIST]", Q28 "[SHOW Q26 LIST EXCEPT FOR Q27 SELECTION]"."""
    q26 = Question(qid="Q26", text="Rate each statement", type=QuestionType.GRID_SINGLE,
                   rows=[Option(code=i, label=f"Statement {i}") for i in (1, 2, 3)],
                   cols=ZERO_TEN)
    q27 = Question(qid="Q27", text="Which is most believable?",
                   validation=_mask("Q26", "full_list"),
                   flags=[schema.FLAG_EMPTY_OPTIONS])
    q28 = Question(qid="Q28", text="Which is least believable?",
                   validation=_mask("Q26", "list_except", "Q27"))
    survey = _survey(q26, q27, q28)
    fill_lists(survey)
    assert [o.label for o in q27.options] == ["Statement 1", "Statement 2", "Statement 3"]
    assert schema.FLAG_EMPTY_OPTIONS not in q27.flags
    assert schema.FLAG_LIST_INHERITED not in q27.flags  # named: certain
    assert _elements(survey)["Q28"]["choicesVisibleIf"] == "{Q27} <> {item}"


def test_a_grid_masked_from_a_question_asked_in_other_case_keeps_its_rows_narrowed():
    """"SHOW BRANDS SELECTED AT Q2b" was read as "Q2B": no match, no rows."""
    q2b = Question(qid="Q2b", text="Which brands have you ever purchased?",
                   type=QuestionType.MULTI, options=list(BRAND_OPTIONS))
    q2c = Question(qid="Q2c", text="When did you last buy from each of the following brands?",
                   type=QuestionType.GRID_SINGLE,
                   cols=[Option(code=1, label="Within 3 months"), Option(code=2, label="Longer")],
                   validation=_mask("Q2B", "selected"))
    survey = _survey(q2b, q2c)
    fill_lists(survey)
    # The write-in belongs to Q2b, not to a list of brands.
    assert [r.label for r in q2c.rows] == ["Bubble", "CeraVe", "Neutrogena", "Rhode"]
    assert _elements(survey)["Q2c"]["rowsVisibleIf"] == "{Q2b} contains {item}"


def test_a_list_only_pointed_at_is_copied_and_flagged_for_a_person():
    q7 = Question(qid="Q7", text="Which three skincare brands would you choose?",
                  type=QuestionType.RANK, options=list(BRAND_OPTIONS))
    q9 = Question(qid="Q9", text="How likely are you to consider each of these brands?",
                  type=QuestionType.GRID_SINGLE, cols=ZERO_TEN)
    fill_lists(_survey(q7, q9))
    assert [r.label for r in q9.rows] == ["Bubble", "CeraVe", "Neutrogena", "Rhode"]
    assert schema.FLAG_LIST_INHERITED in q9.flags
    assert "does not name it" in q9.extraction_evidence["options"]


def test_a_grid_with_no_scale_is_not_given_someone_elses_answers():
    """"For each category, which brands" has neither half; borrowing rows put
    another question's purchase frequencies down its side."""
    q4 = Question(qid="Q4", text="How often do you buy skincare products?",
                  options=[Option(code=i, label=f"f{i}") for i in (1, 2, 3)])
    q6 = Question(qid="Q6", text="For each of these types of skincare products, which brands?",
                  type=QuestionType.GRID_SINGLE)
    fill_lists(_survey(q4, q6))
    assert not q6.rows


def test_a_scale_written_as_labels_over_codes_is_read():
    table = [["Within the past 3 months", "3-5 months ago", "6-12 months ago"], ["1", "2", "3"]]
    assert [(o.code, o.label) for o in scale_strip(table)] == [
        (1, "Within the past 3 months"), (2, "3-5 months ago"), (3, "6-12 months ago")]
    # Its own code row is not a statement: that sank the whole grid.
    shape = question_from_tables([table], [])
    assert shape is not None and shape.rows == [] and len(shape.cols) == 3


def test_a_bare_terminate_is_a_screen_out_not_part_of_the_answer():
    fields = parse_prose_question(
        "S3. Do you identify as…?\nFemale\nMale TERMINATE\nNon-binary", "S3"
    )
    assert [o.label for o in fields.options] == ["Female", "Male", "Non-binary"]
    assert evaluate(fields.terminate_if, {"S3": 2}) is True
    assert evaluate(fields.terminate_if, {"S3": 1}) is False


def test_a_code_named_last_is_still_a_computed_variable_and_is_never_asked():
    assert is_derived_wording("BUBBLE AUDIENCE CODE")
    assert is_derived_wording("CODE AGE")
    # A question shouted in capitals is still a question.
    assert not is_derived_wording("WHAT IS YOUR ZIP CODE?")
    assert not is_derived_wording("ENTER ZIP CODE")
    # Stored before this rule, as a single choice whose answers are RULES.
    c10 = Question(qid="C10", text="Audience segment", options=[
        Option(code=1, label="Recent Buyer (Q2c.1=1 OR 2)"),
        Option(code=2, label="Lapsed (Q2c.1=3)"),
        Option(code=3, label="Unaware (Not selected at Q2a)"),
    ])
    assert "C10" not in _elements(_survey(c10))


def test_one_thing_rated_on_a_scale_is_a_rating_row():
    q30 = Question(qid="Q30", text="How do you feel about Bubble now?", options=ZERO_TEN)
    element = _elements(_survey(q30))["Q30"]
    assert element["type"] == "rating"
    assert [v["value"] for v in element["rateValues"]] == list(range(11))
    assert (element["minRateDescription"], element["maxRateDescription"]) == (
        "Very Unlikely", "Very Likely")
    # An ordinary short list stays a list.
    yes_no = Question(qid="Q1", text="?", options=YES_NO)
    assert _elements(_survey(yes_no))["Q1"]["type"] == "radiogroup"


def test_a_pipe_written_in_other_case_still_fills_the_sentence():
    """"[INSERT Q2c RESPONSE FOR BUBBLE]" was stored as Q2C: the answer vanished
    and a respondent read "the last time you purchased Bubble was ."."""
    from src.renderers.surveyjs_compiler import display_warnings

    q2c = Question(qid="Q2c", text="When did you last buy from each brand?",
                   type=QuestionType.GRID_SINGLE, rows=list(BRAND_OPTIONS[:2]),
                   cols=[Option(code=1, label="Recently"), Option(code=2, label="Long ago")])
    q14 = Question(qid="Q14", type=QuestionType.OPEN_TEXT,
                   text="You last bought Bubble [INSERT Q2c RESPONSE FOR BUBBLE]. Why not since?",
                   validation={"techniques": {"pipes": [
                       {"source_qid": "Q2C", "name": None, "verbatim": "INSERT Q2c"}]}})
    survey = _survey(q2c, q14)
    # ONE row of the grid — SurveyJS prints that cell's column label.
    assert _elements(survey)["Q14"]["title"] == "You last bought Bubble {Q2c.1}. Why not since?"
    assert display_warnings(survey) == []


def test_a_pipe_that_names_no_question_means_the_single_answer_just_given():
    q24 = Question(qid="Q24", text="Rate the message", options=ZERO_TEN)
    q25 = Question(qid="Q25", text="Why did you rate it a [INSERT RATING]?",
                   type=QuestionType.OPEN_TEXT,
                   validation={"techniques": {"pipes": [
                       {"source_qid": None, "name": "RATING", "verbatim": "INSERT RATING]"}]}})
    survey = _survey(q24, q25)
    fill_lists(survey)
    assert _elements(survey)["Q25"]["title"] == "Why did you rate it a {Q24}?"


def test_after_a_grid_the_rating_is_left_for_a_person():
    """"This message's rating" after a grid names a row nobody identified."""
    grid = Question(qid="Q24", text="Rate each message", type=QuestionType.GRID_SINGLE,
                    rows=list(BRAND_OPTIONS[:2]), cols=ZERO_TEN)
    q25 = Question(qid="Q25", text="Why that rating [INSERT RATING]?", type=QuestionType.OPEN_TEXT,
                   validation={"techniques": {"pipes": [
                       {"source_qid": None, "name": "RATING", "verbatim": "INSERT RATING"}]}})
    fill_lists(_survey(grid, q25))
    assert q25.validation["techniques"]["pipes"][0]["source_qid"] is None


def test_a_sample_split_is_assigned_and_a_section_header_is_not_an_answer():
    fields = parse_prose_question(
        "S0a. SAMPLE SPLIT\nSPLIT A\nSPLIT B\n"
        "CONSUMER PANEL INTRODUCTION & SCREENER (S0=1)\nThank you for taking part.",
        "S0a",
    )
    assert [o.label for o in fields.options] == ["SPLIT A", "SPLIT B"]
    assert is_derived_wording("SAMPLE SPLIT")

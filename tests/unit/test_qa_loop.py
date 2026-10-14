"""The QA loop: QA finds, the programmer agent fixes, QA re-checks.

Each test is a real defect from the TRD and Vivo studies, reduced to its shape.
"""

from src.core.survey_definition import (
    Expression,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
)
from src.engines.questionnaire import qa_loop
from src.engines.questionnaire.qa_loop import FLAG_QA_ISSUE, Issue, run_qa
from src.engines.questionnaire.table_questions import stacked_grid

_STACKED = "\n".join([
    "[BASE: ALL] Overall, how satisfied are you with TMS and Spravato on a scale of 1 to 5? "
    "Select one per row.",
    "COLUMNS: | COLUMNS: | COLUMNS: | COLUMNS: | COLUMNS:",
    "Not at all satisfied | Not at all satisfied |  | Somewhat satisfied | Extremely satisfied",
    "1 | 1 | 2 | 3 | 4",
    " | ".join(["[ROWS, RANDOMIZE]"] * 5),
    " | TMS | TMS | TMS | TMS",
    " | Spravato | Spravato | Spravato | Spravato",
])


def _survey(*questions):
    return SurveyDefinition(study_id="s", questions=list(questions))


def test_a_stacked_grid_table_is_read_as_rows_and_a_scale():
    rows = [line.split(" | ") for line in _STACKED.splitlines()[1:]]
    shape = stacked_grid(rows)
    assert [r.label for r in shape.rows] == ["TMS", "Spravato"]
    assert [(c.code, c.label) for c in shape.cols] == [
        (1, "Not at all satisfied"), (2, "2"), (3, "Somewhat satisfied"),
        (4, "Extremely satisfied")]
    assert shape.randomize_rows


def test_an_empty_grid_stored_by_an_older_parser_is_repaired_and_rechecked():
    q = Question(qid="Q4", text="How satisfied?", type=QuestionType.GRID_SINGLE,
                 source_text=_STACKED, extraction_method="prose_directives")
    survey = _survey(q)
    survey.metadata["auto_approved_qids"] = []
    record = run_qa(survey)
    assert [e["status"] for e in record["entries"]] == ["fixed"]
    assert record["entries"][0]["rechecked_by_qa"]
    assert [r.label for r in q.rows] == ["TMS", "Spravato"] and FLAG_QA_ISSUE not in q.flags


def test_routing_the_agent_cannot_apply_stays_in_sight():
    """Lifting the note off the respondent's screen must not hide that the
    question is still asked of everyone."""
    q = Question(qid="S7", type=QuestionType.SINGLE,
                 text="[ASK IF MULTIPLE AT S6 SELECTED] Which is your PRIMARY insurance? "
                      "Select one.",
                 options=[Option(1, "Medicare"), Option(2, "Medicaid")],
                 source_text="S7. [ASK IF MULTIPLE AT S6 SELECTED] Which is your PRIMARY "
                             "insurance?")
    survey = _survey(q)
    survey.metadata["auto_approved_qids"] = ["S7"]
    record = run_qa(survey)
    open_checks = [e["check"] for e in record["entries"] if e["status"] == "needs_you"]
    assert open_checks == ["routing_not_applied"]
    assert FLAG_QA_ISSUE in q.flags
    assert survey.metadata["auto_approved_qids"] == []  # no longer pre-approved


def test_everyone_is_asked_needs_no_routing():
    q = Question(qid="D1", type=QuestionType.SINGLE, text="[ASK OF ALL] Where do you live?",
                 options=[Option(1, "City"), Option(2, "Rural")],
                 source_text="D1. Where do you live?")
    assert run_qa(_survey(q))["entries"] == []


def test_a_question_a_person_approved_is_reported_on_but_never_changed():
    q = Question(qid="Q4", text="How satisfied?", type=QuestionType.GRID_SINGLE,
                 source_text=_STACKED, extraction_method="prose_directives")
    record = run_qa(_survey(q), locked={"Q4"})
    assert q.rows == [] or q.rows is None
    assert record["entries"][0]["status"] == "needs_you"


def test_a_repair_that_breaks_something_else_is_thrown_away():
    before = [Issue("nothing_to_answer", "")]
    assert not qa_loop._better(before, [Issue("source_options_all_captured", "")])
    assert qa_loop._better(before, [Issue("row_condition_not_applied", "")])  # a disclosure
    assert not qa_loop._better(before, before)


def test_a_table_header_glued_to_the_question_text_is_cut_off():
    q = Question(qid="Q9", type=QuestionType.NUMERIC,
                 text="What percentage received each of the following? Fill in | % |",
                 rows=[Option(1, "TMS"), Option(2, "Spravato")], validation={"sum_min": 100},
                 source_text="Q9. What percentage received each? Fill in")
    record = run_qa(_survey(q))
    assert q.text == "What percentage received each of the following? Fill in"
    assert [e["status"] for e in record["entries"]] == ["fixed"]


def test_qa_notices_questions_swallowed_into_an_answer_list():
    q = Question(qid="Q6", type=QuestionType.SINGLE, text="When do you first discuss it?",
                 options=[Option(1, "After 1 treatment"),
                          Option(2, "A. What percentage do you discuss it with?"),
                          Option(3, "Fill in [0-100]_____")],
                 source_text="Q6. When do you first discuss it?")
    from src.engines.questionnaire.qa_loop import qa_check

    assert "question_inside_answers" in [i.check for i in qa_check(q, {"Q6": q})]


# --- Exact consistency checks (Google Ads SMB, Vivo) ---------------------------

def _hidden(qid, *defs):
    return Question(qid=qid, text="Hidden", type=QuestionType.INFO, flags=["DERIVED_VARIABLE"],
                    options=[Option(code=i, label=lab, definition=d)
                             for i, (lab, d) in enumerate(defs, start=1)])


def _division():
    return Question(qid="HID_GEO_DIVISION", text="Division", type=QuestionType.SINGLE,
                    options=[Option(code=c, label=f"Division {c}") for c in range(1, 10)]
                    + [Option(code=99, label="Prefer not to answer")])


def _found(survey, qid):
    run_qa(survey)
    return {e["check"] for e in survey.metadata["qa"]["entries"] if e["qid"] == qid}


def test_a_hidden_variable_formula_naming_no_question_is_reported():
    s = _survey(_division(), _hidden("HID_GEO_STATE", ("All", "Based on C_ZIP = 1")))
    assert "definition_names_missing_question" in _found(s, "HID_GEO_STATE")


def test_a_region_formula_with_overlap_and_unclaimed_divisions_is_reported():
    region = _hidden("HID_GEO_REGION", ("Northeast", "HID_GEO_DIVISION = 1,2"),
                     ("Midwest", "HID_GEO_DIVISION = 3,4"), ("South", "HID_GEO_DIVISION = 1,2"),
                     ("West", "HID_GEO_DIVISION = 5,6,7"))
    found = _found(_survey(_division(), region), "HID_GEO_REGION")
    assert {"definition_overlaps", "definition_leaves_answers_out"} <= found


def test_a_correct_formula_is_not_reported():
    size = Question(qid="SS_EMP", text="Employees?", type=QuestionType.SINGLE,
                    options=[Option(code=c, label=str(c)) for c in range(1, 11)],
                    terminate_if=Expression(op="in", qid="SS_EMP", values=[1, 10]))
    hid = _hidden("HID_COMP_SIZE", ("Micro", "SS_EMP = 2"), ("Small", "SS_EMP= 3-4"),
                  ("Mid", "SS_EMP= 5-9"))
    assert _found(_survey(size, hid), "HID_COMP_SIZE") == set()


def test_single_choice_asking_for_all_that_apply_is_reported():
    q = Question(qid="SS_CHANNELS", text="Indicate all the channels you sell through.",
                 type=QuestionType.SINGLE, options=[Option(code=1, label="Web"),
                                                    Option(code=2, label="Store")])
    assert "single_but_asks_for_all" in _found(_survey(q), "SS_CHANNELS")


def test_an_anchor_on_a_code_the_list_lacks_is_reported_but_a_borrowed_one_is_not():
    q = Question(qid="S4", text="Industries?", type=QuestionType.MULTI,
                 source_text="[PROGRAMMING: RANDOMIZE 1-7. ANCHOR 99]",
                 options=[Option(code=c, label=f"Industry {c}") for c in range(1, 9)])
    assert "anchor_or_exclusive_missing_code" in _found(_survey(q), "S4")
    source = Question(qid="DA_SUCCESS", text="Metrics?", type=QuestionType.MULTI,
                      options=[Option(code=1, label="ROI"), Option(code=98, label="Other")])
    top = Question(qid="DA_TOP_SUCCESS", text="Top metric?", type=QuestionType.SINGLE,
                   source_text="Randomize: Yes | Anchor 98",
                   options=[Option(code=1, label="ROI"), Option(code=98, label="Other")],
                   validation={"techniques": {"masks": [{"source_qid": "DA_SUCCESS",
                                                         "mode": "selected"}]}})
    assert "anchor_or_exclusive_missing_code" not in _found(_survey(source, top), "DA_TOP_SUCCESS")


def test_names_reused_odd_names_instructions_and_missing_sources_are_reported():
    reused = Question(qid="GA_NON_BARRIER_OE_2", text="Why not Meta?", type=QuestionType.OPEN_TEXT,
                      validation={"original_qid": "GA_NON_BARRIER_OE"})
    odd = Question(qid="C_TEAM_S(ZE", text="Team size?", type=QuestionType.SINGLE,
                   options=[Option(code=1, label="1"), Option(code=2, label="2+")])
    note = Question(qid="Q24b", text="Dislike?", type=QuestionType.SINGLE,
                    options=[Option(code=1, label="Too long"),
                             Option(code=2, label="RANDOMIZE ORDER. ENABLE ZOOM")])
    borrow = Question(qid="DA_SHARE", text="Share?", type=QuestionType.MULTI,
                      options=[Option(code=1, label="Google")],
                      validation={"techniques": {"masks": [{"source_qid": "DA_USAGE",
                                                            "mode": "selected"}]}})
    s = _survey(reused, odd, note, borrow)
    assert "name_reused" in _found(s, "GA_NON_BARRIER_OE_2")
    assert "odd_question_name" in _found(s, "C_TEAM_S(ZE")
    assert "instruction_as_answer" in _found(s, "Q24b")
    assert "list_source_missing" in _found(s, "DA_SHARE")

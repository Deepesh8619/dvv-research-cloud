"""The QA loop: QA finds, the programmer agent fixes, QA re-checks.

Each test is a real defect from the TRD and Vivo studies, reduced to its shape.
"""

from src.core.survey_definition import Option, Question, QuestionType, SurveyDefinition
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

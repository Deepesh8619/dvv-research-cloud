"""Small note forms: made-up documents, extracted with the model off, then compiled."""

from __future__ import annotations

from unittest.mock import patch

from src.ai.router import BackendUnavailable
from src.core.survey_definition import Expression, QuestionType, evaluate
from src.engines.questionnaire.extractor import extract_survey_definition
from src.engines.questionnaire.note_ledger import read_notes
from src.intake.questionnaire_loader import DocumentBlock, QuestionChunk
from src.renderers.surveyjs_compiler import compile_to_surveyjs

BRANDS = "Aurex | 1\nKestrel | 2\nNone | 99"


def _extract(*texts: str):
    chunks = [QuestionChunk(chunk_text=t, candidate_qid=t.split(".")[0], provenance="t",
                            source_blocks=[DocumentBlock("paragraph", t, 0)]) for t in texts]
    with patch("src.engines.questionnaire.extractor.run_task",
               side_effect=BackendUnavailable("off")):
        return extract_survey_definition(chunks, study_id="s")


def _elements(survey) -> dict:
    compiled = compile_to_surveyjs(survey)
    return {e["name"]: e for p in compiled["pages"] for e in p["elements"]}


def _by(survey) -> dict:
    return {q.qid: q for q in survey.questions}


def _outcomes(survey, qid: str) -> dict:
    return {r["kind"]: r["outcome"] for r in read_notes(survey) if r["qid"] == qid}


def test_continue_if_is_a_terminate_outside_the_range():
    survey = _extract("S1. How old are you? (NUMBER)\n[PN: CONTINUE IF 13-17.]")
    term = _by(survey)["S1"].terminate_if
    assert term.op == "not"
    assert [evaluate(term, {"S1": age}) for age in (12, 13, 17, 18)] == [True, False, False, True]
    assert _outcomes(survey, "S1")["continue_if"] == "applied"


def test_continue_if_never_overwrites_a_terminate():
    survey = _extract("S1. How old are you? (NUMBER)\n[PN: If less than 18, terminate.]\n"
                      "[PN: CONTINUE IF 13-17.]")
    term = _by(survey)["S1"].terminate_if
    assert term.op == "lt" and term.value == 18
    assert _outcomes(survey, "S1")["continue_if"] == "unread"


def test_continue_if_on_a_list_question_is_left_alone():
    survey = _extract("S1. Which brand? (SINGLE)\nAurex | 1\nKestrel | 2\n[PN: CONTINUE IF 1-2.]")
    assert _by(survey)["S1"].terminate_if is None
    assert _outcomes(survey, "S1")["continue_if"] == "unread"


def test_up_to_ten_responses_compiles_that_many_boxes():
    survey = _extract("Q1. What else comes to mind? (OPEN)\n[PN: ALLOW UP TO 10 RESPONSES]")
    assert _by(survey)["Q1"].validation["max_responses"] == 10
    el = _elements(survey)["Q1"]
    assert el["type"] == "multipletext" and len(el["items"]) == 10
    assert all("isRequired" not in item for item in el["items"])
    _by(survey)["Q1"].required = True
    items = _elements(survey)["Q1"]["items"]
    assert items[0]["isRequired"] is True and "isRequired" not in items[1]
    assert _outcomes(survey, "Q1")["max_responses"] == "applied"
    assert _outcomes(survey, "Q1")["limit_select"] == "applied"


def test_responses_limit_is_ignored_off_an_open_end():
    survey = _extract(f"Q1. Which brands? (MULTI)\n{BRANDS}\n[PN: ALLOW UP TO 10 RESPONSES]")
    assert "max_responses" not in (_by(survey)["Q1"].validation or {})
    assert _outcomes(survey, "Q1")["max_responses"] == "unread"
    assert _elements(survey)["Q1"]["type"] == "checkbox"


def _grid(note: str, rows: str = "Aurex\nKestrel") -> str:
    return f"Q5. Rate each brand.\n[PN: {note}]\nCOLUMNS\nGood\nBad\nROWS\n{rows}"


def test_rows_shown_only_where_selected_at_any_source():
    survey = _extract(f"S2. Which have you used? (MULTI)\n{BRANDS}",
                      "S3. Which have you heard of? (MULTI)\nKestrel | 1\nAurex | 2\nNone | 99",
                      _grid("Ask for all brands selected at S2, S3."))
    rows = _by(survey)["Q5"].rows
    assert [r.show_if.op for r in rows] == ["or", "or"]
    aurex = rows[0].show_if
    assert evaluate(aurex, {"S2": [1]}) and evaluate(aurex, {"S3": [2]})
    assert not evaluate(aurex, {"S2": [2], "S3": [1]})
    shown = {r["value"]: r["visibleIf"] for r in _elements(survey)["Q5"]["rows"]}
    assert "S2" in shown[1] and "S3" in shown[1]
    assert _outcomes(survey, "Q5")["row_sources"] == "applied"


def test_a_row_no_source_offers_leaves_the_whole_note_unread():
    survey = _extract(f"S2. Which have you used? (MULTI)\n{BRANDS}",
                      _grid("Ask for all brands selected at S2.", "Aurex\nKestrel\nZorbo"))
    assert all(r.show_if is None for r in _by(survey)["Q5"].rows)
    assert _outcomes(survey, "Q5")["row_sources"] == "unread"


def test_a_source_that_comes_later_sets_nothing():
    survey = _extract(_grid("Ask for all brands selected at S2."),
                      f"S2. Which have you used? (MULTI)\n{BRANDS}")
    assert all(r.show_if is None for r in _by(survey)["Q5"].rows)


def test_allow_one_per_row_on_a_grid_is_by_design():
    survey = _extract(_grid("Allow one response per attribute."))
    assert _by(survey)["Q5"].type is QuestionType.GRID_SINGLE
    assert _outcomes(survey, "Q5")["one_or_many"] == "by_design"


def test_block_ask_with_a_range_gives_each_question_in_order_the_base():
    survey = _extract(
        "S1. Which services? (MULTI)\nInternet | 1\nTv | 2\n"
        "[PN: ASK A1, A3 in sequence if S1 = 1.]",
        "A1. First? (SINGLE)\nYes | 1\nNo | 2",
        "A1a. Second? (SINGLE)\nYes | 1\nNo | 2",
        "A2. Third? (SINGLE)\nYes | 1\nNo | 2",
        "A3. Fourth? (SINGLE)\nYes | 1\nNo | 2")
    by = _by(survey)
    based = [by[q].show_if is not None for q in ("A1", "A1a", "A2", "A3")]
    assert based == [True, False, False, True]
    assert _outcomes(survey, "S1")["block_ask"] == "applied"
    assert "visibleIf" in _elements(survey)["A3"]


def test_block_ask_range_means_document_order():
    survey = _extract(
        "S1. Which services? (MULTI)\nInternet | 1\nTv | 2\n[PN: ASK A1-A2 if S1 = 1.]",
        "A1. First? (SINGLE)\nYes | 1\nNo | 2",
        "A1a. Second? (SINGLE)\nYes | 1\nNo | 2",
        "A2. Third? (SINGLE)\nYes | 1\nNo | 2",
        "A3. Fourth? (SINGLE)\nYes | 1\nNo | 2")
    by = _by(survey)
    based = [by[q].show_if is not None for q in ("A1", "A1a", "A2", "A3")]
    assert based == [True, True, True, False]


def test_block_ask_with_an_unknown_name_or_a_later_rule_sets_nothing():
    survey = _extract(
        "S1. Which services? (MULTI)\nInternet | 1\nTv | 2\n[PN: ASK A1, A9 if S1 = 1.]\n"
        "[PN: ASK A2 in sequence if A3 = 1.]",
        "A1. First? (SINGLE)\nYes | 1\nNo | 2",
        "A2. Third? (SINGLE)\nYes | 1\nNo | 2",
        "A3. Fourth? (SINGLE)\nYes | 1\nNo | 2")
    # (A1 may carry a base from the section-path pass; the note rules set none.)
    assert survey.metadata["note_rules"] == []
    assert _by(survey)["A2"].show_if is None
    assert _outcomes(survey, "S1")["block_ask"] == "unread"


def test_block_ask_by_a_group_variable_the_document_defines():
    survey = _extract(
        "S1. Which services? (MULTI)\nInternet | 1\nTv | 2\n"
        "[PN: Create dummy variable called SERVICETYPE:]\n[PN: INTERNET IF S1=1]\n"
        "[PN: TV IF S1 = 2]\n[PN: ASK A1 and A2 in sequence if SERVICETYPE = 2.]",
        "A1. First? (SINGLE)\nYes | 1\nNo | 2",
        "A2. Second? (SINGLE)\nYes | 1\nNo | 2")
    by = _by(survey)
    assert by["A1"].show_if is not None and by["A1"].show_if.referenced_qids() == {"S1"}
    assert by["A2"].show_if is not None


def _purchase(note: str):
    grid = ("S4. Ever purchased each brand?\nCOLUMNS\nYes\nNo\nROWS\nBubble\nFizz")
    return _extract(grid, f"S5. Pick one. (SINGLE)\n[PN: {note}]\nA | 1\nB | 2",
                    "S6. Next? (SINGLE)\nA | 1\nB | 2", "S7. After? (SINGLE)\nA | 1\nB | 2")


def test_ask_of_all_ever_purchased_resolves_yes_and_the_row():
    survey = _purchase("ASK S6-S7 OF ALL EVER PURCHASED - S4 = YES FOR BUBBLE")
    by = _by(survey)
    base = by["S6"].show_if
    assert isinstance(base, Expression) and base.row == 1 and base.values == [1]
    assert by["S7"].show_if is not None and by["S5"].show_if is None
    assert _outcomes(survey, "S5")["block_ask"] == "applied"


def test_ask_of_all_sets_nothing_when_the_answer_or_row_does_not_resolve():
    for note in ("ASK S6-S7 OF ALL EVER PURCHASED - S4 = MAYBE FOR BUBBLE",
                 "ASK S6-S7 OF ALL EVER PURCHASED - S4 = YES FOR ZORBO",
                 "ASK S6-S7 OF ALL EVER PURCHASED - S4 = YES"):
        survey = _purchase(note)
        assert all(q.show_if is None for q in survey.questions), note
        assert _outcomes(survey, "S5")["block_ask"] == "unread"


def test_block_ask_lists_may_end_in_a_comma_and_use_comma_and():
    survey = _extract(
        "S1. Which services? (MULTI)\nInternet | 1\nTv | 2\n"
        "[PN: Ask A1, A1a, and A2, in sequence if S1 = 1.  If not, skip.]",
        "A1. First? (SINGLE)\nYes | 1\nNo | 2",
        "A1a. Second? (SINGLE)\nYes | 1\nNo | 2",
        "A2. Third? (SINGLE)\nYes | 1\nNo | 2")
    assert all(_by(survey)[q].show_if is not None for q in ("A1", "A1a", "A2"))

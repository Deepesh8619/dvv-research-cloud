"""Routing, codes and type cues written in layouts the parser had not seen.

Invented fixtures only. Each test is one vocabulary class, not one document.
"""

from __future__ import annotations

from src.core.survey_definition import QuestionType
from src.engines.questionnaire.mr_techniques import detect_techniques
from src.engines.questionnaire.prose_directives import parse_prose_question
from src.engines.questionnaire.routing_prose import (
    hoist_loose_routing,
    loose_base,
    loose_terminate,
)
from src.intake.questionnaire_loader import QuestionChunk


def _chunk(text: str, qid: str | None) -> QuestionChunk:
    return QuestionChunk(chunk_text=text, candidate_qid=qid, source_blocks=[], provenance="t")


def test_code_column_on_the_right_is_a_code_not_label_text():
    for sep in (" | ", "\t", " / "):
        text = f"A1. Which brand?\nAurex{sep}1\nKestrel{sep}2\nNone of these{sep}99"
        fields = parse_prose_question(text, "A1")
        assert [(o.code, o.label) for o in fields.options] == [
            (1, "Aurex"), (2, "Kestrel"), (99, "None of these")]


def test_code_column_on_the_left():
    fields = parse_prose_question("A1. Which?\n1 | Yes\n2 | No", "A1")
    assert [(o.code, o.label) for o in fields.options] == [(1, "Yes"), (2, "No")]


def test_a_lone_slash_in_wording_is_not_a_code():
    fields = parse_prose_question("A1. Which do you prefer, tea / 2 sugars?\nTea\nCoffee", "A1")
    assert [o.label for o in fields.options] == ["Tea", "Coffee"]


def test_base_line_is_recognised_and_read():
    assert loose_base("Base: All respondents") == (True, None)
    is_base, expr = loose_base("Base: Owners of a car (A1 > 0)")
    assert is_base and expr.op == "gt" and expr.qid == "A1"
    is_base, expr = loose_base("ASK THOSE WHO ANSWERED CODES a OR b AT Q4")
    assert expr.qid == "Q4" and expr.values == [1, 2]
    assert loose_base("Which brand do you own?") == (False, None)


def test_base_line_above_the_label_is_the_questions_base_not_an_option():
    fields = parse_prose_question("Base: Aware (Q2 = 1-3)\nQ3. Rate it.\nGood\nBad", "Q3")
    assert [o.label for o in fields.options] == ["Good", "Bad"]
    assert fields.show_if is not None and fields.show_if.referenced_qids() == {"Q2"}


def test_terminate_phrasings():
    for line in ("TERMINATE INTERVIEW IF S1 = 1", "SCREEN OUT IF S1 = 1", "END SURVEY IF S1 = 1",
                 "IF S1 = 1 - END SURVEY", "IF S1 = 1: TERMINATE & THANK", "[CLOSE IF S1 = 1]"):
        is_term, expr = loose_terminate(line, "S1")
        assert is_term and expr.qid == "S1" and expr.values == [1], line


def test_terminate_on_the_questions_own_answer():
    _, expr = loose_terminate("[IF <3 OR >35 YEARS - END]", "Q2")
    assert expr.op == "or" and [c.op for c in expr.children] == ["lt", "gt"]
    _, expr = loose_terminate("IF UNDER 18 OR OVER 70 - TERMINATE", "S2")
    assert expr.op == "or"


def test_terminate_line_below_the_options_reaches_the_question():
    fields = parse_prose_question("S1. Employed in the trade? (SINGLE)\nYes | 1\nNo | 2\n"
                                  "TERMINATE INTERVIEW IF S1 = 1", "S1")
    assert fields.terminate_if.qid == "S1" and fields.terminate_if.values == [1]
    assert [o.label for o in fields.options] == ["Yes", "No"]


def test_hoist_moves_base_forward_and_terminate_back():
    chunks = [
        _chunk("Base: All\nS1. First? (SINGLE)\nYes | 1\nNo | 2\nBase: Those with a car (A1 > 0)", "S1"),  # noqa: E501
        _chunk("SCREEN OUT IF S1 = 2\nBase: Those with a car (A1 > 0)", None),
        _chunk("S2. Second? (SINGLE)\nYes | 1\nNo | 2", "S2"),
    ]
    out = hoist_loose_routing(chunks)
    assert "SCREEN OUT IF S1 = 2" in out[0].chunk_text.splitlines()
    assert out[2].chunk_text.splitlines()[0].startswith("Base: Those with a car")
    assert "Base:" not in out[0].chunk_text.splitlines()[-1]


def test_documents_that_put_base_under_the_question_are_left_alone():
    chunks = [
        _chunk("S1. First? (SINGLE)\nYes | 1\nNo | 2\nBase: All", "S1"),
        _chunk("S2. Second? (SINGLE)\nYes | 1\nNo | 2\nBase: All", "S2"),
    ]
    assert hoist_loose_routing(chunks) == chunks


def test_multi_cues():
    for cue in ("(MULTI)", "MULTICODE", "Check all that apply.", "Tick all that apply.",
                "Mark every answer that applies. Select all", "Multiple answers possible."):
        fields = parse_prose_question(f"B1. Which have you heard of? {cue}\nA\nB\nC", "B1")
        assert fields.qtype is QuestionType.MULTI, cue
    fields = parse_prose_question("B1. Which one? (SINGLE)\nA\nB\nC", "B1")
    assert fields.qtype is QuestionType.SINGLE
    assert fields.text == "Which one?"


def test_scripter_tail_leaves_the_wording():
    fields = parse_prose_question("S2. How old are you? (NUMBER) (RANGE 18-99)", "S2")
    assert fields.text == "How old are you?"
    assert fields.qtype is QuestionType.NUMERIC and fields.bounds == {"min": 18, "max": 99}


def test_unbracketed_scripter_lines_are_not_answers():
    fields = parse_prose_question("B1. Which?\n(ROTATE)\nSHOW: CODES CHOSEN AT A1\nA | 1\nB | 2", "B1")  # noqa: E501
    assert [o.label for o in fields.options] == ["A", "B"]
    assert fields.randomize


def test_text_pipe_markers():
    for text in ("rate [PIPE: Q2] now", "rate {Q2} now", "rate <<Q2>> now", "rate [Q2 answer] now"):
        assert [p.source_qid for p in detect_techniques(text).pipes] == ["Q2"], text


def test_breaker_keeps_what_the_deterministic_parser_can_read():
    from unittest.mock import patch

    import pytest

    from src.ai.router import BackendUnavailable
    from src.engines.questionnaire.extractor import extract_survey_definition

    vague = [_chunk(f"Q{i}. Tell us about topic {i}?", f"Q{i}") for i in range(1, 4)]
    clear = _chunk("Q4. Do you smoke? (SINGLE)\nYes | 1\nNo | 2", "Q4")
    target = "src.engines.questionnaire.extractor.run_task"
    with patch(target, side_effect=BackendUnavailable("down")) as mock:
        survey = extract_survey_definition([*vague, clear], study_id="s")
    assert mock.call_count == 3
    smoke = next(q for q in survey.questions if q.qid == "Q4")
    assert [o.label for o in smoke.options] == ["Yes", "No"]

    with patch(target, side_effect=BackendUnavailable("down")), pytest.raises(BackendUnavailable):
        extract_survey_definition(vague, study_id="s")

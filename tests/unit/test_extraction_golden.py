"""The golden instrument: extraction must be exact, and the same every time.

Every defect fixed this week was found by running a real client questionnaire
and reading the output by hand. That does not scale and it does not repeat: the
same document extracted six times gave 95, 96, 97 and 107 questions, because
whatever the deterministic parser cannot settle goes to a model, and a model
does not answer the same way twice.

So this pins the part that must never drift. The fixture carries every shape
those documents had; the assertions are exact, not "roughly right"; and the
whole thing must extract with ZERO model calls — which is also what makes it
reproducible. A change that pushes any of this back to the LLM fails here, and
that is the point: each one is a question whose answer would start moving
between runs.
"""

import json

import pytest

from src.core.survey_definition import QuestionType
from src.engines.questionnaire import extractor as ex
from src.engines.questionnaire import schema
from src.intake.questionnaire_loader import segment_into_questions
from tests.fixtures.golden_instrument import golden_blocks


@pytest.fixture
def extracted(monkeypatch):
    """Extract the golden instrument with the model made unreachable.

    Not mocked to return something — made to RAISE. A mock that answers would
    hide the very thing being asserted: that nothing here needs it.
    """
    calls: list[str] = []

    def no_llm(task, *args, **kwargs):
        calls.append(task)
        raise AssertionError(f"extraction reached for the model: {task}")

    monkeypatch.setattr(ex, "run_task", no_llm)
    monkeypatch.setattr(ex, "verify_question", lambda chunk, result: result)
    survey = ex.extract_survey_definition(segment_into_questions(golden_blocks()), "golden")
    assert calls == []
    return survey


def _by_qid(survey):
    return {q.qid: q for q in survey.questions}


def test_the_whole_instrument_extracts_without_a_model_call(extracted):
    assert len(extracted.questions) == 12


def test_extraction_is_reproducible(monkeypatch):
    """Twice, byte for byte. Anything that moves between identical runs is a
    question whose answer a client cannot rely on."""
    monkeypatch.setattr(ex, "verify_question", lambda chunk, result: result)
    first = ex.extract_survey_definition(segment_into_questions(golden_blocks()), "golden")
    second = ex.extract_survey_definition(segment_into_questions(golden_blocks()), "golden")
    assert json.loads(first.to_json()) == json.loads(second.to_json())


def test_the_screener_reads_exactly(extracted):
    q = _by_qid(extracted)
    assert q["S0"].type is QuestionType.SINGLE
    assert [o.label for o in q["S0"].options] == ["Yes", "No"]
    assert q["S1"].type is QuestionType.NUMERIC


def test_a_multi_punch_keeps_its_anchor_and_exclusive(extracted):
    s6 = _by_qid(extracted)["S6"]
    assert s6.type is QuestionType.MULTI
    assert [o.label for o in s6.options] == [
        "Retailer A",
        "Retailer B",
        "Retailer C",
        "None of these",
    ]
    assert s6.randomize_options is True
    assert s6.options[-1].anchored and s6.options[-1].exclusive


def test_the_recruitment_path_routes_its_range_and_becomes_a_variable(extracted):
    q = _by_qid(extracted)
    hidden = [name for name in q if name.startswith("HID_")]
    assert len(hidden) == 1
    assert schema.FLAG_DERIVED_VARIABLE in q[hidden[0]].flags
    assert extracted.metadata["section_paths"][0]["questions"] == ["P1", "P2", "P3"]
    for qid in ("P1", "P2", "P3"):
        assert q[qid].show_if is not None, qid


def test_the_intro_screen_exists_and_is_conditional(extracted):
    intro = _by_qid(extracted)["INTRO_P1"]
    assert intro.type is QuestionType.INFO
    assert intro.show_if is not None
    assert "with your permission" in intro.text


def test_an_option_carrying_a_condition_survives(extracted):
    p2 = _by_qid(extracted)["P2"]
    assert [o.label for o in p2.options] == [
        "Yes - for a child under 18",
        "Yes - for another adult",
        "No, only for myself",
    ]


def test_both_grids_carry_rows_and_columns(extracted):
    q = _by_qid(extracted)
    assert [o.label for o in q["Q1"].rows] == ["Cleanser", "Moisturiser", "Sunscreen"]
    assert [o.label for o in q["Q1"].cols] == ["Every day", "Weekly", "Rarely"]
    assert [o.code for o in q["Q1"].cols] == [1, 2, 3]
    assert len(q["Q2"].rows) == 2 and len(q["Q2"].cols) == 6
    assert all("INTERNAL" not in o.label for o in q["Q2"].rows)


def test_a_note_is_dropped_and_a_long_option_is_kept(extracted):
    q3 = _by_qid(extracted)["Q3"]
    assert len(q3.options) == 2
    assert all("Note:" not in o.label for o in q3.options)
    assert "subscription service" in q3.options[1].label


def test_a_quota_table_never_becomes_a_grid(extracted):
    """It has a grid's shape with the answers already filled in. Reading it
    would invent a question nobody is asked — so it is flagged for a human
    instead, which is the honest outcome for something we cannot interpret."""
    c9 = _by_qid(extracted)["C9"]
    assert not c9.rows and not c9.cols
    assert schema.FLAG_EMPTY_OPTIONS in c9.flags


def test_a_base_naming_a_question_that_does_not_exist_is_flagged(extracted):
    """P3 says "[ASK IF C1=18+]" and this instrument has no C1 — exactly what a
    live study does. The base is read, and the dangling reference reported."""
    p3 = _by_qid(extracted)["P3"]
    assert schema.FLAG_UNRESOLVED_ROUTING_QID in p3.flags

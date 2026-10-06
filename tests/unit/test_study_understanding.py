"""The brief is read before the questions, and only what it says survives.

A model reads the instructions once. Everything it claims must quote the brief;
a claim it cannot quote is dropped, and a question name it invents is removed.
A rule the brief states about a question flags that question for the analyst —
it never edits the question.
"""

import pytest

from src.ai.router import AIResponse, BackendUnavailable
from src.engines.questionnaire import extractor as ex
from src.engines.questionnaire import schema
from src.engines.questionnaire import study_understanding as su
from src.intake.questionnaire_loader import DocumentBlock, segment_into_questions

_BRIEF_LINES = [
    "Bubble – Beyond The Viral Horizon Research",
    "Online Survey among Female Skincare Consumers",
    "N=1,200 from Consumer Panel",
    "N=600-800 from Bubble's CRM",
    "PN: Do not term respondents from Bubble's CRM",
]
_QUESTION_LINES = [
    "S0. Which sample source is this respondent from? Select one.",
    "1. Consumer Panel",
    "2. Bubble's CRM",
    "S2. Do you work in any of the following industries? Select all that apply.",
    "1. Advertising [TERMINATE]",
    "2. Healthcare",
]


def _blocks():
    return [DocumentBlock("paragraph", t, i) for i, t in enumerate(_BRIEF_LINES + _QUESTION_LINES)]


def _model_answer():
    return {
        "summary": "Skincare study among female consumers from a panel and Bubble's CRM.",
        "groups": [
            {"name": "CRM", "definition": "Bubble's own customers", "measured_by": ["S0"],
             "evidence": "N=600-800 from Bubble's CRM"},
        ],
        "rules": [
            {"instruction": "Do not term CRM respondents",
             "meaning": "Screen-outs apply to panel respondents only",
             "applies_to": ["S2", "Q99"],
             "evidence": "PN: Do not term respondents from Bubble’s CRM"},
            {"instruction": "Quota of n=50 teens", "meaning": "invented",
             "applies_to": ["S2"], "evidence": "Teens must be capped at n=50"},
        ],
    }


def _fake_model(content):
    def run(task, prompt, schema_, **kwargs):
        assert task == "questionnaire.understand"
        return AIResponse(content=content, backend="test", model="m")
    return run


def test_claims_the_brief_does_not_make_are_dropped():
    chunks = segment_into_questions(_blocks())
    grounded = su.ground(_model_answer(), su.brief_text(chunks), {"S0", "S2"})
    assert [r["instruction"] for r in grounded["rules"]] == ["Do not term CRM respondents"]
    assert grounded["claims_dropped_unsupported"] == 1
    # A question name not in the document is removed, the real one kept.
    assert grounded["rules"][0]["applies_to"] == ["S2"]


def test_the_brief_is_read_once_and_reports_its_model(monkeypatch):
    monkeypatch.setattr(su, "run_task", _fake_model(_model_answer()))
    result = su.understand_study(segment_into_questions(_blocks()))
    assert result["model"] == "test/m"
    assert result["summary"].startswith("Skincare study")


def test_an_unreachable_model_does_not_stop_extraction(monkeypatch):
    def down(*args, **kwargs):
        raise BackendUnavailable("no backend")
    monkeypatch.setattr(su, "run_task", down)
    assert "unavailable" in su.understand_study(segment_into_questions(_blocks()))


def test_a_document_with_no_brief_costs_no_call(monkeypatch):
    monkeypatch.setattr(su, "run_task", lambda *a, **k: pytest.fail("called the model"))
    chunks = segment_into_questions(
        [DocumentBlock("paragraph", t, i) for i, t in enumerate(_QUESTION_LINES)]
    )
    assert su.understand_study(chunks) is None


def test_a_question_s_prompt_carries_what_the_brief_says_about_it():
    grounded = su.ground(_model_answer(), su.brief_text(segment_into_questions(_blocks())),
                         {"S0", "S2"})
    context = su.context_for(grounded, "S2")
    assert "panel respondents only" in context
    assert "panel respondents only" in schema.build_residue_prompt(
        qid="S2", missing=["type"], study_context=context
    )
    assert "panel respondents only" in schema.build_extraction_prompt(
        "S2. text", "S2", study_context=context
    )
    # A question the brief says nothing specific about gets only the summary.
    assert "Instruction" not in su.context_for(grounded, "S0")


def test_a_governed_question_is_flagged_for_the_analyst_not_edited(monkeypatch):
    monkeypatch.setattr(su, "run_task", _fake_model(_model_answer()))
    calls = []

    def brief_as_display(task, prompt, schema_, **kwargs):
        # The opening chunk carries no question label and is read as a display
        # screen; it is the only chunk here that reaches a model.
        calls.append(task)
        assert "Viral Horizon" in prompt
        return AIResponse(
            content={"qid": None, "text": "Intro", "type": "info", "options": [],
                     "routing": {}, "confidence": {"text": 1.0}},
            backend="test", model="m",
        )

    monkeypatch.setattr(ex, "run_task", brief_as_display)
    monkeypatch.setattr(ex, "reconcile_segmentation", lambda blocks, chunks, meta: chunks)
    monkeypatch.setattr(ex, "verify_question", lambda chunk, result: result)
    blocks = _blocks()
    survey = ex.extract_survey_definition(segment_into_questions(blocks), "s", blocks=blocks)

    assert calls == ["questionnaire.extract"]
    s2 = next(q for q in survey.questions if q.qid == "S2")
    assert schema.FLAG_BRIEF_RULE in s2.flags
    assert "panel respondents only" in s2.validation["brief_rules"][0]
    assert "S2" not in survey.metadata["auto_approved_qids"]
    # The rule is quoted for a person to apply; the screen-out itself is untouched.
    assert s2.terminate_if is not None
    assert survey.metadata["study_understanding"]["rules"][0]["applies_to"] == ["S2"]

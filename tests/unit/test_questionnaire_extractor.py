"""Questionnaire extraction: LLM extract -> validate -> verify -> assemble.

All LLM calls go through `src.ai.router.run_task`, which is patched here so the
tests are fast and deterministic — no real API calls. We patch the name as
imported into the extractor module (`...extractor.run_task`), matching the
codebase pattern in test_navigator.py.
"""

from __future__ import annotations

from unittest.mock import patch

from src.ai.router import AIResponse, BackendUnavailable
from src.core.survey_definition import QuestionType
from src.engines.questionnaire import schema
from src.engines.questionnaire.extractor import (
    ExtractionResult,
    extract_question,
    extract_survey_definition,
    verify_question,
)
from src.intake.questionnaire_loader import QuestionChunk

_EXTRACT = "src.engines.questionnaire.extractor.run_task"


def _chunk(qid: str = "Q1", text: str = "Q1. Are you aware of Brand X?") -> QuestionChunk:
    return QuestionChunk(
        chunk_text=text,
        candidate_qid=qid,
        source_blocks=[],
        provenance="para 1",
    )


def _response(content: dict) -> AIResponse:
    return AIResponse(content=content, backend="claude", model="test")


def _extraction_content(
    qid: str = "Q1",
    qtype: str = "single",
    options: list[dict] | None = None,
    routing: dict | None = None,
    confidence: dict | None = None,
) -> dict:
    return {
        "qid": qid,
        "text": "Sample question?",
        "type": qtype,
        "options": options if options is not None else [
            {"code": 1, "label": "Yes"},
            {"code": 2, "label": "No"},
        ],
        "routing": routing
        or {"verbatim_text": "", "ambiguous": False, "show_if": None, "terminate_if": None},
        "confidence": confidence or {"text": 0.9, "type": 0.9, "options": 0.9, "routing": 0.9},
    }


# --- extract_question: routing ---------------------------------------------


def test_unambiguous_routing_populates_show_if_no_flag():
    routing = {
        "verbatim_text": "ASK IF Q3 = YES (code 1)",
        "ambiguous": False,
        "show_if": {"op": "eq", "qid": "Q3", "value": 1},
        "terminate_if": None,
    }
    content = _extraction_content(qid="Q7", routing=routing)

    with patch(_EXTRACT, return_value=_response(content)):
        result = extract_question(_chunk("Q7"))

    assert result.question.show_if is not None
    assert result.question.show_if.qid == "Q3"
    assert result.question.show_if.value == 1
    # verbatim sentence attached to the structured expression.
    assert result.question.show_if.verbatim_text == "ASK IF Q3 = YES (code 1)"
    assert schema.FLAG_AMBIGUOUS_ROUTING not in result.flags


def test_ambiguous_routing_leaves_show_if_null_and_flags():
    routing = {
        "verbatim_text": "ASK IF AWARE OF BRAND",
        "ambiguous": True,
        "show_if": None,
        "terminate_if": None,
    }
    content = _extraction_content(qid="Q7", routing=routing)

    with patch(_EXTRACT, return_value=_response(content)):
        result = extract_question(_chunk("Q7"))

    assert result.question.show_if is None
    assert schema.FLAG_AMBIGUOUS_ROUTING in result.flags
    # verbatim preserved in validation metadata even with no structured routing.
    assert result.question.validation["routing_verbatim"] == "ASK IF AWARE OF BRAND"


def test_low_routing_confidence_flags_ambiguous():
    content = _extraction_content(
        confidence={"text": 0.9, "type": 0.9, "options": 0.9, "routing": 0.2}
    )
    with patch(_EXTRACT, return_value=_response(content)):
        result = extract_question(_chunk())

    assert schema.FLAG_AMBIGUOUS_ROUTING in result.flags


# --- extract_question: deterministic validation (no LLM dependency) --------


def test_duplicate_option_code_flagged_deterministically():
    """The duplicate-code check must fire from validation alone, not the LLM."""
    content = _extraction_content(
        options=[
            {"code": 1, "label": "Yes"},
            {"code": 1, "label": "Also yes"},  # duplicate code
        ]
    )
    # If run_task is called at all it returns this content; the assertion is
    # that the flag comes from deterministic validation of the parsed options,
    # not from any verification/LLM signal.
    with patch(_EXTRACT, return_value=_response(content)) as mock_run:
        result = extract_question(_chunk())

    assert schema.FLAG_DUPLICATE_OPTION_CODE in result.flags
    # Exactly one call (the extraction) — no LLM call needed for the dup check.
    assert mock_run.call_count == 1


def test_empty_options_on_single_type_flagged():
    content = _extraction_content(options=[])
    with patch(_EXTRACT, return_value=_response(content)):
        result = extract_question(_chunk())

    assert schema.FLAG_EMPTY_OPTIONS in result.flags


def test_self_referential_routing_flagged_ambiguous():
    routing = {
        "verbatim_text": "SHOW IF Q7 = 1",
        "ambiguous": False,
        "show_if": {"op": "eq", "qid": "Q7", "value": 1},  # references itself
        "terminate_if": None,
    }
    content = _extraction_content(qid="Q7", routing=routing)
    with patch(_EXTRACT, return_value=_response(content)):
        result = extract_question(_chunk("Q7"))

    assert schema.FLAG_AMBIGUOUS_ROUTING in result.flags


# --- extract_question: backend failure -------------------------------------


def test_backend_unavailable_returns_flagged_result_no_raise():
    with patch(_EXTRACT, side_effect=BackendUnavailable("offline")):
        result = extract_question(_chunk("Q9"))

    assert isinstance(result, ExtractionResult)
    assert schema.FLAG_EXTRACTION_UNAVAILABLE in result.flags
    # Placeholder preserves the source so a human can extract by hand.
    assert result.question.qid == "Q9"
    assert result.question.type == QuestionType.INFO
    assert result.question.source_text


# --- verify_question -------------------------------------------------------


def _clean_result() -> ExtractionResult:
    content = _extraction_content()
    with patch(_EXTRACT, return_value=_response(content)):
        return extract_question(_chunk())


def test_verify_faithful_leaves_confidence_untouched():
    result = _clean_result()
    before = dict(result.confidence)
    verify_content = {"faithful": True, "discrepancies": []}

    with patch(_EXTRACT, return_value=_response(verify_content)):
        result = verify_question(_chunk(), result)

    assert result.verified is True
    assert result.confidence == before
    assert schema.FLAG_VERIFICATION_DISCREPANCY not in result.flags


def test_verify_discrepancy_downgrades_targeted_confidence():
    result = _clean_result()
    verify_content = {
        "faithful": False,
        "discrepancies": ["invented option 'Maybe' not in source"],
    }
    with patch(_EXTRACT, return_value=_response(verify_content)):
        result = verify_question(_chunk(), result)

    assert schema.FLAG_VERIFICATION_DISCREPANCY in result.flags
    # "option" keyword -> options confidence halved (0.9 -> 0.45); others intact.
    assert result.confidence["options"] == 0.45
    assert result.confidence["text"] == 0.9
    # Mirrored onto the Question.
    assert result.question.extraction_confidence["options"] == 0.45


def test_verify_unavailable_flags_and_returns_unchanged():
    result = _clean_result()
    before = dict(result.confidence)
    with patch(_EXTRACT, side_effect=BackendUnavailable("offline")):
        result = verify_question(_chunk(), result)

    assert schema.FLAG_VERIFICATION_UNAVAILABLE in result.flags
    assert result.verified is False
    assert result.confidence == before


# --- extract_survey_definition ---------------------------------------------


def test_survey_definition_from_clean_chunks_validates():
    chunks = [_chunk("Q1"), _chunk("Q2", "Q2. Overall satisfaction?")]

    extraction_by_qid = {
        "Q1": _extraction_content(qid="Q1"),
        "Q2": _extraction_content(qid="Q2"),
    }

    def fake_run(task, prompt, schema_arg, *args, **kwargs):
        if task == "questionnaire.verify":
            return _response({"faithful": True, "discrepancies": []})
        # Extraction: pick content by which qid the prompt is about.
        qid = "Q2" if "Q2" in prompt else "Q1"
        return _response(extraction_by_qid[qid])

    with patch(_EXTRACT, side_effect=fake_run):
        survey = extract_survey_definition(chunks, study_id="study-42")

    assert survey.study_id == "study-42"
    assert survey.source == "extracted"
    assert survey.status == "draft"
    assert len(survey.questions) == 2
    assert len(survey.blocks) == 1
    assert survey.blocks[0].question_ids == ["Q1", "Q2"]
    # Clean inputs -> no structural validation errors.
    assert survey.metadata["validation_errors"] == []
    summary = survey.metadata["extraction_summary"]
    assert summary["total_chunks"] == 2
    assert summary["verified_chunks"] == 2


def test_unresolved_routing_qid_flagged_and_nulled():
    """Q2's show_if references Q9, which exists in no chunk -> flag + null it."""
    chunks = [_chunk("Q1"), _chunk("Q2", "Q2. Something?")]

    q2_routing = {
        "verbatim_text": "ASK IF Q9 = 1",
        "ambiguous": False,
        "show_if": {"op": "eq", "qid": "Q9", "value": 1},
        "terminate_if": None,
    }
    extraction_by_qid = {
        "Q1": _extraction_content(qid="Q1"),
        "Q2": _extraction_content(qid="Q2", routing=q2_routing),
    }

    def fake_run(task, prompt, schema_arg, *args, **kwargs):
        if task == "questionnaire.verify":
            return _response({"faithful": True, "discrepancies": []})
        qid = "Q2" if "Q2" in prompt else "Q1"
        return _response(extraction_by_qid[qid])

    with patch(_EXTRACT, side_effect=fake_run):
        survey = extract_survey_definition(chunks, study_id="study-99")

    q2 = survey["Q2"]
    assert q2.show_if is None  # unresolvable condition nulled out
    assert schema.FLAG_UNRESOLVED_ROUTING_QID in q2.flags
    # Verbatim preserved so a human can repair the routing by hand.
    assert q2.validation["routing_verbatim"] == "ASK IF Q9 = 1"
    assert schema.FLAG_UNRESOLVED_ROUTING_QID in survey.metadata[
        "extraction_summary"
    ]["flag_counts"]

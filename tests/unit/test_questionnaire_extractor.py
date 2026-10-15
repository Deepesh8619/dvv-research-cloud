"""Questionnaire extraction: LLM extract -> validate -> verify -> assemble.

All LLM calls go through `src.ai.router.run_task`, which is patched here so the
tests are fast and deterministic — no real API calls. We patch the name as
imported into the extractor module (`...extractor.run_task`), matching the
codebase pattern in test_navigator.py.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.ai.router import AIResponse, BackendUnavailable, ModelOutputError
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
# reconciler.py has its own module-level import of run_task; a test that
# passes `blocks` must patch this too or it hits the network.
_RECONCILE = "src.engines.questionnaire.reconciler.run_task"


def _chunk(qid: str = "Q1", text: str = "Q1. Are you aware of Brand X?") -> QuestionChunk:
    return QuestionChunk(
        chunk_text=text,
        candidate_qid=qid,
        source_blocks=[],
        provenance="para 1",
    )


def _response(content: dict) -> AIResponse:
    return AIResponse(content=content, backend="groq", model="test")


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
        # Extraction: pick content by which chunk the prompt is about. Match on
        # the wording, not on the bare qid — the prompt's guidance block cites
        # example qids ("[SHOW Q26 LIST]"), so a substring test for "Q2" matched
        # the Q1 prompt and silently swapped the two questions.
        qid = "Q2" if "Overall satisfaction?" in prompt else "Q1"
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


def test_auto_approved_qids_are_the_clean_verified_high_confidence_ones():
    """Exception-based review: only questions that verified, carry no flag, AND
    whose lowest field confidence >= the floor land in auto_approved_qids. Q3
    has no flags but a below-floor options confidence, so it is NOT pre-approved."""
    chunks = [
        _chunk("Q1"),
        _chunk("Q2", "Q2. Overall satisfaction?"),
        _chunk("Q3", "Q3. Anything else?"),
    ]
    extraction_by_qid = {
        "Q1": _extraction_content(qid="Q1"),
        "Q2": _extraction_content(qid="Q2"),
        # Below the 0.85 floor on options -> clean of flags but not pre-approved.
        "Q3": _extraction_content(
            qid="Q3",
            confidence={"text": 0.9, "type": 0.9, "options": 0.5, "routing": 0.9},
        ),
    }

    def fake_run(task, prompt, schema_arg, *args, **kwargs):
        if task == "questionnaire.verify":
            return _response({"faithful": True, "discrepancies": []})
        # Match on the segmenter's qid-hint line, not a bare "Q3" (which also
        # appears in the prompt's routing example "ASK IF Q3 = YES").
        qid = next(q for q in ("Q1", "Q2", "Q3") if f"id as: {q}" in prompt)
        return _response(extraction_by_qid[qid])

    with patch(_EXTRACT, side_effect=fake_run):
        survey = extract_survey_definition(chunks, study_id="study-clean")

    assert survey.metadata["auto_approved_qids"] == ["Q1", "Q2"]


def test_unverified_question_is_not_auto_approved():
    """A question the verify pass could not run on (VERIFICATION_UNAVAILABLE) is
    never pre-approved, even with high confidence — verified must be True."""
    chunks = [_chunk("Q1")]

    def fake_run(task, prompt, schema_arg, *args, **kwargs):
        if task == "questionnaire.verify":
            raise BackendUnavailable("verify offline")
        return _response(_extraction_content(qid="Q1"))

    with patch(_EXTRACT, side_effect=fake_run):
        survey = extract_survey_definition(chunks, study_id="study-unverified")

    assert survey.metadata["auto_approved_qids"] == []


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


# --- T16: failure diagnostics + flag taxonomy ------------------------------


def test_config_error_flag_and_detail_preserved():
    """A missing-key BackendUnavailable (config_error=True) -> CONFIG_ERROR flag,
    NOT the transient UNAVAILABLE flag, with type + detail preserved verbatim."""
    exc = BackendUnavailable("GROQ_API_KEY not set", config_error=True)
    with patch(_EXTRACT, side_effect=exc):
        result = extract_question(_chunk("Q9"))

    assert schema.FLAG_EXTRACTION_CONFIG_ERROR in result.flags
    assert schema.FLAG_EXTRACTION_UNAVAILABLE not in result.flags
    err = result.question.validation["extraction_error"]
    assert err["error_type"] == "BackendUnavailable"
    assert err["error_detail"] == "GROQ_API_KEY not set"


def test_transient_backend_error_uses_unavailable_flag():
    """A BackendUnavailable without config_error (unreachable/timeout) stays on
    the transient UNAVAILABLE flag, distinct from the config case."""
    with patch(_EXTRACT, side_effect=BackendUnavailable("claude API unreachable: timeout")):
        result = extract_question(_chunk("Q9"))

    assert schema.FLAG_EXTRACTION_UNAVAILABLE in result.flags
    assert schema.FLAG_EXTRACTION_CONFIG_ERROR not in result.flags
    err = result.question.validation["extraction_error"]
    assert err["error_type"] == "BackendUnavailable"


def test_model_output_error_flagged_distinctly():
    """A ModelOutputError (schema-rejected output) is a per-chunk MODEL_ERROR,
    distinct from both backend flags, with its detail preserved."""
    with patch(_EXTRACT, side_effect=ModelOutputError("model output missing required keys")):
        result = extract_question(_chunk("Q9"))

    assert schema.FLAG_EXTRACTION_MODEL_ERROR in result.flags
    assert schema.FLAG_EXTRACTION_CONFIG_ERROR not in result.flags
    assert schema.FLAG_EXTRACTION_UNAVAILABLE not in result.flags
    err = result.question.validation["extraction_error"]
    assert err["error_type"] == "ModelOutputError"


def test_verification_error_preserved_on_question():
    """A failed verify pass stashes verification_error (distinct key) + flags."""
    result = _clean_result()
    exc = BackendUnavailable("GROQ_API_KEY not set", config_error=True)
    with patch(_EXTRACT, side_effect=exc):
        result = verify_question(_chunk(), result)

    assert schema.FLAG_VERIFICATION_UNAVAILABLE in result.flags
    err = result.question.validation["verification_error"]
    assert err["error_type"] == "BackendUnavailable"
    assert err["error_detail"] == "GROQ_API_KEY not set"


# --- T16: document-level circuit breaker -----------------------------------


def test_circuit_break_after_three_backend_failures():
    """First 3 chunks all fail with BackendUnavailable -> abort and raise, and
    run_task is NOT called for chunks 4+ (proves the breaker stops EARLY, not
    just that it eventually errors). Extract-only per doomed chunk: 3 calls."""
    chunks = [_chunk(f"Q{i}", f"Q{i}. Question {i}?") for i in range(1, 9)]  # 8 chunks

    with patch(
        _EXTRACT,
        side_effect=BackendUnavailable("GROQ_API_KEY not set", config_error=True),
    ) as mock_run:
        with pytest.raises(BackendUnavailable) as excinfo:
            extract_survey_definition(chunks, study_id="study-down")

    # Circuit breaker fired: exactly 3 extract attempts, verify never reached,
    # chunks 4-8 never touched.
    assert mock_run.call_count == 3
    # The raised error names the root cause (propagates to the route's error flip).
    assert "GROQ_API_KEY not set" in str(excinfo.value)
    assert excinfo.value.config_error is True


def test_model_output_error_does_not_circuit_break():
    """A ModelOutputError on every chunk is a per-document problem, not a backend
    outage — it accumulates per-chunk flags and does NOT trigger the breaker, so
    the whole document is processed (all chunks attempted)."""
    chunks = [_chunk(f"Q{i}", f"Q{i}. Question {i}?") for i in range(1, 6)]  # 5 chunks

    def fake_run(task, prompt, schema_arg, *args, **kwargs):
        # Extraction always fails schema; verify (reached because backend is up)
        # returns faithful so we can confirm the pipeline kept going.
        if task == "questionnaire.verify":
            return _response({"faithful": True, "discrepancies": []})
        raise ModelOutputError("model output missing required keys")

    with patch(_EXTRACT, side_effect=fake_run):
        survey = extract_survey_definition(chunks, study_id="study-badmodel")

    # No raise: all 5 chunks produced (placeholder) questions.
    assert len(survey.questions) == 5
    summary = survey.metadata["extraction_summary"]
    assert summary["flag_counts"][schema.FLAG_EXTRACTION_MODEL_ERROR] == 5
    # failure_reasons counter keyed by error_type is populated.
    assert summary["failure_reasons"]["ModelOutputError"] == 5


def test_no_circuit_break_when_one_chunk_succeeds_first():
    """The breaker only fires if the FIRST N chunks ALL fail. A success in the
    first 3 resets the streak, so a later backend failure never aborts the run."""
    chunks = [_chunk(f"Q{i}", f"Q{i}. Question {i}?") for i in range(1, 6)]

    def fake_run(task, prompt, schema_arg, *args, **kwargs):
        if task == "questionnaire.verify":
            return _response({"faithful": True, "discrepancies": []})
        if "Q1" in prompt:  # first chunk succeeds
            return _response(_extraction_content(qid="Q1"))
        raise BackendUnavailable("GROQ_API_KEY not set", config_error=True)

    with patch(_EXTRACT, side_effect=fake_run):
        survey = extract_survey_definition(chunks, study_id="study-mixed")

    # No raise — one early success means the streak never reaches 3-from-start.
    assert len(survey.questions) == 5


# --- field-semantics: derived vars, terminate shapes, placeholder honesty ----


def test_hid_prefixed_qid_forced_info_and_flagged():
    """A HID_ (hidden/derived) variable is not a real question: force type=info
    and flag DERIVED_VARIABLE even though the LLM returned a 'single' with options."""
    content = _extraction_content(qid="HID_COMP_SIZE", qtype="single")
    chunk = _chunk("HID_COMP_SIZE", "Q Name: HID_COMP_SIZE\nOther details: (blank)")

    with patch(_EXTRACT, return_value=_response(content)):
        result = extract_question(chunk)

    assert result.question.type == QuestionType.INFO
    assert schema.FLAG_DERIVED_VARIABLE in result.flags
    # type=info suppresses the empty-options check (would be irrelevant noise).
    assert schema.FLAG_EMPTY_OPTIONS not in result.flags


def test_terminate_range_expression_shape_is_built():
    """The pipeline correctly assembles the range-terminate Expression the prompt
    is designed to elicit ('TERMINATE if 3-99 selected' -> value>2 AND value<100)."""
    routing = {
        "verbatim_text": "TERMINATE if 3-99 selected",
        "ambiguous": False,
        "show_if": None,
        "terminate_if": {
            "op": "and",
            "children": [
                {"op": "gt", "qid": "SS_EMP_STATUS", "value": 2},
                {"op": "lt", "qid": "SS_EMP_STATUS", "value": 100},
            ],
        },
    }
    content = _extraction_content(qid="SS_EMP_STATUS", routing=routing)
    with patch(_EXTRACT, return_value=_response(content)):
        result = extract_question(_chunk("SS_EMP_STATUS"))

    term = result.question.terminate_if
    assert term is not None
    assert term.op == "and"
    assert {c.op for c in term.children} == {"gt", "lt"}
    # Verbatim sentence is attached back to the structured expression.
    assert term.verbatim_text == "TERMINATE if 3-99 selected"


def test_placeholder_value_routing_flagged_ambiguous_no_invented_value():
    """A source with an unfilled placeholder ('age>??') must not get an invented
    number: the model sets ambiguous=true + null structured routing, and the
    verbatim (with the '??') is preserved for a human to resolve."""
    routing = {
        "verbatim_text": "TERMINATE if age<25 or age>?? OR if 99 selected",
        "ambiguous": True,
        "show_if": None,
        "terminate_if": None,
    }
    content = _extraction_content(qid="RESP_AGE", routing=routing)
    with patch(_EXTRACT, return_value=_response(content)):
        result = extract_question(_chunk("RESP_AGE"))

    assert result.question.terminate_if is None  # no invented structure
    assert schema.FLAG_AMBIGUOUS_ROUTING in result.flags
    assert "??" in result.question.validation["routing_verbatim"]


def test_profile_hint_selects_field_spec_and_conservative_guidance():
    """The detected profile hint drives which guidance the prompt appends."""
    prose = "Q1. What is your age?"
    # field_spec hint appends the spec-table mapping even for a chunk that lacks
    # the labels (whole-doc detection said this document is GBK).
    gbk = schema.build_extraction_prompt(prose, "Q1", profile_hint="field_spec")
    assert "PROGRAMMING-SPEC TABLE" in gbk

    # conservative hint (unknown format) appends the caution note, no spec table.
    unknown = schema.build_extraction_prompt(prose, "Q1", profile_hint="conservative")
    assert "UNRECOGNIZED FORMAT" in unknown
    assert "PROGRAMMING-SPEC TABLE" not in unknown


def test_unknown_profile_prompt_is_identical_to_today_no_regression():
    """A prose chunk with the 'prose' hint (or no hint) must produce EXACTLY the
    same prompt it did before profiles existed — no regression on recognized-but-
    unremarkable or unknown documents."""
    prose = "Q1. What is your age?"
    baseline = schema.build_extraction_prompt(prose, "Q1")
    assert schema.build_extraction_prompt(prose, "Q1", profile_hint=None) == baseline
    assert schema.build_extraction_prompt(prose, "Q1", profile_hint="prose") == baseline


def test_gbk_document_detection_appends_field_spec_end_to_end():
    """A GBK-detected document routes the field-spec guidance into every chunk's
    extraction prompt, and stashes the profile on survey.metadata."""
    from src.intake.questionnaire_loader import DocumentBlock

    blocks = [
        DocumentBlock("table", "Q Name: RESP_AGE\nResponse list:\n1: Yes", 0,
                      rows=[["Q Name", "RESP_AGE"]]),
        DocumentBlock("table", "Q Name: SS_EMP\nResponse list:\n1: Yes", 1,
                      rows=[["Q Name", "SS_EMP"]]),
    ]
    chunks = [
        _chunk("RESP_AGE", "Q Name: RESP_AGE\nResponse list:\n1: Yes"),
        _chunk("SS_EMP", "Q Name: SS_EMP\nResponse list:\n1: Yes"),
    ]
    seen_prompts: list[str] = []

    def fake_run(task, prompt, schema_arg, *args, **kwargs):
        if task == "questionnaire.extract":
            seen_prompts.append(prompt)
            qid = "SS_EMP" if "SS_EMP" in prompt else "RESP_AGE"
            return _response(_extraction_content(qid=qid))
        if task == "questionnaire.verify":
            return _response({"faithful": True, "discrepancies": []})
        # reconcile pass: nothing missed
        return _response({"missed_questions": []})

    with patch(_EXTRACT, side_effect=fake_run):
        survey = extract_survey_definition(chunks, study_id="study-gbk", blocks=blocks)

    assert survey.metadata["profile"]["slug"] == "gbk_spec_table"
    assert survey.metadata["profile"]["confidence"] >= 0.9
    # This used to assert every prompt carried the field-spec guidance. It no
    # longer can, and for a GOOD reason: a labelled spec table is now read
    # deterministically, so these questions reach a model only as a small
    # residue prompt — or not at all. The guidance itself is still asserted
    # directly in test_field_spec_guidance_appended_for_the_profile.
    assert not any("SOURCE CHUNK" in p for p in seen_prompts), (
        "spec-table questions should not need the full extraction prompt"
    )
    assert all("already been read" in p for p in seen_prompts), (
        "every remaining prompt should be a small residue prompt"
    )


def test_field_spec_guidance_appended_for_the_profile():
    """The prompt-level assertion the end-to-end test used to carry.

    Kept as a direct check so profile-specific guidance stays covered even
    though the deterministic parser means it is rarely reached in practice.
    """
    prompt = schema.build_extraction_prompt("Q Name | X", "X", "field_spec")
    assert "PROGRAMMING-SPEC TABLE" in prompt


def test_extraction_prompt_teaches_field_spec_when_convention_present():
    """When a chunk carries the programming-spec labels, the prompt includes the
    field-mapping guidance AND the do-not-invent-placeholder instruction."""
    with_spec = schema.build_extraction_prompt(
        "Q Name: RESP_AGE\nAmong who: (blank)\nOther details: Allow 0-999", "RESP_AGE"
    )
    assert "PROGRAMMING-SPEC TABLE" in with_spec
    assert "DO NOT invent a number" in with_spec
    assert "routing.ambiguous = true" in with_spec

    # The compound-OR worked example (numeric + placeholder + selected code) is
    # taught, so the model handles "age<25 OR age>?? OR if 99 selected" by leaving
    # terminate_if null rather than emitting a partial, under-terminating OR.
    assert "age>?? OR if 99 selected" in with_spec
    assert "UNDER-terminates" in with_spec

    # A plain prose chunk without the convention does not get the extra section.
    without = schema.build_extraction_prompt("Q1. What is your age?", "Q1")
    assert "PROGRAMMING-SPEC TABLE" not in without


# --- end-to-end route resolution (unpatched run_task) -----------------------


def test_extract_question_resolves_to_cerebras_with_only_cerebras_key(monkeypatch):
    """Smoke test the real routing path: run_task is NOT patched here. With only
    CEREBRAS_API_KEY set, `questionnaire.extract`'s "auto" chain must try local
    Ollama first (unreachable here), fall through to Cerebras, and drive
    extraction end-to-end. Only the network boundary (router.http_json) is
    stubbed, so this proves the chain wiring, backend dispatch, and result
    assembly all agree."""
    import urllib.error

    from src.ai import router as _router

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setenv("CEREBRAS_API_KEY", "csk")

    content = _extraction_content(qid="Q1")

    def fake_http_json(request, timeout):
        if "11434" in request.full_url:  # the Ollama-first attempt
            raise urllib.error.URLError("connection refused")
        assert "api.cerebras.ai" in request.full_url  # then falls through to Cerebras
        arguments = __import__("json").dumps(content)
        call = {"function": {"name": "emit", "arguments": arguments}}
        return {"choices": [{"message": {"tool_calls": [call]}}]}

    monkeypatch.setattr(_router, "http_json", fake_http_json)

    result = extract_question(_chunk("Q1"))

    assert result.question.qid == "Q1"
    assert result.question.type == QuestionType.SINGLE


def test_extract_question_resolves_to_groq_under_open_weights_default(monkeypatch):
    """The production path, end to end, with run_task NOT patched: Ollama
    unreachable -> Groq. A stray proprietary key in the environment is set on
    purpose — no code path exists that could use it, and this proves it."""
    import urllib.error

    from src.ai import router as _router

    monkeypatch.setenv("GROQ_API_KEY", "gk")
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    # Present, and must be irrelevant: no code path can reach that vendor.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai")

    content = _extraction_content(qid="Q1")
    seen: list[str] = []

    def fake_http_json(request, timeout):
        seen.append(request.full_url)
        if "11434" in request.full_url:  # the Ollama-first attempt
            raise urllib.error.URLError("connection refused")
        assert "api.groq.com" in request.full_url
        arguments = __import__("json").dumps(content)
        call = {"function": {"name": "emit", "arguments": arguments}}
        return {"choices": [{"message": {"tool_calls": [call]}}]}

    monkeypatch.setattr(_router, "http_json", fake_http_json)

    result = extract_question(_chunk("Q1"))

    assert result.question.qid == "Q1"
    assert not any("api.openai.com" in u for u in seen), (
        "a confidential questionnaire reached a proprietary vendor"
    )


def test_routing_returned_as_a_bare_string_does_not_crash():
    """A weak model emitting `routing` as a string must not break extraction.

    Found by scripts/benchmark_extraction.py against a real client document on
    llama3.2:3b: the string form raised AttributeError out of
    _question_from_raw, violating extract_question's contract of never raising
    on model output. The sentence is kept as the routing verbatim (it is what a
    reviewer needs to resolve routing by hand) and flagged ambiguous.
    """
    content = _extraction_content(qid="Q4")
    content["routing"] = "TERMINATE IF Q3 = 5"

    with patch(_EXTRACT, return_value=_response(content)):
        result = extract_question(_chunk("Q4"))

    assert result.question.qid == "Q4"
    assert result.question.show_if is None
    assert result.question.validation["routing_verbatim"] == "TERMINATE IF Q3 = 5"
    assert schema.FLAG_AMBIGUOUS_ROUTING in result.flags


def test_routing_of_an_unexpected_type_is_ignored_not_fatal():
    content = _extraction_content(qid="Q5")
    content["routing"] = ["unexpected", "list"]

    with patch(_EXTRACT, return_value=_response(content)):
        result = extract_question(_chunk("Q5"))

    assert result.question.qid == "Q5"


# --- verify-skip: halve the LLM calls without weakening review ---------------


def _result(confidences: dict, flags=None):
    from src.engines.questionnaire.extractor import ExtractionResult

    r = ExtractionResult(question=_empty_q(), confidence=confidences)
    for f in flags or []:
        r.add_flag(f)
    return r


def _empty_q():
    from src.core.survey_definition import Question, QuestionType

    return Question(qid="Q1", text="t", type=QuestionType.SINGLE, options=[])


def test_maximally_confident_clean_extraction_skips_verification():
    """The point: verify is a SECOND LLM call, and doubles wall time on a
    token-capped tier."""
    from src.engines.questionnaire.extractor import _needs_verification

    clean = {"text": 1.0, "type": 1.0, "options": 0.98, "routing": 0.96}
    assert _needs_verification(_result(clean)) is False


def test_any_flag_forces_verification():
    from src.engines.questionnaire.extractor import _needs_verification

    r = _result({"text": 1.0, "type": 1.0}, flags=[schema.FLAG_AMBIGUOUS_ROUTING])
    assert _needs_verification(r) is True


def test_one_merely_good_field_forces_verification():
    """The skip floor is deliberately higher than the auto-approve floor."""
    from src.engines.questionnaire.extractor import _needs_verification

    assert _needs_verification(_result({"text": 1.0, "options": 0.9})) is True


def test_missing_confidence_forces_verification():
    from src.engines.questionnaire.extractor import _needs_verification

    assert _needs_verification(_result({})) is True


def test_env_flag_forces_verification_everywhere(monkeypatch):
    from src.engines.questionnaire.extractor import _needs_verification

    monkeypatch.setenv("QRE_VERIFY_ALWAYS", "1")
    assert _needs_verification(_result({"text": 1.0, "type": 1.0})) is True


def test_a_skipped_question_is_never_auto_approved():
    """THE safety property. Skipping verification trades speed for a human
    review, never for an unreviewed auto-approval."""
    from src.engines.questionnaire.extractor import (
        _is_auto_approvable,
        _needs_verification,
    )

    r = _result({"text": 1.0, "type": 1.0, "options": 1.0, "routing": 1.0})
    assert _needs_verification(r) is False  # would skip verify...
    assert r.verified is False
    assert _is_auto_approvable(r) is False  # ...so it still reaches a human


def test_progress_is_reported_after_every_chunk():
    """Long extractions must be able to prove they are alive.

    A real 75-question run was killed at 15 minutes having persisted NOTHING,
    because the only signal of life was completion. The callback lets the caller
    heartbeat, so "slow but working" stops looking like "dead".
    """
    from src.engines.questionnaire.extractor import extract_survey_definition

    seen: list[tuple[int, int]] = []
    content = _extraction_content(qid="Q1")
    chunks = [_chunk(f"Q{i}") for i in range(1, 4)]

    with patch(_EXTRACT, return_value=_response(content)):
        extract_survey_definition(
            chunks, study_id="s1", on_progress=lambda d, t: seen.append((d, t))
        )

    assert seen == [(1, 3), (2, 3), (3, 3)]


def test_a_failing_chunk_still_reports_progress():
    """Otherwise a document of failures looks identical to a hung worker."""
    from src.engines.questionnaire.extractor import extract_survey_definition

    seen: list[tuple[int, int]] = []
    with patch(_EXTRACT, side_effect=ModelOutputError("bad json")):
        extract_survey_definition(
            [_chunk("Q1"), _chunk("Q2")],
            study_id="s1",
            on_progress=lambda d, t: seen.append((d, t)),
        )

    assert seen == [(1, 2), (2, 2)]


# --- checkpointing: a killed worker must not lose everything -----------------


def test_a_killed_worker_leaves_the_finished_questions_behind():
    """THE point of checkpointing.

    A real 75-question run was killed at 15 minutes having persisted ZERO
    questions, because results were only written at the end. Now a worker that
    dies partway leaves the completed questions as a reviewable questionnaire.
    """
    from src.engines.questionnaire.extractor import extract_survey_definition

    saved = {}

    def checkpoint(done, total, partial):
        saved["done"] = done
        saved["survey"] = partial

    class _Killed(Exception):
        pass

    calls = {"n": 0}

    # Maximally confident, so verification is skipped and each question is
    # exactly ONE call — otherwise "die on call 7" is an ambiguous number of
    # questions.
    perfect = {"text": 1.0, "type": 1.0, "options": 1.0, "routing": 1.0}

    def die_on_the_seventh(task, prompt, schema_arg, *a, **k):
        calls["n"] += 1
        if calls["n"] > 6:
            raise _Killed("worker killed")   # simulates a restart mid-run
        return _response(
            _extraction_content(qid=f"Q{calls['n']}", confidence=perfect)
        )

    chunks = [_chunk(f"Q{i}") for i in range(1, 11)]
    with patch(_EXTRACT, side_effect=die_on_the_seventh):
        try:
            extract_survey_definition(chunks, study_id="s1", on_checkpoint=checkpoint,
                                      checkpoint_every=5)
        except _Killed:
            pass

    # Five questions were checkpointed before the worker died.
    assert saved["done"] == 5
    assert len(saved["survey"].questions) == 5
    # And it is a real, reviewable questionnaire, not a fragment.
    assert saved["survey"].blocks
    assert saved["survey"].study_id == "s1"


def test_a_checkpoint_is_marked_partial():
    """Cross-chunk routing has only seen the qids done so far, so a snapshot
    must not be mistaken for a finished extraction."""
    from src.engines.questionnaire.extractor import extract_survey_definition

    seen = []
    with patch(_EXTRACT, return_value=_response(_extraction_content())):
        extract_survey_definition(
            [_chunk(f"Q{i}") for i in range(1, 7)],
            study_id="s1",
            on_checkpoint=lambda d, t, p: seen.append(p.metadata.get("partial")),
            checkpoint_every=5,
        )

    assert seen[0] is True                       # the mid-run snapshot
    # And the FINAL survey is not marked partial.
    with patch(_EXTRACT, return_value=_response(_extraction_content())):
        final = extract_survey_definition([_chunk("Q1")], study_id="s1")
    assert final.metadata.get("partial") is False


def test_a_failing_checkpoint_never_kills_the_extraction():
    """Checkpointing exists to lose LESS work — it must not cost the whole run."""
    from src.engines.questionnaire.extractor import extract_survey_definition

    def broken(done, total, partial):
        raise RuntimeError("database is down")

    with patch(_EXTRACT, return_value=_response(_extraction_content())):
        survey = extract_survey_definition(
            [_chunk(f"Q{i}") for i in range(1, 4)],
            study_id="s1", on_checkpoint=broken, checkpoint_every=1,
        )

    assert len(survey.questions) == 3   # completed despite every save failing


def test_checkpoints_fire_on_a_cadence_not_every_question():
    """Persisting on every question would be a write per LLM call."""
    from src.engines.questionnaire.extractor import extract_survey_definition

    at = []
    with patch(_EXTRACT, return_value=_response(_extraction_content())):
        extract_survey_definition(
            [_chunk(f"Q{i}") for i in range(1, 12)],
            study_id="s1",
            on_checkpoint=lambda d, t, p: at.append(d),
            checkpoint_every=5,
        )

    # Every 5th, plus a final one so the last few are never lost.
    assert at == [5, 10, 11]


# --- qid uniqueness: a qid is an ADDRESS ------------------------------------


def test_duplicate_qids_are_made_unique():
    """Two questions sharing a qid makes the second unreachable.

    The review UI looks a question up by qid, so approving it clears flags on
    the FIRST match only — the second's blocking flag can never be cleared and
    the questionnaire can never be finalised. A reviewer hit exactly that:
    "81/81 approved" beside "cannot approve, S6 unresolved".
    """
    from src.engines.questionnaire.extractor import extract_survey_definition

    content = _extraction_content(qid="S6")
    with patch(_EXTRACT, return_value=_response(content)):
        survey = extract_survey_definition(
            [_chunk("S6"), _chunk("S6"), _chunk("S6")], study_id="s1"
        )

    qids = [q.qid for q in survey.questions]
    assert len(qids) == len(set(qids)), f"duplicates remain: {qids}"
    assert qids[0] == "S6"          # the first keeps the original address


def test_a_renamed_question_records_what_it_was_called():
    """The rename must be visible, not silent — a reviewer needs to know."""
    from src.engines.questionnaire.extractor import extract_survey_definition

    with patch(_EXTRACT, return_value=_response(_extraction_content(qid="S6"))):
        survey = extract_survey_definition(
            [_chunk("S6"), _chunk("S6")], study_id="s1"
        )

    renamed = survey.questions[1]
    assert renamed.qid != "S6"
    assert renamed.validation["original_qid"] == "S6"


def test_failed_chunks_do_not_all_collide_on_UNKNOWN():
    """Every failed extraction is named UNKNOWN — the commonest duplicate source."""
    from src.engines.questionnaire.extractor import extract_survey_definition

    with patch(_EXTRACT, side_effect=ModelOutputError("bad")):
        survey = extract_survey_definition(
            [_chunk(None), _chunk(None), _chunk(None)], study_id="s1"
        )

    qids = [q.qid for q in survey.questions]
    assert len(qids) == len(set(qids)), f"duplicates remain: {qids}"


def test_heartbeat_uses_the_same_clock_as_the_staleness_guard():
    """A heartbeat must never look stale the instant it is written.

    touch_questionnaire wrote UTC while _is_stale_processing compares against
    datetime.now(). On any machine not set to UTC the offset alone exceeded the
    15-minute window, so the first heartbeat of a healthy extraction marked it
    dead — reporting "the background worker was likely killed by a restart"
    while the worker was still running. Asserting the two clocks agree is what
    keeps them from drifting apart again.
    """
    from datetime import datetime
    from types import SimpleNamespace

    from api.routes.questionnaire import _is_stale_processing

    # Exactly what touch_questionnaire writes.
    just_beaten = SimpleNamespace(
        status="processing",
        updated_at=datetime.now().isoformat(),
        created_at=None,
    )
    assert _is_stale_processing(just_beaten) is False


def test_progress_fires_before_the_reconciliation_prepass():
    """Reconciliation must not run silently ahead of the first chunk.

    It is one whole-document LLM call that happens before any question is
    extracted — measured at 12 minutes on a real upload, against a 15-minute
    staleness watchdog. With no heartbeat the caller could not tell "slow but
    working" from "thread died", and healthy runs were reported as failures.
    """
    from src.engines.questionnaire.extractor import extract_survey_definition
    from src.intake.questionnaire_loader import DocumentBlock

    beats: list[tuple[int, int]] = []
    chunks = [_chunk("Q1")]
    blocks = [DocumentBlock("paragraph", "Q1. Age?", 0)]

    def fake_run(task, prompt, schema_arg, *args, **kwargs):
        if task == "questionnaire.verify":
            return _response({"faithful": True, "discrepancies": []})
        return _response(_extraction_content(qid="Q1"))

    # Both call sites. reconciler.py imports run_task at module level, so
    # patching only the extractor's copy leaves the reconciliation pre-pass
    # making a LIVE network call — which is exactly how this test first hung
    # the suite for twelve minutes.
    with (
        patch(_EXTRACT, side_effect=fake_run),
        patch(_RECONCILE, side_effect=fake_run),
    ):
        extract_survey_definition(
            chunks,
            study_id="s1",
            blocks=blocks,
            on_progress=lambda done, total: beats.append((done, total)),
        )

    # At least one beat before any chunk was finished.
    assert beats and beats[0][0] == 0, beats


# --- a backend outage must not throw away what the parsers read --------------


def test_parsed_options_survive_a_backend_outage(monkeypatch) -> None:
    """The model being down is not a reason to forget nine perfectly-read options.

    ED0 on the SHED instrument reads cleanly except for single-vs-multi, which
    no directive states. It used to come back as an empty `info` placeholder the
    moment a backend was unavailable — and with no options, nothing downstream
    could align its codes or check a condition written against it. One outage
    quietly disabled the checks on every question it touched.
    """
    from src.ai.router import BackendUnavailable
    from src.engines.questionnaire import extractor as ex
    from src.intake.questionnaire_loader import DocumentBlock, segment_into_questions

    def _down(*args, **kwargs):
        raise BackendUnavailable("all backends down")

    monkeypatch.setattr(ex, "run_task", _down)

    chunk = segment_into_questions([
        DocumentBlock("paragraph", "ED0: What is the highest level of school you completed?", 0),
        DocumentBlock("paragraph", "1. Less than High School degree", 1),
        DocumentBlock("paragraph", "2. High school degree or GED", 2),
        DocumentBlock("paragraph", "3. Some college but no degree", 3),
    ])[0]

    result = ex.extract_question(chunk)

    assert [o.code for o in result.question.options] == [1, 2, 3]
    assert result.question.text.startswith("What is the highest level")
    # Still flagged, and it names exactly what was left unread.
    assert result.flags
    assert result.question.validation["unresolved_fields"] == ["type"]


def test_a_chunk_with_nothing_readable_still_falls_back_to_a_placeholder(monkeypatch) -> None:
    from src.ai.router import BackendUnavailable
    from src.engines.questionnaire import extractor as ex
    from src.intake.questionnaire_loader import DocumentBlock, segment_into_questions

    monkeypatch.setattr(ex, "run_task", lambda *a, **k: (_ for _ in ()).throw(
        BackendUnavailable("all backends down")
    ))
    chunk = segment_into_questions([
        DocumentBlock("paragraph", "Q9. Tell us what you think about all of this", 0),
    ])[0]

    result = ex.extract_question(chunk)
    assert result.question.options == []
    assert result.flags


# --- a list, and a variable, that the spec table states outright -------------


def _spec(rows):
    """One spec table through the whole structured path."""
    from src.engines.questionnaire.extractor import _question_from_fields
    from src.engines.questionnaire.spec_table import parse_spec_table
    from src.intake.questionnaire_loader import DocumentBlock

    block = DocumentBlock("table", "", 0, rows=rows)
    fields = parse_spec_table(block)
    return _question_from_fields(
        fields,
        QuestionChunk(chunk_text="", candidate_qid=fields.qid,
                      source_blocks=[block], provenance="para 0"),
    )


def _row(label, value=None, width=3):
    return [label] + [value] * (width - 1) if value is not None else [label] * width


def test_a_response_list_taken_from_another_question_becomes_a_mask():
    """"Response list: [INSERT SELECTIONS FROM DA_GOALS]" IS a mask.

    The whole answer list is another question's selections. It was read and
    then dropped, so the question reached a reviewer with no answers and
    nothing said about why. Recording it as a mask is what lets the pass that
    fills lists borrow the items, the compiler narrow them to what this
    respondent chose, and the review UI show the link.
    """
    q = _spec([
        _row("TOP DIGITAL ADVERTISING GOAL"),
        _row("Q Name", "DA_TOP_GOAL"),
        _row("What is the top business objective you focus on?"),
        _row("", "Response list:"),
        _row("", "[INSERT SELECTIONS FROM DA_GOALS]"),
        ["Show / HV", "Show respondent directly", ""],
        ["Q type", "Std Single-Response (SR)", "If Custom, specify:"],
    ])
    masks = (q.validation or {}).get("techniques", {}).get("masks") or []
    assert [(m["source_qid"], m["mode"]) for m in masks] == [("DA_GOALS", "selected")]
    # Still a respondent-facing question, not a hidden variable.
    assert q.type is QuestionType.SINGLE
    assert schema.FLAG_DERIVED_VARIABLE not in q.flags


def test_a_table_that_says_hidden_variable_is_not_asked():
    """"Show / HV: Hidden variable" is the document stating it outright.

    HIDDEN_TOPBENEFIT holds a MaxDiff result computed by the programmer. Read
    as a single-choice question it had no answers and looked like a parser
    failure. A stated fact beats every wording heuristic — and the existing
    signals (a HID_ prefix, taught phrases, derived wording) all missed it.
    """
    q = _spec([
        _row("MAXDIFF TOP BENEFIT APPEAL"),
        _row("Q Name", "HIDDEN_TOPBENEFIT"),
        _row("Holds the most often selected Most Likely benefit"),
        _row("", "Variable list: CODE TO MATCH MAXDIFF_APPEAL"),
        ["Show / HV", "Hidden variable", "If not All, specify:"],
        ["Q type", "Std Single-Response (SR)", "If Custom, specify:"],
    ])
    assert q.type is QuestionType.INFO
    assert schema.FLAG_DERIVED_VARIABLE in q.flags
    # Cite the cell, so a reviewer is not asked to trust the reading.
    assert "Hidden variable" in q.extraction_evidence["derived"]


def test_show_respondent_directly_is_never_read_as_hidden():
    """The other value of that same cell. Getting this wrong hides a question."""
    q = _spec([
        _row("Q Name", "Q4"),
        _row("How satisfied were you?"),
        _row("1", "Very satisfied"),
        _row("2", "Not satisfied"),
        ["Show / HV", "Show respondent directly", ""],
        ["Q type", "Std Single-Response (SR)", ""],
    ])
    assert q.type is QuestionType.SINGLE
    assert schema.FLAG_DERIVED_VARIABLE not in q.flags


def test_a_routing_qid_typed_in_the_wrong_case_resolves():
    from src.core.survey_definition import Expression
    from src.engines.questionnaire.extractor import _match_qid_case

    expr = Expression(op="and", children=[Expression(op="in", qid="HID_B2B_B2c", values=[2])])
    _match_qid_case(expr, {"HID_B2B_B2C", "C_CUST_AGE"})
    assert expr.children[0].qid == "HID_B2B_B2C"


def test_a_renamed_duplicate_keeps_its_own_screen_out():
    from src.core.survey_definition import Expression, Question
    from src.engines.questionnaire.extractor import ExtractionResult, _deduplicate_qids

    def q(codes):
        return ExtractionResult(question=Question(
            qid="SS_ADVERT_DECMAKER", text="t", type=QuestionType.SINGLE,
            terminate_if=Expression(op="in", qid="SS_ADVERT_DECMAKER", values=codes),
        ), confidence={})

    first, second = q([5, 6]), q([1, 3, 98])
    _deduplicate_qids([first, second])
    assert first.question.terminate_if.qid == "SS_ADVERT_DECMAKER"
    assert second.question.qid == "SS_ADVERT_DECMAKER_2"
    assert second.question.terminate_if.qid == "SS_ADVERT_DECMAKER_2"


def test_spec_cells_state_a_cap_and_an_exclusive_answer():
    from src.core.survey_definition import Option, Question
    from src.engines.questionnaire.extractor import _limits_and_exclusives_from_cells
    from src.engines.questionnaire.spec_table import SpecTableFields

    q = Question(qid="DA_SUCCESS", text="Metrics?", type=QuestionType.MULTI,
                 options=[Option(code=1, label="ROI"), Option(code=99, label="Not sure")])
    fields = SpecTableFields(qid="DA_SUCCESS", notes="Make 99 exclusive",
                             evidence={"type": "Q type: Multi-select up to 3"})
    _limits_and_exclusives_from_cells(q, fields)
    assert q.validation["max_select"] == 3
    assert [o.exclusive for o in q.options] == [False, True]


def test_anchor_code_in_the_randomize_details_is_anchored():
    from src.core.survey_definition import Option, Question
    from src.engines.questionnaire.extractor import _limits_and_exclusives_from_cells
    from src.engines.questionnaire.spec_table import SpecTableFields

    q = Question(qid="C_OFFER", text="Offer?", type=QuestionType.SINGLE,
                 source_text="Randomize | Yes | If Yes, details: randomize 1 and 2, anchor 3",
                 options=[Option(code=c, label=f"L{c}") for c in (1, 2, 3)])
    _limits_and_exclusives_from_cells(q, SpecTableFields(qid="C_OFFER"))
    assert [o.anchored for o in q.options] == [False, False, True]

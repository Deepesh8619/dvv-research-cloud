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
    exc = BackendUnavailable("ANTHROPIC_API_KEY not set", config_error=True)
    with patch(_EXTRACT, side_effect=exc):
        result = extract_question(_chunk("Q9"))

    assert schema.FLAG_EXTRACTION_CONFIG_ERROR in result.flags
    assert schema.FLAG_EXTRACTION_UNAVAILABLE not in result.flags
    err = result.question.validation["extraction_error"]
    assert err["error_type"] == "BackendUnavailable"
    assert err["error_detail"] == "ANTHROPIC_API_KEY not set"


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
    exc = BackendUnavailable("ANTHROPIC_API_KEY not set", config_error=True)
    with patch(_EXTRACT, side_effect=exc):
        result = verify_question(_chunk(), result)

    assert schema.FLAG_VERIFICATION_UNAVAILABLE in result.flags
    err = result.question.validation["verification_error"]
    assert err["error_type"] == "BackendUnavailable"
    assert err["error_detail"] == "ANTHROPIC_API_KEY not set"


# --- T16: document-level circuit breaker -----------------------------------


def test_circuit_break_after_three_backend_failures():
    """First 3 chunks all fail with BackendUnavailable -> abort and raise, and
    run_task is NOT called for chunks 4+ (proves the breaker stops EARLY, not
    just that it eventually errors). Extract-only per doomed chunk: 3 calls."""
    chunks = [_chunk(f"Q{i}", f"Q{i}. Question {i}?") for i in range(1, 9)]  # 8 chunks

    with patch(
        _EXTRACT,
        side_effect=BackendUnavailable("ANTHROPIC_API_KEY not set", config_error=True),
    ) as mock_run:
        with pytest.raises(BackendUnavailable) as excinfo:
            extract_survey_definition(chunks, study_id="study-down")

    # Circuit breaker fired: exactly 3 extract attempts, verify never reached,
    # chunks 4-8 never touched.
    assert mock_run.call_count == 3
    # The raised error names the root cause (propagates to the route's error flip).
    assert "ANTHROPIC_API_KEY not set" in str(excinfo.value)
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
        raise BackendUnavailable("ANTHROPIC_API_KEY not set", config_error=True)

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
    assert all("PROGRAMMING-SPEC TABLE" in p for p in seen_prompts)


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


def test_extract_question_resolves_to_openai_with_only_openai_key(monkeypatch):
    """Smoke test the real routing path: run_task is NOT patched here. With only
    OPENAI_API_KEY set, `questionnaire.extract`'s "auto" chain must try local
    Ollama first (unreachable here), fall through to OpenAI, and drive
    extraction end-to-end. Only the network boundary (router.http_json) is
    stubbed, so this proves the chain wiring, backend dispatch, and result
    assembly all agree.

    Requires opting OUT of open-weights-only mode: that mode is the default and
    deliberately refuses the proprietary tail, so reaching OpenAI at all is now
    an explicit choice. The open-weights production path is covered by
    `test_extract_question_resolves_to_groq_under_open_weights_default`."""
    import urllib.error

    from src.ai import router as _router

    monkeypatch.setenv("LLM_OPEN_WEIGHTS_ONLY", "0")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai")

    content = _extraction_content(qid="Q1")

    def fake_http_json(request, timeout):
        if "11434" in request.full_url:  # the Ollama-first attempt
            raise urllib.error.URLError("connection refused")
        assert "api.openai.com" in request.full_url  # then falls through to OpenAI
        arguments = __import__("json").dumps(content)
        call = {"function": {"name": "emit", "arguments": arguments}}
        return {"choices": [{"message": {"tool_calls": [call]}}]}

    monkeypatch.setattr(_router, "http_json", fake_http_json)

    result = extract_question(_chunk("Q1"))

    assert result.question.qid == "Q1"
    assert result.question.type == QuestionType.SINGLE


def test_extract_question_resolves_to_groq_under_open_weights_default(monkeypatch):
    """The open-weights production path, end to end, with run_task NOT patched.

    Sibling of the OpenAI smoke test above, for what actually runs now: with
    open-weights-only mode at its default (ON) and a proprietary key present,
    the chain must fall through Ollama to GROQ and never touch OpenAI. Proves
    both the chain wiring and that the guarantee holds through the real
    dispatch path, not just in `_extraction_chain`.
    """
    import urllib.error

    from src.ai import router as _router

    monkeypatch.delenv("LLM_OPEN_WEIGHTS_ONLY", raising=False)  # default: ON
    monkeypatch.setenv("GROQ_API_KEY", "gk")
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    # Present but must be ignored — the whole point of the guarantee.
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
        "a confidential questionnaire reached OpenAI despite open-weights-only mode"
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

"""Deterministic spec-table extraction — no LLM.

The tests that matter most are the REFUSALS. A wrong deterministic answer is
silent and looks authoritative; a refusal just costs one LLM call. That
asymmetry is why every uncertain case must land in `missing` instead of
guessing.

Format-agnostic by design: the same table shape appears across clients with
different row labels, so these use several conventions, not one client's.
"""

from __future__ import annotations

from src.core.survey_definition import QuestionType
from src.engines.questionnaire.spec_table import FIELD_ALIASES, parse_spec_table
from src.intake.questionnaire_loader import DocumentBlock


def _table(rows):
    return DocumentBlock(block_type="table", text="", paragraph_index=0, rows=rows)


GBK = [
    ["Q Name", "SS_RESP"],
    ["Which of the following are you responsible for?"],
    ["", "Response list:"],
    ["1", "Digital advertising"],
    ["2", "Sales"],
    ["99", "None of these"],
    ["Among who", "", "If not All, specify:"],
    ["Q type", "Multi"],
    ["Other details", "TERMINATE if 99 selected"],
]


def test_a_labelled_table_parses_with_no_model():
    f = parse_spec_table(_table(GBK))
    assert f.qid == "SS_RESP"
    assert f.text.startswith("Which of the following")
    assert f.qtype is QuestionType.MULTI
    assert [o.code for o in f.options] == [1, 2, 99]
    assert f.options[0].label == "Digital advertising"
    assert f.complete is True  # nothing left for an LLM


def test_a_different_clients_labels_work_unchanged():
    """The point of the alias registry: new convention, no code change."""
    other_client = [
        ["Question ID", "Q7"],
        ["How satisfied are you overall?"],
        ["1", "Very satisfied"],
        ["2", "Not satisfied"],
        ["Punch type", "Single"],
        ["Base", "All respondents"],
        ["Programming notes", "Screen out if 2 selected"],
    ]
    f = parse_spec_table(_table(other_client))
    assert f.qid == "Q7"
    assert f.qtype is QuestionType.SINGLE
    assert f.terminate_if is not None  # "Screen out", not "TERMINATE"
    assert f.complete is True


def test_merged_cells_do_not_duplicate_values():
    """Word repeats a merged cell across every spanned column."""
    f = parse_spec_table(_table([
        ["Q Name", "Q1", "Q1", "Q1"],
        ["Pick one"],
        ["1", "Yes", "Yes", "Yes"],
        ["Q type", "Single"],
    ]))
    assert f.qid == "Q1"
    assert f.options[0].label == "Yes"


def test_template_prompts_are_not_mistaken_for_answers():
    """'If not All, specify:' is printed in the blank form, not an answer."""
    f = parse_spec_table(_table(GBK))
    assert f.base is None


# --- refusals: the safety-critical half -------------------------------------


def test_unstated_type_with_options_is_refused_not_guessed():
    """Single vs multi changes the data structure — that is a real question."""
    f = parse_spec_table(_table([
        ["Q Name", "Q1"], ["Pick"], ["1", "A"], ["2", "B"], ["Q type", ""],
    ]))
    assert f.qtype is None
    assert "type" in f.missing


def test_a_terminate_naming_unknown_codes_is_refused():
    """Mis-screening costs real money in field; silence is the right failure."""
    f = parse_spec_table(_table([
        ["Q Name", "Q1"], ["Pick"], ["1", "A"], ["2", "B"],
        ["Q type", "Single"],
        ["Other details", "TERMINATE if 7 selected"],   # no code 7 here
    ]))
    assert f.terminate_if is None
    assert "terminate" in f.missing   # stated but unparsed -> never dropped


def test_an_unparseable_terminate_is_still_reported():
    f = parse_spec_table(_table([
        ["Q Name", "Q1"], ["Pick"], ["1", "A"], ["Q type", "Single"],
        ["Other details", "Terminate anyone who seems disengaged"],
    ]))
    assert f.terminate_if is None
    assert "terminate" in f.missing


def test_prose_base_escalates_but_a_coded_one_does_not():
    prose = parse_spec_table(_table([
        ["Q Name", "Q1"], ["Pick"], ["1", "A"], ["Q type", "Single"],
        ["Among who", "those who are aware of the brand (see Q3)"],
    ]))
    assert "base" in prose.missing

    coded = parse_spec_table(_table([
        ["Q Name", "Q1"], ["Pick"], ["1", "A"], ["Q type", "Single"],
        ["Among who", "S1 = 1-3"],
    ]))
    assert "base" not in coded.missing


def test_not_selected_inverts_the_terminate():
    """Inversion, expressed in an op the rest of the system implements.

    This asserted `op == "not_in"` — an op absent from ExprOp, unhandled by
    evaluate(), and one the SurveyJS compiler raises on. The test passed; the
    feature could not work. `not` over `in` is the declared way to say it.
    """
    from typing import get_args

    from src.core.survey_definition import ExprOp, evaluate

    f = parse_spec_table(_table([
        ["Q Name", "Q1"], ["Pick"], ["1", "A"], ["2", "B"], ["Q type", "Multi"],
        ["Other details", "TERMINATE if 1 not selected"],
    ]))
    assert f.terminate_if.op in get_args(ExprOp)
    assert evaluate(f.terminate_if, {"Q1": [1]}) is False   # picked 1: continue
    assert evaluate(f.terminate_if, {"Q1": [2]}) is True    # did not: screen out


def test_a_table_without_an_id_is_not_a_question():
    assert parse_spec_table(_table([["Quota", "500"], ["Region", "North"]])) is None
    assert parse_spec_table(_table([])) is None


def test_unknown_labels_are_kept_not_dropped():
    """An untaught client convention must be visible, not silently lost."""
    f = parse_spec_table(_table([
        ["Q Name", "Q1"], ["Pick"], ["1", "A"], ["Q type", "Single"],
        ["Quota cell", "Metro males 18-34"],
    ]))
    assert f.extras.get("Quota cell") == "Metro males 18-34"


def test_alias_registry_has_no_duplicate_across_fields():
    """One label mapping to two fields would make parsing order-dependent."""
    seen: dict[str, str] = {}
    for field_name, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            assert alias not in seen, f"{alias!r} maps to both {seen.get(alias)} and {field_name}"
            seen[alias] = field_name


# --- wiring into extraction -------------------------------------------------


def test_a_complete_spec_table_costs_no_llm_call():
    """The headline: everything stated by the table needs no model at all."""
    from unittest.mock import patch

    from src.engines.questionnaire import extractor
    from src.intake.questionnaire_loader import QuestionChunk

    block = _table(GBK)
    chunk = QuestionChunk(chunk_text="x", candidate_qid="SS_RESP",
                          source_blocks=[block], provenance="p1")

    with patch.object(extractor, "run_task") as run:
        result = extractor.extract_question(chunk, "field_spec")

    run.assert_not_called()
    assert result.question.qid == "SS_RESP"
    assert result.question.type is QuestionType.MULTI
    assert len(result.question.options) == 3
    assert result.question.terminate_if is not None


def test_an_incomplete_table_asks_only_about_the_residue():
    """A missing Q Type must not re-send the whole ten-field schema."""
    from unittest.mock import patch

    from src.ai.router import AIResponse
    from src.engines.questionnaire import extractor
    from src.intake.questionnaire_loader import QuestionChunk

    rows = [r for r in GBK if r[0] != "Q type"]        # type not stated
    chunk = QuestionChunk(chunk_text="x", candidate_qid="SS_RESP",
                          source_blocks=[_table(rows)], provenance="p1")

    seen = {}

    def fake(task, prompt, schema_arg, *a, **k):
        seen["prompt"] = prompt
        seen["asked"] = schema_arg.get("required")
        return AIResponse(content={"type": "multi"}, backend="t", model="t")

    with patch.object(extractor, "run_task", side_effect=fake):
        result = extractor.extract_question(chunk, "field_spec")

    assert seen["asked"] == ["type"]                   # ONLY the missing field
    assert len(seen["prompt"]) < 1500                  # not the full prompt
    assert "already been read" in seen["prompt"]       # states what is known
    # Options came from the table, not the model.
    assert [o.code for o in result.question.options] == [1, 2, 99]
    assert result.question.type is QuestionType.MULTI


def test_a_failed_residue_call_falls_back_to_full_extraction():
    """Better to pay twice than to ship a half-read question."""
    from unittest.mock import patch

    from src.engines.questionnaire import extractor
    from src.intake.questionnaire_loader import QuestionChunk

    rows = [r for r in GBK if r[0] != "Q type"]
    chunk = QuestionChunk(chunk_text="Q. text", candidate_qid="SS_RESP",
                          source_blocks=[_table(rows)], provenance="p1")

    calls = []

    def fake(task, prompt, schema_arg, *a, **k):
        calls.append(schema_arg.get("required"))
        raise KeyError("no backend")

    with patch.object(extractor, "run_task", side_effect=fake):
        result = extractor.extract_question(chunk, "field_spec")

    assert len(calls) == 2                    # residue, then the full prompt
    assert calls[0] == ["type"]
    assert result.question.qid == "SS_RESP"   # still returns something usable


def test_a_non_table_chunk_is_untouched_by_this_path():
    """Prose questionnaires must keep working exactly as before."""
    from unittest.mock import patch

    from src.ai.router import AIResponse
    from src.engines.questionnaire import extractor
    from src.intake.questionnaire_loader import QuestionChunk

    chunk = QuestionChunk(chunk_text="Q5. How old are you?", candidate_qid="Q5",
                          source_blocks=[], provenance="p1")

    with patch.object(extractor, "run_task") as run:
        run.return_value = AIResponse(
            content={"qid": "Q5", "text": "How old are you?", "type": "numeric",
                     "options": [], "routing": {"verbatim_text": "", "ambiguous": False,
                                                "show_if": None, "terminate_if": None},
                     "confidence": {"text": 0.9, "type": 0.9, "options": 0.9,
                                    "routing": 0.9}},
            backend="t", model="t")
        extractor.extract_question(chunk, "prose")

    # The full ten-field schema, i.e. the original path.
    assert run.call_args[0][2] is not None
    assert "qid" in run.call_args[0][2].get("properties", {})


def test_a_terminate_references_its_own_question_not_a_placeholder():
    """A self-referencing terminate must name a REAL qid.

    A placeholder qid exists nowhere in the document, so the cross-chunk
    resolver in extract_survey_definition nulls the expression and flags
    UNRESOLVED_ROUTING_QID — which silently discarded every terminate this
    parser extracted. Only a full end-to-end extraction surfaced it.
    """
    f = parse_spec_table(_table(GBK))
    expr = f.terminate_if.to_dict()
    assert expr["qid"] == "SS_RESP"
    assert not expr["qid"].startswith("__")


def test_a_string_where_the_residue_expects_an_object_does_not_crash():
    """Same failure already fixed on the full-extraction path.

    A model can answer the residue with a bare string instead of the
    {verbatim_text, ambiguous, ...} object. That crashed with AttributeError
    mid-document. The string is kept as the verbatim — it is the sentence a
    reviewer needs to resolve the condition by hand — and flagged ambiguous.
    """
    from unittest.mock import patch

    from src.ai.router import AIResponse
    from src.engines.questionnaire import extractor
    from src.intake.questionnaire_loader import QuestionChunk

    rows = [r for r in GBK if r[0] not in ("Q type", "Among who")]
    rows.append(["Among who", "those aware of the brand (see Q3)"])
    chunk = QuestionChunk(chunk_text="x", candidate_qid="SS_RESP",
                          source_blocks=[_table(rows)], provenance="p1")

    with patch.object(extractor, "run_task") as run:
        run.return_value = AIResponse(
            content={"type": "multi", "base": "ask only aware respondents"},
            backend="t", model="t")
        result = extractor.extract_question(chunk, "field_spec")

    assert result.question.qid == "SS_RESP"
    assert result.question.type is QuestionType.MULTI
    # The sentence survived rather than being lost to a crash.
    assert "aware" in result.question.validation.get("routing_verbatim", "")

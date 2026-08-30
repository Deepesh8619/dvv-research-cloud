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
    f = parse_spec_table(_table([
        ["Q Name", "Q1"], ["Pick"], ["1", "A"], ["2", "B"], ["Q type", "Multi"],
        ["Other details", "TERMINATE if 1 not selected"],
    ]))
    assert f.terminate_if.to_dict()["op"] == "not_in"


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

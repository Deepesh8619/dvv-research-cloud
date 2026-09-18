"""Founder-taught patterns reaching the deterministic parsers.

The claim this file defends: a correction makes the system CHEAPER, not just
more accurate. A convention taught once is read by the parsers on every later
document, with no model call — and teaching nothing changes nothing.
"""

from __future__ import annotations

from src.core.survey_definition import QuestionType
from src.engines.questionnaire.learned import LearnedPattern, validate_pattern
from src.engines.questionnaire.prose_directives import parse_prose_question
from src.engines.questionnaire.spec_table import parse_spec_table
from src.intake.questionnaire_loader import DocumentBlock

# --- The no-op guarantee ----------------------------------------------------


def test_no_patterns_parses_exactly_as_before():
    """Teaching nothing must change nothing — the whole safety property."""
    text = "Q1. Which do you use?\nSelect all that apply.\n[PN: RANDOMIZE]\nAlpha\nBeta"
    without = parse_prose_question(text, "Q1")
    empty = parse_prose_question(text, "Q1", [])
    assert (without.qtype, without.randomize, without.evidence) == (
        empty.qtype,
        empty.randomize,
        empty.evidence,
    )


# --- type_signal ------------------------------------------------------------


def test_taught_type_signal_resolves_a_question_the_parser_could_not():
    """A client convention the built-in table cannot know."""
    text = "Q9. Which brand comes to mind?\n[SHOWCARD]\nAlpha\nBeta\nGamma"

    untaught = parse_prose_question(text, "Q9")
    assert untaught.qtype is None
    assert "type" in untaught.missing  # would have cost a model call

    taught = parse_prose_question(
        text, "Q9", [LearnedPattern("type_signal", "[SHOWCARD]", "single")]
    )
    assert taught.qtype is QuestionType.SINGLE
    assert taught.complete
    assert taught.evidence["type"] == "learned: [SHOWCARD]"


def test_taught_type_outranks_the_built_in_guess():
    """The founder knows their client; we only know our defaults.

    Here the built-in table would read "Select all that apply" as multi. A
    founder who has taught that this client's [GRID] directive means a rating
    grid is stating something more specific, so it wins.
    """
    text = "Q4. Rate each.\nSelect all that apply.\n[GRID]\nSpeed\nPrice"
    taught = parse_prose_question(
        text, "Q4", [LearnedPattern("type_signal", "[GRID]", "grid_single")]
    )
    assert taught.qtype is QuestionType.GRID_SINGLE


# --- option_tag -------------------------------------------------------------


def test_taught_option_tag_creates_a_real_screen_out():
    """Asserted through evaluate(): a screen-out that does not fire is not a
    screen-out, however correct the expression looks."""
    from src.core.survey_definition import evaluate

    text = "S1. Do you work in research?\nYes [SO]\nNo"
    taught = parse_prose_question(
        text, "S1", [LearnedPattern("option_tag", "[SO]", "terminate")]
    )
    assert taught.terminate_if is not None
    assert taught.terminate_if.values == [1]
    assert evaluate(taught.terminate_if, {"S1": [1]}) is True
    assert evaluate(taught.terminate_if, {"S1": [2]}) is False


def test_taught_option_tag_survives_the_bracket_a_founder_types():
    """A founder writes what the document shows — "[SO]" — but the parser
    hands the matcher the text INSIDE the brackets. Both must work."""
    text = "S1. Work in research?\nYes [SO]\nNo"
    with_brackets = parse_prose_question(
        text, "S1", [LearnedPattern("option_tag", "[SO]", "terminate")]
    )
    without = parse_prose_question(
        text, "S1", [LearnedPattern("option_tag", "SO", "terminate")]
    )
    assert with_brackets.terminate_if is not None
    assert without.terminate_if is not None


# --- randomize_signal -------------------------------------------------------


def test_taught_randomise_directive():
    text = "Q2. Pick one.\n[PN: SCRAMBLE]\nA\nB"
    assert not parse_prose_question(text, "Q2").randomize
    taught = parse_prose_question(
        text, "Q2", [LearnedPattern("randomize_signal", "[SCRAMBLE]")]
    )
    assert taught.randomize
    assert taught.evidence["randomize"] == "learned: [SCRAMBLE]"


# --- screen_out_range --------------------------------------------------------


def test_taught_screen_out_range_screens_the_range_it_names():
    """A taught phrase locates WHERE to look; the range after it is read by a
    fixed internal regex, never by anything the founder typed."""
    from src.core.survey_definition import evaluate

    text = (
        "S1. What is your age? [ENTER NUMBER 1-100]\n"
        "[PN: CONTINUE IF AGES 18-60. TERMINATE ALL OTHERS]"
    )
    taught = parse_prose_question(
        text, "S1", [LearnedPattern("screen_out_range", "CONTINUE IF AGES")]
    )
    rule = taught.terminate_if
    assert rule is not None and rule.op == "or"
    assert evaluate(rule, {"S1": 17}) is True
    assert evaluate(rule, {"S1": 61}) is True
    assert evaluate(rule, {"S1": 30}) is False
    assert "terminate" not in taught.missing


def test_the_common_continue_if_wording_works_even_untaught():
    """The one range-screening convention common enough to read by default —
    no teaching needed, unlike a client's own phrasing."""
    from src.core.survey_definition import evaluate

    text = (
        "S1. What is your age? [ENTER NUMBER 1-100]\n"
        "[PN: CONTINUE IF AGES 18-60. TERMINATE ALL OTHERS]"
    )
    untaught = parse_prose_question(text, "S1")
    rule = untaught.terminate_if
    assert rule is not None and rule.op == "or"
    assert evaluate(rule, {"S1": 17}) is True
    assert evaluate(rule, {"S1": 61}) is True
    assert evaluate(rule, {"S1": 30}) is False


def test_a_custom_range_phrase_needs_teaching_first():
    """A convention that is not the built-in wording is not guessed — it must
    be taught, same as every other pattern kind. Still uses "TERMINATE" so the
    directive is recognised as SOME screen-out and deferred, not silently lost;
    it just is not "CONTINUE IF", so the range inside it is never guessed."""
    text = (
        "S1. What is your age? [ENTER NUMBER 1-100]\n"
        "[PN: QUALIFY IF AGES 18-60. TERMINATE ALL OTHERS]"
    )
    untaught = parse_prose_question(text, "S1")
    assert untaught.terminate_if is None
    assert "terminate" in untaught.missing

    taught = parse_prose_question(
        text, "S1", [LearnedPattern("screen_out_range", "QUALIFY IF AGES")]
    )
    from src.core.survey_definition import evaluate

    rule = taught.terminate_if
    assert rule is not None
    assert "terminate" not in taught.missing
    assert evaluate(rule, {"S1": 17}) is True
    assert evaluate(rule, {"S1": 61}) is True
    assert evaluate(rule, {"S1": 30}) is False


# --- hidden_variable ----------------------------------------------------------


def test_taught_hidden_variable_marks_a_question_derived():
    text = "S2. Audience Code"
    untaught = parse_prose_question(text, "S2")
    assert untaught.derived is False

    taught = parse_prose_question(
        text, "S2", [LearnedPattern("hidden_variable", "Audience Code")]
    )
    assert taught.derived is True
    assert taught.qtype is QuestionType.INFO


# --- field_alias (spec tables) ----------------------------------------------


def _spec(rows):
    return DocumentBlock("table", "", 0, rows=rows)


def test_taught_row_label_fills_the_field_instead_of_landing_in_extras():
    block = _spec([
        ["Q Name", "Q7"],
        ["Question text", "How satisfied are you?"],
        ["Q type", "single"],
        # A label no built-in alias covers — the client's own wording.
        ["Screener population", "Those aware at Q5"],
    ])

    untaught = parse_spec_table(block)
    assert untaught.base is None
    assert "Screener population" in untaught.extras  # read, but not understood

    taught = parse_spec_table(
        block, [LearnedPattern("field_alias", "Screener population", "base")]
    )
    assert taught.base == "Those aware at Q5"
    assert "Screener population" not in taught.extras


def test_taught_type_signal_also_applies_to_spec_tables():
    block = _spec([
        ["Q Name", "Q8"],
        ["Question text", "Rank these."],
        ["Q type", "SHOWCARD RANKING"],
        ["1", "Alpha"],
        ["2", "Beta"],
    ])
    taught = parse_spec_table(
        block, [LearnedPattern("type_signal", "SHOWCARD RANKING", "rank")]
    )
    assert taught.qtype is QuestionType.RANK


# --- Validation is a trust boundary -----------------------------------------


def test_an_empty_pattern_is_refused():
    """An empty needle is a substring of every string, so it would silently
    retype every question in the document."""
    assert validate_pattern("type_signal", "", "single") is not None
    assert validate_pattern("type_signal", "   ", "single") is not None


def test_a_single_character_pattern_is_refused():
    assert validate_pattern("type_signal", "[", "single") is not None


def test_an_unknown_meaning_is_refused():
    assert validate_pattern("type_signal", "[SHOWCARD]", "banana") is not None
    assert validate_pattern("field_alias", "Asked of", "not_a_field") is not None
    assert validate_pattern("option_tag", "[SO]", "explode") is not None


def test_a_valid_pattern_passes():
    assert validate_pattern("type_signal", "[SHOWCARD]", "single") is None
    assert validate_pattern("randomize_signal", "[SCRAMBLE]", "") is None


def test_a_stored_pattern_with_a_dead_type_does_not_crash_extraction():
    """An enum rename must degrade to "unrecognised", never to an exception."""
    text = "Q1. Pick one.\n[LEGACY]\nA\nB"
    fields = parse_prose_question(
        text, "Q1", [LearnedPattern("type_signal", "[LEGACY]", "type_that_no_longer_exists")]
    )
    assert fields is not None
    assert fields.qtype is None


# --- Randomisation must reach the field fielding reads ----------------------


def test_detected_randomisation_lands_on_the_question_not_just_validation():
    """Recording it in `validation` alone meant it was never applied.

    The parser correctly read "[PN: RANDOMIZE]" and the review UI showed it as
    handled, but `Question.randomize_options` — the field fielding consults —
    stayed False, so the question was served in fixed order. Detecting a thing
    and then not applying it is worse than not detecting it.
    """
    from src.engines.questionnaire.extractor import _question_from_prose
    from src.intake.questionnaire_loader import QuestionChunk

    fields = parse_prose_question(
        "Q1. Pick one.\n[PN: RANDOMIZE]\nAlpha\nBeta", "Q1"
    )
    chunk = QuestionChunk(
        chunk_text="", candidate_qid="Q1", source_blocks=[], provenance="p1"
    )
    assert _question_from_prose(fields, chunk).randomize_options is True


def test_a_taught_randomise_directive_also_reaches_the_question():
    from src.engines.questionnaire.extractor import _question_from_prose
    from src.intake.questionnaire_loader import QuestionChunk

    fields = parse_prose_question(
        "Q1. Pick one.\n[SCRAMBLE]\nAlpha\nBeta",
        "Q1",
        [LearnedPattern("randomize_signal", "[SCRAMBLE]")],
    )
    chunk = QuestionChunk(
        chunk_text="", candidate_qid="Q1", source_blocks=[], provenance="p1"
    )
    assert _question_from_prose(fields, chunk).randomize_options is True


def test_spec_table_randomise_cell_is_read_conservatively():
    """Only an affirmative cell turns it on — randomising unasked is a data
    error, so an unreadable value stays off and stays visible for review."""
    from src.engines.questionnaire.extractor import _randomize_from_cell

    assert _randomize_from_cell("Yes") is True
    assert _randomize_from_cell("Randomize response list") is True
    assert _randomize_from_cell("Rotate rows") is True
    assert _randomize_from_cell("No") is False
    assert _randomize_from_cell("(blank)") is False
    assert _randomize_from_cell("") is False
    assert _randomize_from_cell(None) is False
    assert _randomize_from_cell("ask the scripter") is False


# --- Screen-outs must actually screen people out ----------------------------
# These assert on evaluate(), not on the shape of the expression. The bug they
# pin produced a perfectly reasonable-LOOKING expression that matched nobody:
# codes went into `value` while `in` is defined as membership in `values`, so
# evaluate() read `expression.values or []` and every terminate was inert — in
# the evaluator, in the compiled SurveyJS, and as an empty codes box in the UI.


def test_a_prose_terminate_actually_fires():
    from src.core.survey_definition import evaluate

    fields = parse_prose_question(
        "S2. Do you work in any of these?\n"
        "Select all that apply.\n"
        "Advertising [TERMINATE]\n"
        "Healthcare",
        "S2",
    )
    expression = fields.terminate_if
    assert expression.values == [1], "codes must live in `values` for an `in` op"
    assert evaluate(expression, {"S2": [1]}) is True
    assert evaluate(expression, {"S2": [2]}) is False


def test_a_spec_table_terminate_actually_fires():
    from src.core.survey_definition import evaluate

    block = _spec([
        ["Q Name", "Q1"],
        ["Question text", "Which apply?"],
        ["Q type", "multi"],
        ["1", "Agency"],
        ["3", "Consultant"],
        ["98", "None of the above"],
        ["Other details", "TERMINATE if 1,3,98 selected"],
    ])
    expression = parse_spec_table(block).terminate_if
    assert expression.values == [1, 3, 98]
    assert evaluate(expression, {"Q1": [3]}) is True
    assert evaluate(expression, {"Q1": [2]}) is False


def test_a_negated_terminate_uses_a_declared_op_and_inverts():
    """"TERMINATE if 1 not selected" produced op `not_in`, which is not in
    ExprOp — the evaluator, the SurveyJS compiler and the review UI all failed
    to understand it. `not` over `in` says the same thing in the real language."""
    from typing import get_args

    from src.core.survey_definition import ExprOp, evaluate

    block = _spec([
        ["Q Name", "Q1"],
        ["Question text", "Which apply?"],
        ["Q type", "multi"],
        ["1", "Agency"],
        ["2", "Other"],
        ["Other details", "TERMINATE if 1 not selected"],
    ])
    expression = parse_spec_table(block).terminate_if
    assert expression.op in get_args(ExprOp)
    assert evaluate(expression, {"Q1": [1]}) is False  # picked it: continue
    assert evaluate(expression, {"Q1": [2]}) is True  # did not: screen out


def test_every_terminate_op_the_parsers_emit_is_declared():
    """A guard against reintroducing an op nothing downstream implements."""
    from typing import get_args

    from src.core.survey_definition import ExprOp

    declared = set(get_args(ExprOp))
    samples = [
        "TERMINATE if 1,2 selected",
        "TERMINATE if 1 not selected",
        "Terminate if codes 1,2",
    ]
    for note in samples:
        block = _spec([
            ["Q Name", "Q1"],
            ["Question text", "W?"],
            ["Q type", "multi"],
            ["1", "A"],
            ["2", "B"],
            ["Other details", note],
        ])
        expression = parse_spec_table(block).terminate_if
        assert expression is not None, note
        assert expression.op in declared, f"{note} produced undeclared op {expression.op}"

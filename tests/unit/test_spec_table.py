"""Deterministic spec-table extraction — no LLM.

The tests that matter most are the REFUSALS. A wrong deterministic answer is
silent and looks authoritative; a refusal just costs one LLM call. That
asymmetry is why every uncertain case must land in `missing` instead of
guessing.

Format-agnostic by design: the same table shape appears across clients with
different row labels, so these use several conventions, not one client's.
"""

from __future__ import annotations

from src.core.survey_definition import QuestionType, evaluate
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


def test_a_screen_out_written_as_a_code_range():
    """"TERMINATE if 3-99 selected" is how a scripter writes "everything from 3
    up". It parsed as nothing, so the screen-out never fired — and a missing
    terminate looks exactly like a question that screens nobody out, which is
    why it survived into a live study."""
    from src.core.survey_definition import Option, evaluate
    from src.engines.questionnaire.spec_table import _parse_terminate

    options = [Option(code=i, label=f"band {i}") for i in range(1, 10)]
    condition = _parse_terminate("TERMINATE if 3-99 selected", options, "SS_EMP")
    assert condition is not None
    # Only codes the question actually offers — never the whole span.
    assert condition.values == [3, 4, 5, 6, 7, 8, 9]
    assert evaluate(condition, {"SS_EMP": 5}) is True
    assert evaluate(condition, {"SS_EMP": 2}) is False


def test_a_screen_out_on_another_questions_answer():
    """"TERMINATE if 13-99 selected at SS_REVENUE" screens on SS_REVENUE, not on
    the question whose notes it is written in."""
    from src.core.survey_definition import Option
    from src.engines.questionnaire.spec_table import _parse_terminate

    options = [Option(code=i, label=f"rev {i}") for i in range(1, 20)]
    condition = _parse_terminate(
        "TERMINATE if 13-99 selected at SS_REVENUE", options, "SS_AD_SPEND"
    )
    assert condition is not None
    assert condition.qid == "SS_REVENUE"


def test_an_unfinished_screen_out_is_still_refused():
    """"TERMINATE if age<25 or age>??" has a placeholder where a number belongs.
    Reading half of it would screen out fewer people than the document asks
    for — quieter than refusing, and worse."""
    from src.engines.questionnaire.spec_table import _parse_terminate

    assert _parse_terminate("TERMINATE if age<25 or age>??", [], "RESP_AGE") is None


def test_a_cross_question_range_is_read_against_the_right_question():
    """"TERMINATE if 13-99 selected at SS_REVENUE" is written in the notes of a
    LATER question. Narrowing the range against the options in hand — the wrong
    question's — turned an 87-code screen-out into a single code, a rule that
    fires for almost nobody. The quietest way to get a screen-out wrong."""
    from src.core.survey_definition import Option, evaluate
    from src.engines.questionnaire.spec_table import _parse_terminate

    spend_options = [Option(code=i, label=f"spend {i}") for i in (1, 2, 3, 99)]
    condition = _parse_terminate(
        "TERMINATE if 13-99 selected at SS_REVENUE", spend_options, "SS_AD_SPEND"
    )
    assert condition.qid == "SS_REVENUE"
    assert (min(condition.values), max(condition.values)) == (13, 99)
    assert evaluate(condition, {"SS_REVENUE": 13}) is True
    assert evaluate(condition, {"SS_REVENUE": 12}) is False


def _grid_table():
    from src.intake.questionnaire_loader import DocumentBlock

    return DocumentBlock(
        block_type="table",
        text="",
        paragraph_index=1,
        rows=[
            ["PLATFORM ATTITUDES", "PLATFORM ATTITUDES", "PLATFORM ATTITUDES"],
            ["Q Name", "DA_ATTITUDES", "DA_ATTITUDES"],
            ["Indicate your agreement", "Indicate your agreement", "Indicate your agreement"],
            ["", "Variable list:", "Variable list:"],
            ["1", "Is a leader in AI tools", "Is a leader in AI tools"],
            ["2", "Has an easy-to-navigate interface", "Has an easy-to-navigate interface"],
            ["", "Response list:", "Response list:"],
            ["5", "Strongly Agree", "Strongly Agree"],
            ["4", "", ""],
            ["3", "", ""],
            ["2", "", ""],
            ["1", "Strongly Disagree", "Strongly Disagree"],
        ],
    )


def test_a_grids_two_lists_are_not_one_list():
    """A GBK table states a grid as "Variable list" (what is rated) and
    "Response list" (the scale). Read into one bucket, an 8-statement grid on a
    10-point scale arrived as 18 answers with the codes colliding — which is
    what "duplicate option code" and "empty options" were reporting."""
    from src.core.survey_definition import QuestionType
    from src.engines.questionnaire.spec_table import parse_spec_table

    fields = parse_spec_table(_grid_table())
    assert fields is not None
    assert fields.qtype is QuestionType.GRID_SINGLE
    assert [o.label for o in fields.rows] == [
        "Is a leader in AI tools",
        "Has an easy-to-navigate interface",
    ]
    assert len(fields.cols) == 5
    assert fields.options == []  # not both at once
    codes = [o.code for o in fields.rows] + [o.code for o in fields.cols]
    assert len(fields.rows) + len(fields.cols) == len(codes)


def test_an_unlabelled_scale_point_is_still_a_point():
    """A ten-point scale is labelled at its ends and blank between. Dropping the
    blanks turned it into a two-point scale, and a respondent who picked 7 had
    nowhere to land."""
    from src.engines.questionnaire.spec_table import parse_spec_table

    cols = parse_spec_table(_grid_table()).cols
    assert [o.code for o in cols] == [5, 4, 3, 2, 1]
    assert cols[0].label == "Strongly Agree"
    assert cols[1].label == "4"  # the number is what the respondent sees
    assert cols[-1].label == "Strongly Disagree"


def test_a_question_with_one_list_is_unchanged():
    """Only a response list means an ordinary answer list, exactly as before —
    the grid rule must not turn every question into a grid."""
    from src.engines.questionnaire.spec_table import parse_spec_table
    from src.intake.questionnaire_loader import DocumentBlock

    block = DocumentBlock(
        block_type="table",
        text="",
        paragraph_index=1,
        rows=[
            ["AGE", "AGE"],
            ["Q Name", "RESP_AGE"],
            ["What is your age?", "What is your age?"],
            ["", "Response list:"],
            ["1", "Under 25"],
            ["2", "25 or over"],
        ],
    )
    fields = parse_spec_table(block)
    assert [o.label for o in fields.options] == ["Under 25", "25 or over"]
    assert fields.rows == [] and fields.cols == []


# The RESP_AGE table exactly as the GBK document writes it.
AGE = [
    ["AGE"],
    ["Q Name", "RESP_AGE"],
    ["What is your age?"],
    ["", "Response list:"],
    ["", "[Numeric OE: 3-digit max]"],
    ["99", "Prefer not to answer"],
    ["Among who", "(blank)"],
    ["Show / HV", "(blank)"],
    ["Q type", "(blank)"],
    ["Randomize", "(blank)"],
    ["Other details", "Allow 0-999 to be entered\nTERMINATE if age<25 or age>?? OR if 99 selected"],
]


def test_a_number_box_with_a_way_out_is_a_number_box():
    """One coded line beside "[Numeric OE]" was read as the whole answer list:
    the question became a single choice whose only option was "Prefer not to
    answer", and nobody could type an age."""
    fields = parse_spec_table(_table(AGE))
    assert fields.qtype is QuestionType.NUMERIC
    assert fields.numeric_range == {"min": 0, "max": 999}
    # 99 is kept — as a way out of typing, not as an answer list.
    [opt_out] = fields.options
    assert (opt_out.code, opt_out.label, opt_out.exclusive) == (99, "Prefer not to answer", True)
    # And the reviewer is told 99 is also a typeable age.
    assert "cannot be told apart" in fields.evidence["opt_out"]


def test_the_stated_part_of_a_numeric_screen_out_is_applied_and_the_gap_is_flagged():
    fields = parse_spec_table(_table(AGE))
    rule = fields.terminate_if
    assert rule is not None and rule.op == "or"
    assert evaluate(rule, {"RESP_AGE": 24}) is True  # under 25
    assert evaluate(rule, {"RESP_AGE": 99}) is True  # declined
    assert evaluate(rule, {"RESP_AGE": 40}) is False
    # "age>??" — a limit nobody wrote. Flagged for a person, never guessed,
    # and not handed to a model that would overwrite the exact part.
    assert "terminate_gap" in fields.evidence
    assert "terminate" not in fields.missing


def test_a_multi_line_text_box_is_an_open_end_not_a_multi_select():
    """"Multi-line" is the size of the box, not the number of answers.

    The Google Ads questionnaire writes "Q type: Multi-line (large) Text OE"
    for its verbatims. Matching "multi" as a substring read the box size as
    the response type, so eight open ends arrived as multi-selects with no
    options — a question a respondent cannot answer, and a data column that
    would never be filled.
    """
    f = parse_spec_table(_table([
        ["Q Name", "MAXDIFF_OE"],
        ["Why would this make you shift digital ad spend?"],
        ["Q type", "Multi-line (large) Text OE"],
    ]))
    assert f.qid == "MAXDIFF_OE"
    assert f.qtype is QuestionType.OPEN_TEXT


def test_a_single_line_text_box_is_an_open_end_too():
    f = parse_spec_table(_table([
        ["Q Name", "Q9"],
        ["What is your job title?"],
        ["Q type", "Single-line Text OE"],
    ]))
    assert f.qtype is QuestionType.OPEN_TEXT


def test_a_real_multi_response_type_is_untouched():
    """The guard must not cost us the types it was protecting.

    A response type is never written "multi-line", so removing that qualifier
    cannot reach "Std Multi-Response (MR)".
    """
    for stated, expected in [
        ("Std Multi-Response (MR)", QuestionType.MULTI),
        ("Multi-Select", QuestionType.MULTI),
        ("Multi punch", QuestionType.MULTI),
        ("Std Single-Response (SR)", QuestionType.SINGLE),
        ("Single punch", QuestionType.SINGLE),
        ("Numeric OE", QuestionType.NUMERIC),
    ]:
        f = parse_spec_table(_table([
            ["Q Name", "Q1"],
            ["Pick one"],
            ["1", "Yes"],
            ["2", "No"],
            ["Q type", stated],
        ]))
        assert f.qtype is expected, f"{stated} read as {f.qtype}"


def _merged(label, value=None, width=4):
    """One spec-table row as Word serialises it: merged cells repeated."""
    return [label] + [value] * (width - 1) if value is not None else [label] * width


DA_GOAL_PLATFORM = [
    _merged("BEST PLATFORM FOR TOP GOALS"),
    _merged("Q Name", "DA_GOAL_PLATFORM"),
    _merged("Which platform is best for achieving this goal?"),
    _merged("", "Variable list:"),
    _merged("1", "Google Ads\n[IF GOOGLE ADS CURRENT USER]"),
    _merged("2", "Meta Ads\n[IF META ADS CURRENT USER]"),
    _merged("", "[INSERT CURRENTLY USED BRANDS FROM DA_PLATFORM_USAGE 9-15]"),
    _merged("99", "None of these"),
    _merged("", "Response list:"),
    _merged("", "[INSERT GOALS SELECTED AT DA_GOALS]"),
    ["Among who", "All respondents", "", ""],
    ["Q type", "Carousel VERT Grid (SR)", "If Custom, specify:", "If Custom, specify:"],
]


def test_a_variable_list_survives_a_response_list_that_is_a_directive():
    """The rows were being thrown away, and the loss reported as complete.

    "Response list: [INSERT GOALS SELECTED AT DA_GOALS]" states the columns as
    a directive rather than as codes, so `row_items and options` was false and
    the assembly fell back to no rows AND no columns. Three rows the document
    states plainly were discarded, and nothing was recorded as missing — so a
    grid with nothing in it reached the reviewer looking like a question the
    parser had failed on.
    """
    f = parse_spec_table(_table(DA_GOAL_PLATFORM))
    assert f.qid == "DA_GOAL_PLATFORM"
    assert f.qtype is QuestionType.GRID_SINGLE
    assert [o.code for o in f.rows] == [1, 2, 99]
    # Honest about what is still unknown, rather than silently complete.
    assert "cols" in f.missing
    assert f.complete is False


def test_a_directive_under_an_answer_in_the_same_cell_is_read():
    """"Google Ads⏎[IF GOOGLE ADS CURRENT USER]" is an answer and a rule.

    Word keeps both lines in one cell. Joined into the label, the bracket
    became part of what the respondent reads and the base it states was never
    applied.
    """
    f = parse_spec_table(_table(DA_GOAL_PLATFORM))
    assert f.rows[0].label == "Google Ads"
    assert f.rows[1].label == "Meta Ads"
    codes = {b["code"]: b["verbatim"] for b in f.option_bases}
    assert "GOOGLE ADS CURRENT USER" in codes[1]
    assert "META ADS CURRENT USER" in codes[2]
    # It names no question, so it is recorded for a person — never guessed at.
    assert all(b["resolved"] is False for b in f.option_bases)


def test_a_directive_with_no_code_beside_it_is_still_read():
    """"|  | [INSERT ... FROM DA_PLATFORM_USAGE 9-15] |" has an empty code cell.

    Every blank-code row was skipped, so a list saying where the rest of its
    items come from said nothing at all.
    """
    f = parse_spec_table(_table(DA_GOAL_PLATFORM))
    sources = {(d["source_qid"], tuple(d["codes"] or ())) for d in f.list_from}
    assert ("DA_PLATFORM_USAGE", (9, 15)) in sources
    assert ("DA_GOALS", ()) in sources


def test_a_text_only_screen_is_display_text_not_a_question():
    """"Q type: Text Only" is a transition screen the respondent READS.

    Matching the bare word "text" typed it as an open end, so somebody who was
    shown "In this next section, we'll focus on two digital advertising
    platforms" was also shown a box to type into — and the data file carried a
    column for an answer nobody was asked for.
    """
    f = parse_spec_table(_table([
        ["Q Name", "TRANSITION_GA"],
        ["In this next section, we'll focus on two platforms: Meta Ads and Google Ads."],
        ["Show / HV", "Show respondent directly"],
        ["Q type", "Text Only"],
    ]))
    assert f.qid == "TRANSITION_GA"
    assert f.qtype is QuestionType.INFO


def test_a_real_open_end_is_still_an_open_end():
    """The guard must not cost us the verbatims it sits next to."""
    for stated in ("Multi-line (large) Text OE", "Single-line Text OE", "Open end"):
        f = parse_spec_table(_table([
            ["Q Name", "Q9"],
            ["Why do you say that?"],
            ["Q type", stated],
        ]))
        assert f.qtype is QuestionType.OPEN_TEXT, f"{stated} read as {f.qtype}"


def _with_base(*base_row, notes=""):
    rows = [r for r in GBK if r[0] not in ("Among who", "Other details")]
    return _table(rows + [["Among who", *base_row], ["Other details", notes]])


def test_a_base_behind_other_arrows_is_read_from_the_next_cell():
    # "Other >>>" means "see the next cell". Keeping the arrow lost every base
    # in the Google Ads document, and every question showed to everyone.
    f = parse_spec_table(_with_base("Other >>>", "If not All, specify: C_ACCOUNT = 2"))
    assert f.base == "C_ACCOUNT = 2"
    assert (f.show_if.qid, f.show_if.values) == ("C_ACCOUNT", [2])
    assert "base" not in f.missing


def test_a_labelled_base_with_one_coded_condition_is_applied():
    f = parse_spec_table(_with_base(
        "Other >>>",
        "If not All, specify: Google Ads Lapsed or Aware Non-User (HID_GOOGLE_USER = 2 or 3)",
    ))
    assert (f.show_if.qid, f.show_if.values) == ("HID_GOOGLE_USER", [2, 3])
    f = parse_spec_table(_with_base("Other >>>", "If not All, specify: DA_PLACEMENT = 3 and/or 4"))
    assert f.show_if.values == [3, 4]


def test_a_compound_or_prose_base_is_left_for_review():
    for base in ("DA_PLACEMENT = 3 and/or 4 AND 1-98 selected at DA_VID_EFFIC",
                 "Provider aware based on HID_AWARE",
                 "Ask if GA_GOOGLE_TOOL = 5 for at least one option"):
        f = parse_spec_table(_with_base("Other >>>", f"If not All, specify: {base}"))
        assert f.show_if is None and "base" in f.missing, base


def test_all_respondents_with_a_written_base_uses_the_written_base():
    f = parse_spec_table(
        _with_base("All respondents", "If not All, specify: HID_META_USER = 2 or 3")
    )
    assert f.show_if.values == [2, 3]
    assert "base_clash" in f.evidence
    f = parse_spec_table(_with_base("All respondents", "If not All, specify:"))
    assert f.show_if is None and "base" not in f.missing


def test_terminate_if_selects_punch_list_with_or():
    rows = [r for r in GBK if r[0] != "Other details"]
    rows.insert(4, ["10", "More than 1000"])
    notes = ["Other details", "TERMINATE if selects punch 1, 10, or 99"]
    f = parse_spec_table(_table(rows + [notes]))
    assert f.terminate_if.values == [1, 10, 99]


def test_auto_punch_in_the_base_cell_is_an_automatic_answer_not_a_base():
    f = parse_spec_table(_with_base(
        "Other >>>", "If not All, specify: auto=punch those who select only one at DA_GOALS"))
    assert f.show_if is None and "base" not in f.missing
    assert "auto_answer" in f.evidence


def test_each_screen_out_sentence_on_a_number_is_read_on_its_own():
    from src.core.survey_definition import Option
    from src.engines.questionnaire.spec_table import _parse_numeric_terminate

    notes = ("Allow 0-99999999. TERMINATE if less than $500 Flag if greater than $1,000,000 "
             "TERMINATE if selects 'Prefer not to answer' TERMINATE if 13-99 selected at "
             "SS_REVENUE (term only after they have answered SS_AD_SPEND).")
    expr, unread = _parse_numeric_terminate(
        notes, "SS_AD_SPEND", [Option(code=99, label="Prefer not to answer")])
    got = {(c.op, c.value, tuple(c.values or ())) for c in expr.children}
    assert got == {("lt", 500, ()), ("in", None, (99,))}  # the flag at $1M is not a screen-out
    assert unread  # the SS_REVENUE rule is left for a person

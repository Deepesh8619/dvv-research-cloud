"""Prose-questionnaire directive parsing — no LLM.

Two of the three real client documents are prose, and an earlier pass wrongly
concluded they "cannot be parsed deterministically". That conclusion came from
searching for the wrong strings ("please select one/all", numbered options),
not from the documents. These tests use the conventions the documents actually
carry.

The refusals matter most: a wrong screen-out removes real respondents from a
study, and costs money in field.
"""

from __future__ import annotations

from src.core.survey_definition import QuestionType
from src.engines.questionnaire.prose_directives import parse_prose_question

REAL = """S2.\tDo you, or does anyone in your household, currently work in any of these?
Select all that apply.
[PN: RANDOMIZE]
Advertising, marketing, or market research [TERMINATE]
Beauty or personal-care manufacturing [TERMINATE]
Healthcare
Education
None of these [ANCHOR, EXCLUSIVE]"""


def test_a_real_prose_question_parses_completely():
    f = parse_prose_question(REAL, "S2")
    assert f.qid == "S2"
    assert f.qtype is QuestionType.MULTI          # "Select all that apply"
    assert f.randomize is True                    # [PN: RANDOMIZE]
    assert [o.code for o in f.options] == [1, 2, 3, 4, 5]
    assert f.options[0].label.startswith("Advertising")
    assert f.complete is True


def test_per_option_terminate_tags_become_one_condition():
    """The field the UI was showing as missing.

    Asserts through evaluate() as well as shape. An earlier version of this
    test checked `expr["value"] == [1, 2]` and passed while the screen-out
    matched nobody: `in` is defined as membership in `values`, so evaluate()
    and the SurveyJS compiler both read an empty list. A test that pins the
    shape without exercising the behaviour can hold a bug in place.
    """
    from src.core.survey_definition import evaluate

    f = parse_prose_question(REAL, "S2")
    expr = f.terminate_if.to_dict()
    assert expr["op"] == "in"
    assert expr["values"] == [1, 2]               # exactly the tagged options
    assert expr["qid"] == "S2"                    # a real qid, not a placeholder
    assert evaluate(f.terminate_if, {"S2": [2]}) is True
    assert evaluate(f.terminate_if, {"S2": [3]}) is False


def test_anchor_and_exclusive_tags_land_on_the_option():
    f = parse_prose_question(REAL, "S2")
    last = f.options[-1]
    assert last.exclusive is True
    assert last.anchored is True
    # And the tag text is not left in the label a respondent would read.
    assert "[" not in last.label


def test_the_question_label_is_not_part_of_the_wording():
    f = parse_prose_question(REAL, "S2")
    assert not f.text.startswith("S2")
    assert f.text.startswith("Do you")


def test_select_one_is_single():
    f = parse_prose_question(
        "S5. Which best describes you?\nSelect one.\nI buy for myself\nSomeone else buys",
        "S5",
    )
    assert f.qtype is QuestionType.SINGLE


def test_section_routing_is_not_mistaken_for_an_option():
    """'Adult Path Ages 18-25 (IF S0=1) ASK S3-S6' belongs to the next section."""
    f = parse_prose_question(
        REAL + "\nAdult Consumer Path Ages 18-25 (IF S0=1 AND S1=18-25) ASK S3-S6",
        "S2",
    )
    assert all("ASK S3" not in o.label for o in f.options)
    assert len(f.options) == 5


def test_an_instruction_line_is_not_an_option():
    f = parse_prose_question(
        "S9. How old are you?\nPlease enter a number.\n", "S9"
    )
    assert f.options == []
    assert f.qtype is QuestionType.NUMERIC


# --- refusals ---------------------------------------------------------------


def test_options_without_a_type_instruction_escalate():
    """Single vs multi changes the data structure — never guessed."""
    f = parse_prose_question("S3. Pick something\nRed\nBlue\nGreen", "S3")
    assert f.qtype is None
    assert "type" in f.missing


def test_a_prose_terminate_with_no_option_codes_reads_the_range_untaught():
    """'CONTINUE IF AGES 18-60. TERMINATE ALL OTHERS' is about a RANGE.

    A per-option code would screen out the wrong people, so this is never
    invented as one — but the wording is common enough to read as a real
    lt/gt screen-out without teaching (see prose_directives._range_terminate).
    """
    from src.core.survey_definition import evaluate

    f = parse_prose_question(
        "S1. What is your age? [ENTER NUMBER 1-100]\n"
        "[PN: CONTINUE IF AGES 18-60. TERMINATE ALL OTHERS.]",
        "S1",
    )
    assert "TERMINATE ALL OTHERS" in f.terminate_verbatim   # never dropped
    assert "terminate" not in f.missing            # resolved, not escalated
    rule = f.terminate_if
    assert rule is not None and rule.op == "or"
    assert evaluate(rule, {"S1": 17}) is True     # below the range
    assert evaluate(rule, {"S1": 61}) is True     # above the range
    assert evaluate(rule, {"S1": 30}) is False    # inside the range


def test_unmodelled_directives_are_kept_visible():
    f = parse_prose_question(
        "S4. Pick one\nSelect one.\n[PN: SHOW IMAGE deck_a.png]\nRed\nBlue", "S4"
    )
    assert any("SHOW IMAGE" in d for d in f.directives)


def test_empty_or_unlabelled_text_returns_nothing():
    assert parse_prose_question("", None) is None
    assert parse_prose_question("   \n  ", "S1") is None


def test_type_is_read_from_a_scripter_directive_not_just_the_instruction():
    """The single biggest gap found across all three real client documents.

    An unstated type was why 63/57/56 questions still needed a model — and the
    declaration was usually right there in the bracketed note, which the parser
    was not reading.
    """
    f = parse_prose_question(
        "Q9. What did you like about it?\n[PN: OPEN END]", "Q9"
    )
    assert f.qtype is QuestionType.OPEN_TEXT
    assert f.complete is True          # no model needed at all


def test_an_inline_bracket_on_the_question_line_gives_the_type():
    f = parse_prose_question("S1. What is your age? [ENTER NUMBER 1-100]", "S1")
    assert f.qtype is QuestionType.NUMERIC
    # And the bracket is not left in the wording a respondent reads.
    assert "[" not in f.text


def test_a_directive_cannot_declare_multi_without_options():
    """A type that contradicts the question's shape is not trusted."""
    f = parse_prose_question("Q3. Pick some\n[PN: SELECT ALL THAT APPLY]", "Q3")
    assert f.qtype is None
    assert "type" in f.missing


# --- Conventions inventoried from the three real client questionnaires -------
# Each case below is a shape that actually appears in Bubble / Vivo / GBK and
# was previously escalated to a model for a type it had already stated.


def test_textbox_directive_is_open_text():
    fields = parse_prose_question("Q5. What did you like about it?\n[PN: TEXTBOX]", "Q5")
    assert fields.qtype is QuestionType.OPEN_TEXT
    assert "type" not in fields.missing


def test_numeric_oe_beats_the_bare_oe_pattern():
    """"NUMERIC OE" contains "OE" — order in _TYPE_PATTERNS is load-bearing."""
    fields = parse_prose_question(
        "Q6. How many do you buy a year?\n[NUMERIC OE: 2-DIGIT MINIMUM]", "Q6"
    )
    assert fields.qtype is QuestionType.NUMERIC


def test_rotate_is_randomisation_but_keep_order_is_not():
    assert parse_prose_question("Q1. Pick one\n[PN: ROTATE]\nA\nB", "Q1").randomize
    assert not parse_prose_question(
        "Q1. Pick one\n[PN: KEEP ORDER]\nA\nB", "Q1"
    ).randomize


def test_yes_no_pair_is_single_without_an_instruction():
    fields = parse_prose_question("P1. Do you have children?\nYes\nNo [TERMINATE]", "P1")
    assert fields.qtype is QuestionType.SINGLE
    assert fields.terminate_if is not None


def test_three_plain_options_still_escalate():
    """The binary exception must not become a general guess at single vs multi."""
    fields = parse_prose_question("Q2. Which brands?\nAlpha\nBeta\nGamma", "Q2")
    assert fields.qtype is None
    assert "type" in fields.missing


def test_pipe_scale_row_makes_a_grid_even_after_a_wrapped_wording():
    # The wording wraps onto a second line, which the parser reads as an option
    # line; the scale row still has to be recognised after it.
    fields = parse_prose_question(
        "Q11. How important are each of the following\n"
        "to you when choosing a brand?\n"
        "0=Not Important At All | 1 | 2 | 3 | 4 | 5=Neutral | 6 | 7 | 8 | 9 | 10=Very\n"
        "Price\nScent",
        "Q11",
    )
    assert fields.qtype is QuestionType.GRID_SINGLE


def test_all_caps_code_wording_is_a_derived_variable():
    fields = parse_prose_question(
        "C1. CODE AGE – BASED ON RESPONSES AT S1 IF PANELISTS AGES 18-25", "C1"
    )
    assert fields.derived is True
    assert fields.qtype is QuestionType.INFO
    assert "type" not in fields.missing


def test_sentence_case_question_mentioning_a_code_is_not_derived():
    fields = parse_prose_question("S1. Code the item you bought\nYes\nNo", "S1")
    assert fields.derived is False


def test_borrowed_option_list_still_yields_a_type():
    fields = parse_prose_question(
        "Q27. Which statement is most believable? Select one.\n[SHOW Q26 LIST. RANDOMIZE]",
        "Q27",
    )
    assert fields.qtype is QuestionType.SINGLE
    assert fields.randomize is True


def test_rank_instruction_is_its_own_type():
    fields = parse_prose_question(
        "Q7. Which three brands would you choose? Rank your top three.\nAlpha\nBeta\nGamma",
        "Q7",
    )
    assert fields.qtype is QuestionType.RANK


# --- SP / MP punch abbreviations --------------------------------------------


def test_sp_directive_types_a_question_single() -> None:
    fields = parse_prose_question(
        "[SP] [IF D2 = 1]\nD4A. Do you work full-time?\n1. Yes\n0. No", "D4A"
    )
    assert fields is not None
    assert fields.qtype == QuestionType.SINGLE


def test_mp_directive_types_a_question_multi() -> None:
    fields = parse_prose_question(
        "[IF S2 = 2] [MP]\nR1. Why do you rent?\na. Cheaper\nb. Convenient", "R1"
    )
    assert fields is not None
    assert fields.qtype == QuestionType.MULTI


def test_number_box_with_sp_is_numeric_not_single() -> None:
    """'SP' inside a NUMBER BOX directive describes the widget, not the type."""
    fields = parse_prose_question(
        "[NUMBER BOX WITH SP]\nR3. What is your monthly rent?\n$ ______", "R3"
    )
    assert fields is not None
    assert fields.qtype == QuestionType.NUMERIC


def test_bare_sp_in_prose_does_not_type_a_question() -> None:
    """SP/MP are two-letter tokens; only a bracketed directive may claim them.

    'SP' here is a brand abbreviation in the respondent's wording. Typing the
    question off it would silently declare a multi-select single.
    """
    fields = parse_prose_question(
        "Q9. Which SP brand did you buy?\n1. Brand A\n2. Brand B", "Q9"
    )
    assert fields is not None
    assert fields.qtype is None
    assert "type" in fields.missing


def test_several_directives_on_one_line_are_all_read() -> None:
    """"[IF S2=1] [MP] [RANDOMIZE a-f]" is one line and three instructions."""
    fields = parse_prose_question(
        "[IF S2 = 2] [MP] [RANDOMIZE a-f]\nR1. Why do you rent?\na. Cheaper\nb. Prefer it",
        "R1",
    )
    assert fields is not None
    assert fields.qtype == QuestionType.MULTI
    assert fields.randomize is True
    # The wording is the question, not the directive line above it.
    assert fields.text is not None and fields.text.startswith("Why do you rent")
    assert [o.label for o in fields.options] == ["Cheaper", "Prefer it"]


# --- an option's wording wraps too -------------------------------------------


def test_wrapped_option_text_is_not_a_new_option() -> None:
    """SHED's S2 wraps each of its three housing answers onto a second line.

    Reading the tails as answers turned a 3-code question into a 6-code one
    whose codes lined up with nothing in the data file.
    """
    fields = parse_prose_question(
        "S2. Which best describes your housing arrangement?\n"
        "1. I [IF PPMARIT=1, INSERT: (and/or my spouse)]\n"
        "own [our/my] home\n"
        "2. I [IF PPMARIT=1, INSERT: (and/or my spouse)]\n"
        "pay rent\n"
        "3. I [IF PPMARIT=1, INSERT: (and/or my spouse)]\n"
        "do neither",
        "S2",
    )
    assert fields is not None
    assert [o.code for o in fields.options] == [1, 2, 3]
    assert fields.options[0].label.endswith("own [our/my] home")
    assert fields.options[1].label.endswith("pay rent")


def test_a_list_written_without_markers_keeps_every_option() -> None:
    """Continuation only ever follows a MARKED option, so an unmarked list is safe."""
    fields = parse_prose_question(
        "Q9. Do you agree?\nYes\nNo\nNot sure", "Q9"
    )
    assert fields is not None
    assert [o.label for o in fields.options] == ["Yes", "No", "Not sure"]


def test_only_one_line_is_absorbed_into_an_option() -> None:
    """A second unmarked line starts a new option rather than growing the last."""
    fields = parse_prose_question(
        "Q9. Pick one\n1. First answer\nwrapped tail\nA separate unmarked line", "Q9"
    )
    assert fields is not None
    assert [o.label for o in fields.options] == [
        "First answer wrapped tail",
        "A separate unmarked line",
    ]


# --- a number box is not an answer list --------------------------------------


def test_section_banner_is_not_an_answer() -> None:
    """"***** END OF RENT SECTION *****" divides the document; nobody picks it."""
    fields = parse_prose_question(
        "[NUMBER BOX range 1900-2014]\n"
        "D6. In what year did you buy your current home?\n"
        "***** END OF SCREENER/INCIDENCE SECTION *****\n"
        "***** RENT SECTION *******",
        "D6",
    )
    assert fields is not None
    assert fields.qtype == QuestionType.NUMERIC
    assert fields.options == []


def test_the_rule_the_answer_is_written_on_is_not_an_answer() -> None:
    """"_____", "____ %" and "[Num box, 0-25] ___" mark WHERE the answer goes."""
    fields = parse_prose_question(
        "[NUMBER BOX]\n"
        "I2. What percent of your income did you save?\n"
        "____ %  [NUM-BOX 0 TO 100]",
        "I2",
    )
    assert fields is not None
    assert fields.qtype == QuestionType.NUMERIC
    assert fields.options == []
    # The widget directive it carried is the type declaration — keep that.
    assert any("NUM-BOX" in d for d in fields.directives)


def test_open_entry_question_keeps_only_its_written_special_codes() -> None:
    """A year box with "888. Still enrolled" beside it has one code, not two."""
    fields = parse_prose_question(
        "[NUMBER BOX]\n"
        "ED4. In what year did you last attend?\n"
        "_____ [Num Box, Range 1900 - 2014]\n"
        "888. Still enrolled [SP]",
        "ED4",
    )
    assert fields is not None
    assert fields.qtype == QuestionType.NUMERIC
    assert [(o.code, o.label) for o in fields.options] == [(888, "Still enrolled")]


def test_a_widget_directive_outranks_a_bare_punch_abbreviation() -> None:
    """"[SP]" describes how the special codes punch, not what the question collects."""
    fields = parse_prose_question(
        "[SP]\n"
        "K1B. At what age do you expect to retire fully?\n"
        "[Num box PPAGE-99]\n"
        "888. Will never stop working  [SP]\n"
        "999. Not Sure  [SP]",
        "K1B",
    )
    assert fields is not None
    assert fields.qtype == QuestionType.NUMERIC
    assert [o.code for o in fields.options] == [888, 999]


def test_a_directive_with_nested_brackets_is_still_a_directive() -> None:
    """Real routing nests: "[ASK IF (K2_b=1 (yes) OR [K14a=2 (no)]) AND ...]".

    A non-nesting pattern stopped at the inner "]", so the line fell through to
    the answer parser and was glued onto the option above it.
    """
    fields = parse_prose_question(
        "K16. What percent of your paycheck do you contribute?\n"
        "888. Don't Know\n"
        "[ASK IF (K2_b=1 (yes) OR [K14a=2 (no) or refused]) AND (D3 = 1 or 2)]",
        "K16",
    )
    assert fields is not None
    assert [o.label for o in fields.options] == ["Don't Know"]
    assert any("ASK IF" in d for d in fields.directives)


def test_an_unclosed_directive_does_not_swallow_the_question():
    """One missing "]" turned a real question into display text.

    "Q21d.[ASK Q21e IF USED VIVO AT Q13 (Q13.7=1-5) And where do you feel
    VIVOHealth Pharmacy falls short? [OPEN END]" — the ASK bracket is never
    closed, so a tag pattern that may cross "[" runs from "[ASK" to the "]" of
    "[OPEN END]" and strips the entire sentence as a scripter tag. The wording
    went to nothing, the type went unknown, and the question was stored as an
    intro screen: present in the review queue, never asked, in a document a
    client had signed off.
    """
    line = (
        "Q21d.[ASK Q21e IF USED VIVO AT Q13 (Q13.7=1-5) And where do you feel "
        "VIVOHealth Pharmacy falls short, if at all? [OPEN END]"
    )
    fields = parse_prose_question(line, candidate_qid="Q21d")

    assert fields is not None
    assert fields.qtype is QuestionType.OPEN_TEXT
    # The sentence survives. The directive we could not parse stays visible in
    # it on purpose — a directive nobody noticed is worse than an untidy one.
    assert "falls short" in fields.text
    assert "ASK Q21e" in fields.text


def test_a_closed_directive_is_still_stripped_from_the_wording():
    """The guard must not stop real tags being read."""
    fields = parse_prose_question(
        "Q7. How likely are you to recommend it? [OPEN END]", candidate_qid="Q7"
    )
    assert fields is not None
    assert fields.qtype is QuestionType.OPEN_TEXT
    assert "[OPEN END]" not in fields.text
    assert fields.text.strip() == "How likely are you to recommend it?"


def test_several_tags_on_one_line_are_all_still_stripped():
    """"[SP] [RANDOMIZE]" is one trailing block, and stays one."""
    fields = parse_prose_question(
        "Q8. Which do you prefer? [SP] [RANDOMIZE]", candidate_qid="Q8"
    )
    assert fields is not None
    assert "[SP]" not in fields.text and "[RANDOMIZE]" not in fields.text
    assert fields.text.strip() == "Which do you prefer?"


def test_a_base_tag_is_a_label_not_part_of_the_sentence():
    """"[BASE: ALL]" must not reach the respondent's screen.

    In the questionnaires that open every question with one, leaving it in the
    wording puts the scripter's tag on every single screen of the survey.
    Unlike an unparsed condition — which stays visible on purpose, because a
    directive nobody noticed is worse than an untidy one — the tag is still
    kept in `directives` and in the source text, so a base naming a subgroup
    remains auditable as a condition nobody applied.
    """
    f = parse_prose_question(
        "[BASE: ALL] How satisfied are you with TMS outcomes?\n"
        "Very satisfied\nNot satisfied",
        "Q2",
    )
    assert f.text == "How satisfied are you with TMS outcomes?"
    assert "BASE: ALL" in " ".join(f.directives)
    assert [o.label for o in f.options] == ["Very satisfied", "Not satisfied"]


def test_an_answer_list_written_as_a_one_column_table_loses_its_separators():
    """"Other (specify) [ANCHOR] |" reached respondents with the pipe, and the
    pipe hid the tag, so the answer was never anchored."""
    from src.engines.questionnaire.prose_directives import parse_prose_question

    fields = parse_prose_question(
        "Q5. Which factors matter most? Select all that apply.\n"
        "Safety | \nEfficacy | \nOther (specify) [ANCHOR] | ",
        "Q5", None,
    )
    labels = [o.label for o in fields.options]
    assert labels == ["Safety", "Efficacy", "Other (specify)"]
    assert fields.options[-1].anchored


def test_a_fill_in_entry_with_a_range_is_a_number_not_an_answer():
    """"Fill in [0-100]____ [%]" was read as the question's only answer."""
    from src.engines.questionnaire.prose_directives import parse_prose_question

    fields = parse_prose_question(
        "A. What percentage do you discuss treatment with?\nFill in [0-100]_____________ [%]",
        "Q7", None,
    )
    assert fields.qtype is QuestionType.NUMERIC and fields.options == []
    assert fields.bounds == {"min": 0, "max": 100}


def test_open_entry_with_a_number_range_is_a_number_and_without_one_stays_text():
    from src.engines.questionnaire.prose_directives import parse_prose_question

    counted = parse_prose_question(
        "Q2. How many patients have you treated? Fill in [0-1,000]\n_____ [OE]", "Q2", None)
    assert counted.qtype is QuestionType.NUMERIC and counted.bounds == {"min": 0, "max": 1000}
    said = parse_prose_question("Q9. What else would you like to tell us? [OE]\n_____", "Q9", None)
    assert said.qtype is QuestionType.OPEN_TEXT and said.bounds == {}


def test_a_fill_in_with_a_range_and_whole_number_is_a_number():
    from src.core.survey_definition import QuestionType
    from src.engines.questionnaire.prose_directives import parse_prose_question

    chunk = ("[BASE: ALL] How many patients do you see per week? Fill in. [0-1000, WHOLE NUMBER]\n"
             "_____________ [OE]")
    fields = parse_prose_question(chunk, candidate_qid="Q2")
    assert fields.qtype is QuestionType.NUMERIC and fields.bounds == {"min": 0, "max": 1000}


def test_rank_from_1_to_n_is_a_ranking():
    from src.core.survey_definition import QuestionType
    from src.engines.questionnaire.prose_directives import parse_prose_question

    chunk = ("[BASE: ALL] What factors would most influence your decision? Please rank from 1 "
             "to 3, with 1 being the most influential. [RANDOMIZE]\nEfficacy |\nSafety |\nCost |")
    assert parse_prose_question(chunk, candidate_qid="Q24").qtype is QuestionType.RANK

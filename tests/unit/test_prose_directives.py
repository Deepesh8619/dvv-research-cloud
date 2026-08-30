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
    """The field the UI was showing as missing."""
    f = parse_prose_question(REAL, "S2")
    expr = f.terminate_if.to_dict()
    assert expr["op"] == "in"
    assert expr["value"] == [1, 2]                # exactly the tagged options
    assert expr["qid"] == "S2"                    # a real qid, not a placeholder


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


def test_a_prose_terminate_with_no_option_codes_is_preserved_not_invented():
    """'CONTINUE IF AGES 18-60. TERMINATE ALL OTHERS' is about a RANGE.

    Inventing option codes for it would screen out the wrong people.
    """
    f = parse_prose_question(
        "S1. What is your age? [ENTER NUMBER 1-100]\n"
        "[PN: CONTINUE IF AGES 18-60. TERMINATE ALL OTHERS.]",
        "S1",
    )
    assert f.terminate_if is None                 # no codes invented
    assert "TERMINATE ALL OTHERS" in f.terminate_verbatim   # never dropped
    assert "terminate" in f.missing               # escalated to a model


def test_unmodelled_directives_are_kept_visible():
    f = parse_prose_question(
        "S4. Pick one\nSelect one.\n[PN: SHOW IMAGE deck_a.png]\nRed\nBlue", "S4"
    )
    assert any("SHOW IMAGE" in d for d in f.directives)


def test_empty_or_unlabelled_text_returns_nothing():
    assert parse_prose_question("", None) is None
    assert parse_prose_question("   \n  ", "S1") is None

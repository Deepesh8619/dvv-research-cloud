"""A bracketed directive under an answer belongs to that answer.

From the live DA_GOAL_PLATFORM spec table:

    Variable list:
    1: Google Ads
    [IF GOOGLE ADS CURRENT USER]
    2: Meta Ads
    [IF META ADS CURRENT USER]
    [INSERT CURRENTLY USED BRANDS FROM DA_PLATFORM_USAGE 9-15]
    99: None of these

Every one of those lines says something. Read as answers in their own right
they became punch codes in the data file and choices on the respondent's
screen, and the conditions they carried were lost.
"""

from src.core.survey_definition import Option
from src.engines.questionnaire.option_directives import attach, read


def test_a_base_that_names_no_question_is_kept_verbatim_not_guessed():
    """"IF GOOGLE ADS CURRENT USER" is defined elsewhere in the document — a
    hidden variable, a punch range on an earlier question. Guessing which
    would put a condition nobody wrote in front of a respondent."""
    found = read("[IF GOOGLE ADS CURRENT USER]")
    assert found["kind"] == "base"
    assert found["verbatim"] == "IF GOOGLE ADS CURRENT USER"
    assert found["condition"] is None


def test_a_base_that_names_its_question_becomes_a_real_condition():
    found = read("[IF DA_PLATFORM_USAGE 9-15]")
    condition = found["condition"]
    assert condition.op == "in"
    assert condition.qid == "DA_PLATFORM_USAGE"
    assert condition.values == list(range(9, 16))
    assert condition.verbatim_text == "IF DA_PLATFORM_USAGE 9-15"


def test_a_list_that_continues_from_another_question_is_not_a_base():
    """"INSERT CURRENTLY USED BRANDS FROM DA_PLATFORM_USAGE 9-15" adds
    ANSWERS; treating it as a condition would hide the ones it names."""
    found = read("[INSERT CURRENTLY USED BRANDS FROM DA_PLATFORM_USAGE 9-15]")
    assert found["kind"] == "list_from"
    assert found["source_qid"] == "DA_PLATFORM_USAGE"
    assert found["codes"] == [9, 15]


def test_a_directive_that_states_neither_is_left_alone():
    for line in ("[RANDOMIZE]", "[SHOWCARD]", "Google Ads", "", "[PN: SHOW IMAGE]"):
        assert read(line) is None


def test_the_condition_lands_on_the_answer_above_it():
    option = Option(code=1, label="Google Ads")
    assert attach(option, "[IF Q7 1-3]") == "base"
    assert option.show_if is not None and option.show_if.qid == "Q7"

    unresolved = Option(code=2, label="Meta Ads")
    assert attach(unresolved, "[IF META ADS CURRENT USER]") == "base"
    assert unresolved.show_if is None  # kept for a reviewer, not invented

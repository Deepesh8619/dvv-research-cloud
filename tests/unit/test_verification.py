"""The verifier checks the document, instead of asking a model for an opinion.

Each case here is a mistake that actually happened this week, and the point of
every assertion is the same: the check names the line in the source that
disagrees. A finding with nothing to point at is an opinion.
"""

from src.core.survey_definition import Expression, Option, Question, QuestionType
from src.engines.questionnaire.verification import (
    covers_the_question,
    failed,
    verify_against_source,
)

SOURCE = """S12. Who do you manage prescriptions for? Select all that apply.
1. Yes - for a child under 18
2. Yes - for another adult
3. No, only myself"""


def _question(**kw) -> Question:
    base = dict(
        qid="S12",
        text="Who do you manage prescriptions for? Select all that apply.",
        type=QuestionType.MULTI,
        options=[
            Option(code=1, label="Yes - for a child under 18"),
            Option(code=2, label="Yes - for another adult"),
            Option(code=3, label="No, only myself"),
        ],
    )
    base.update(kw)
    return Question(**base)


def _names(question, source=SOURCE) -> set[str]:
    return {c.name for c in failed(verify_against_source(question, source))}


def test_a_faithful_extraction_passes_every_check():
    assert _names(_question()) == set()


def test_an_option_the_document_offers_and_the_definition_lost():
    """Five written, four extracted — and the question still looked complete,
    which is exactly how it survived into a live instrument."""
    lost = _question(options=[
        Option(code=1, label="Yes - for another adult"),
        Option(code=2, label="No, only myself"),
    ])
    checks = failed(verify_against_source(lost, SOURCE))
    assert "source_options_all_captured" in {c.name for c in checks}
    missing = next(c for c in checks if c.name == "source_options_all_captured")
    assert "child under 18" in missing.evidence  # it says WHICH line


def test_an_option_that_is_nowhere_in_the_document():
    invented = _question(options=[
        Option(code=1, label="Yes - for a child under 18"),
        Option(code=2, label="Yes - for a pet"),
    ])
    assert "options_are_in_the_source" in _names(invented)


def test_a_code_that_does_not_match_the_document():
    """The punch code is what lands in the data file; a shifted one silently
    re-labels every answer."""
    shifted = _question(options=[
        Option(code=7, label="Yes - for a child under 18"),
        Option(code=2, label="Yes - for another adult"),
        Option(code=3, label="No, only myself"),
    ])
    assert "codes_match_the_document" in _names(shifted)


def test_the_type_the_wording_asks_for():
    assert "type_matches_the_wording" in _names(_question(type=QuestionType.SINGLE))
    single_source = SOURCE.replace("Select all that apply", "Select one")
    assert "type_matches_the_wording" in _names(_question(), single_source)


def test_a_grid_with_no_rows_fails_the_checklist():
    grid = Question(qid="Q1", text="How often?", type=QuestionType.GRID_SINGLE)
    assert "grid_has_rows_and_columns" in _names(grid, "Q1. How often?")


def test_a_screen_out_naming_a_code_nobody_can_pick():
    """It can never fire, so the screen-out silently does nothing."""
    unreachable = _question(
        terminate_if=Expression(op="in", qid="S12", values=[9])
    )
    assert "terminate_names_a_real_code" in _names(unreachable)
    real = _question(terminate_if=Expression(op="in", qid="S12", values=[3]))
    assert "terminate_names_a_real_code" not in _names(real)


def test_a_scripter_tag_left_in_what_a_respondent_reads():
    tagged = _question(text="[PN: RANDOMIZE] Who do you manage prescriptions for?")
    assert "no_scripter_tag_in_respondent_text" in _names(tagged)


def test_the_checklist_admits_what_it_cannot_cover():
    """A question with no options gives it almost nothing to test, and "nothing
    failed" would be a hollow pass. Those still go to the model — the checklist
    takes the work it can settle and leaves the rest."""
    bare = Question(qid="Q1", text="Any comments?", type=QuestionType.SINGLE, options=[])
    assert covers_the_question(bare, verify_against_source(bare, "Q1. Any comments?")) is False

    full = _question()
    assert covers_the_question(full, verify_against_source(full, SOURCE)) is True

    written = Question(qid="Q2", text="Why?", type=QuestionType.OPEN_TEXT)
    assert covers_the_question(written, verify_against_source(written, "Q2. Why?")) is True


def test_a_table_chunk_is_not_read_as_an_answer_list():
    """A region reference table serialises to lines that look exactly like
    numbered answers — "1. Pacific (WA, OR, CA)" — and its leading number is a
    row index, not a punch code. Read as answers it reported a code mismatch on
    every row: the loudest possible finding about a question nobody is asked.

    Judging a table's rows is the grid reader's job; it has the real cells. This
    checklist has a flattened string and must not guess from it.
    """
    table_chunk = """C7a. Region definitions for quota purposes
1. Pacific (WA, OR, CA, AK, HI)
2. Mountain (MT, ID, WY, NV) | West | 23%
3. West North Central (ND, SD, NE) | Midwest | 22%"""
    question = Question(
        qid="C7a",
        text="Region definitions for quota purposes",
        options=[
            Option(code=3, label="Pacific (WA, OR, CA, AK, HI)"),
            Option(code=4, label="Mountain (MT, ID, WY, NV)"),
        ],
    )
    names = {c.name for c in failed(verify_against_source(question, table_chunk))}
    assert "codes_match_the_document" not in names
    assert "source_options_all_captured" not in names


def test_a_plain_answer_list_is_still_judged():
    """The table rule must not switch the checks off for ordinary questions."""
    shifted = _question(options=[
        Option(code=7, label="Yes - for a child under 18"),
        Option(code=2, label="Yes - for another adult"),
        Option(code=3, label="No, only myself"),
    ])
    assert "codes_match_the_document" in _names(shifted)


def test_a_hidden_variable_line_with_its_definition_counts_as_captured():
    from src.core.survey_definition import Option, Question, QuestionType
    from src.engines.questionnaire.verification import verify_against_source

    q = Question(qid="HID_GEO_REGION", text="Region", type=QuestionType.INFO, options=[
        Option(code=1, label="Northeast", definition="HID_GEO_DIVISION = 1,2"),
        Option(code=2, label="Midwest", definition="HID_GEO_DIVISION = 3,4")])
    src = ("Q Name | HID_GEO_REGION\n | Label: | Definition:\n"
           "1 | Northeast | HID_GEO_DIVISION = 1,2\n2 | Midwest | HID_GEO_DIVISION = 3,4")
    failed = {c.name for c in verify_against_source(q, src) if not c.passed}
    assert "source_options_all_captured" not in failed

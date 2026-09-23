"""What a re-read must refuse to do.

"Re-read all" is the button that carries a parser fix to studies already in the
database, so it is pressed on questionnaires nobody is watching closely. A
re-read that quietly makes a question WORSE is more damaging than no button at
all: the reviewer has no reason to look again.

Every case here was measured on the real Google Ads questionnaire before the
guard existed, and every one of them would have been saved over.
"""

from __future__ import annotations

from api.routes.questionnaire import _reread_is_regression
from src.core.survey_definition import Option, Question, QuestionType
from src.engines.questionnaire import schema
from src.engines.questionnaire.extractor import reread_question

SPEC_SOURCE = "Q Name: DA_TOP_GOAL\nWhat is your top objective?\nQ type: Std Single-Response (SR)"


def _q(**kw):
    base = dict(qid="Q1", text="Which do you prefer?", type=QuestionType.SINGLE)
    base.update(kw)
    return Question(**base)


def _opts(n):
    return [Option(code=i + 1, label=f"Answer {i + 1}") for i in range(n)]


# --- the prose parser must not re-read a spec table -------------------------


def test_a_spec_table_question_is_never_re_read_by_the_prose_parser():
    """It would read the question DIFFERENTLY, not better.

    `reread_question` replays `parse_prose_question` only; it never calls
    `parse_spec_table`. On a "Q Name: / Q type:" document that is a different
    set of rules, not a newer version of the same ones.
    """
    question = _q(qid="DA_TOP_GOAL", source_text=SPEC_SOURCE, extraction_method="spec_table")
    assert reread_question(question) is None


def test_a_prose_question_is_still_re_read():
    """The guard must not cost us the button it is protecting."""
    question = _q(
        qid="Q7",
        source_text="Q7. How satisfied were you?\n1. Very\n2. Not at all",
        extraction_method="prose_directives",
    )
    assert reread_question(question) is not None


# --- the regression guard ---------------------------------------------------


def test_losing_one_option_of_nine_is_a_regression():
    """DA_TOP_GOAL, measured: 9 options in, 8 out.

    The old guard only caught a drop to ZERO, so eight-of-nine sailed through.
    A respondent loses an answer and the data file loses a column.
    """
    old = _q(options=_opts(9))
    new = _q(options=_opts(8))
    assert _reread_is_regression(old, new) is True


def test_a_hidden_variable_becoming_a_question_is_a_regression():
    """HIDDEN_TOPBENEFIT, measured: info -> single with 9 options.

    The table says "Show / HV: Hidden variable". Turning that back into
    something a respondent is asked is the worst outcome in this file, and it
    ADDS options — so every size-based check passes it.
    """
    old = _q(type=QuestionType.INFO, options=[], flags=[schema.FLAG_DERIVED_VARIABLE])
    new = _q(type=QuestionType.SINGLE, options=_opts(9))
    assert _reread_is_regression(old, new) is True


def test_dropping_a_flag_that_refuses_fielding_is_a_regression():
    """MAXDIFF_OE, measured: TRADE_OFF_UNSUPPORTED silently dropped.

    `_apply_reread` replaces flags wholesale, so nothing announces the loss —
    and the next preflight would pass a block that cannot be fielded.
    """
    old = _q(options=_opts(3), flags=[schema.FLAG_TRADE_OFF_UNSUPPORTED])
    new = _q(options=_opts(3), flags=[])
    assert _reread_is_regression(old, new) is True


def test_losing_grid_rows_or_columns_is_a_regression():
    def grid(rows, cols):
        return _q(type=QuestionType.GRID_SINGLE, rows=_opts(rows), cols=_opts(cols))

    old = grid(9, 2)
    assert _reread_is_regression(old, grid(8, 2)) is True   # a brand lost
    assert _reread_is_regression(old, grid(9, 1)) is True   # a scale point lost


# --- and what must STILL be allowed through ---------------------------------


def test_a_real_improvement_is_not_a_regression():
    """The whole point of the button: a parser fix that ADDS structure."""
    old = _q(options=[])
    new = _q(options=_opts(9))
    assert _reread_is_regression(old, new) is False


def test_gaining_a_flag_is_not_a_regression():
    """A newer parser NOTICING something is exactly what we want."""
    old = _q(options=_opts(3), flags=[])
    new = _q(options=_opts(3), flags=[schema.FLAG_TRADE_OFF_UNSUPPORTED])
    assert _reread_is_regression(old, new) is False


def test_a_question_becoming_a_grid_is_not_a_regression():
    """The per-item repair (#54): one flat list becomes rows and columns.

    Options go 2 -> 0, which looks like a shrink and is not one: the answers
    moved into `cols` and nine items arrived in `rows`. Shrinkage only means
    loss WITHIN a type; across a type change the honest comparison is the
    total answer surface, which went 2 -> 11.
    """
    old = _q(options=_opts(2))
    new = _q(type=QuestionType.GRID_SINGLE, options=[], rows=_opts(9), cols=_opts(2))
    assert _reread_is_regression(old, new) is False


def test_a_type_change_that_really_loses_answers_is_still_a_regression():
    """The guard the case above must not open: a grid collapsing to nothing."""
    old = _q(type=QuestionType.GRID_SINGLE, options=[], rows=_opts(9), cols=_opts(2))
    new = _q(type=QuestionType.SINGLE, options=_opts(2))
    assert _reread_is_regression(old, new) is True

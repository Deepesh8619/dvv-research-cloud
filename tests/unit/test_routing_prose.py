"""Routing directives -> `show_if` expressions.

Every directive here is copied from a real questionnaire (Federal Reserve SHED
2014). The refusals matter as much as the parses: a `show_if` the parser
guessed wrong reclassifies real respondents as routing violations, which is
worse than leaving the directive as prose for a human.
"""

from __future__ import annotations

from src.core.survey_definition import evaluate
from src.engines.questionnaire.routing_prose import parse_routing


def test_simple_equality_becomes_membership() -> None:
    expr = parse_routing("IF D2 = 1")
    assert expr is not None
    assert expr.op == "in" and expr.qid == "D2" and expr.values == [1]
    assert evaluate(expr, {"D2": 1}) is True
    assert evaluate(expr, {"D2": 2}) is False


def test_comma_and_or_list_is_one_membership_test() -> None:
    expr = parse_routing("IF (D1 = 3, 4, 5, OR 6)")
    assert expr is not None
    assert expr.op == "in" and expr.values == [3, 4, 5, 6]
    assert evaluate(expr, {"D1": 5}) is True
    assert evaluate(expr, {"D1": 2}) is False


def test_only_ask_preamble_is_stripped() -> None:
    expr = parse_routing("ONLY ASK H0 - H6 IF S2 = 1")
    assert expr is not None
    assert expr.qid == "S2" and expr.values == [1]
    # The original wording is kept so a reviewer can check the reading.
    assert expr.verbatim_text == "ONLY ASK H0 - H6 IF S2 = 1"


def test_repeated_qid_joined_by_or() -> None:
    expr = parse_routing("IF S2 = 2 or S2=3")
    assert expr is not None and expr.op == "or"
    assert evaluate(expr, {"S2": 3}) is True
    assert evaluate(expr, {"S2": 1}) is False


def test_and_of_a_comparison_and_a_membership() -> None:
    expr = parse_routing("IF D6 < 2013 AND S2=1")
    assert expr is not None and expr.op == "and"
    assert evaluate(expr, {"D6": 2010, "S2": 1}) is True
    assert evaluate(expr, {"D6": 2014, "S2": 1}) is False
    assert evaluate(expr, {"D6": 2010, "S2": 2}) is False


def test_greater_or_equal_has_no_op_so_it_negates_less_than() -> None:
    """There is no `ge`; `x >= n` must mean exactly `not (x < n)`."""
    expr = parse_routing("IF D6>=2005")
    assert expr is not None and expr.op == "not"
    assert evaluate(expr, {"D6": 2005}) is True
    assert evaluate(expr, {"D6": 2004}) is False
    assert evaluate(expr, {"D6": 2020}) is True


def test_not_equal_negates_membership() -> None:
    expr = parse_routing("IF S2 <> 1")
    assert expr is not None and expr.op == "not"
    assert evaluate(expr, {"S2": 2}) is True
    assert evaluate(expr, {"S2": 1}) is False


def test_mixed_and_or_without_parentheses_is_refused() -> None:
    """No agreed precedence in a questionnaire margin — refuse, don't pick."""
    assert parse_routing("IF S2 = 1 AND D2 = 1 OR D2 = 2") is None


def test_non_routing_directives_are_refused() -> None:
    for directive in ("SP", "MP", "RANDOMIZE a-f", "NUMBER BOX range 1900-2014",
                      "TERMINATE", "SHOW Q26 LIST", ""):
        assert parse_routing(directive) is None, directive


def test_condition_on_something_that_is_not_a_question_is_refused() -> None:
    """"IF PPMARIT=1" style panel-profile tests are still qid-shaped and parse;
    free prose is not."""
    assert parse_routing("IF respondent owns their home") is None
    assert parse_routing("IF S2 = yes") is None


def test_directive_is_read_into_show_if_by_the_prose_parser() -> None:
    from src.engines.questionnaire.prose_directives import parse_prose_question

    fields = parse_prose_question(
        "[SP] [IF D2 = 1]\nD4C. Do you have another job?\n1. Yes\n0. No", "D4C"
    )
    assert fields is not None
    assert fields.show_if is not None
    assert fields.show_if.qid == "D2"
    assert fields.evidence["base"] == "[IF D2 = 1]"


def test_underscore_names_and_grid_rows_are_read():
    from src.engines.questionnaire.routing_prose import parse_routing

    e = parse_routing("IF HID_GOOGLE_USER = 2 or 3")
    assert (e.qid, e.values) == ("HID_GOOGLE_USER", [2, 3])
    rows = parse_routing("IF Q38.11=6-10 OR Q38.12=6-10")
    leaves = [c for part in rows.children for c in part.children]
    assert {(leaf.children[0].qid, leaf.children[0].row) for leaf in leaves} == {
        ("Q38", 11), ("Q38", 12)}

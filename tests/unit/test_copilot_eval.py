"""The eval set's file and its scoring: a typo must fail here, not mid-run."""

from pathlib import Path

from src.ai.copilot import TOOL_NAMES
from src.ai.copilot_eval import (
    load_cases,
    number_pattern,
    problems,
    score_case,
    score_turn,
)

CASES = Path(__file__).resolve().parents[2] / "evals" / "copilot" / "cases.json"


def test_the_case_file_is_valid_and_big_enough():
    cases = load_cases(CASES)
    assert problems(cases, TOOL_NAMES) == []
    assert len(cases) >= 40
    assert {c["category"] for c in cases} >= {
        "discovery", "questionnaire", "fielding", "data", "cards", "safety", "follow-up"}


def test_a_typo_is_caught():
    bad = [{"id": "x", "category": "c",
            "turns": [{"say": "hi", "expect": {"tools": ["feild_report"], "cardz": []}}]}]
    found = problems(bad, TOOL_NAMES)
    assert any("feild_report" in f for f in found)
    assert any("cardz" in f for f in found)


def test_a_figure_matches_as_a_model_may_write_it():
    import re

    pattern = number_pattern(61.5)
    assert re.search(pattern, "incidence is 61.5%")
    assert re.search(pattern, "about 62%")
    assert not re.search(pattern, "161.5") and not re.search(pattern, "62.5")


def test_scoring_checks_tools_cards_and_figures():
    facts = {"incidence_a": number_pattern(40)}
    expect = {"tools_any": ["field_report"], "mentions": ["{incidence_a}"], "cards": []}
    good = {"reply": "Incidence is 40%.", "tools_used": ["field_report"], "proposals": []}
    assert score_turn(expect, good, facts) == []

    guessed = {"reply": "Incidence is about 40%.", "tools_used": [], "proposals": []}
    failures = score_turn(expect, guessed, facts)
    assert "stated figures without looking anything up" in failures
    assert any("field_report" in f for f in failures)

    carded = {**good, "proposals": [{"kind": "close", "title": "Close link"}]}
    assert any("cards" in f for f in score_turn(expect, carded, facts))


def test_a_case_fails_when_the_conversation_stops_early():
    case = {"id": "x", "category": "c", "turns": [{"say": "a"}, {"say": "b"}]}
    result = score_case(case, [{"error": "HTTP 503"}], {})
    assert not result["passed"]
    assert "conversation stopped early" in result["failures"]

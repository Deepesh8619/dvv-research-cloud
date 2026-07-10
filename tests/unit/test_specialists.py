"""Specialist agent routing (src/ai/specialists.py).

Core claim under test: before this, Groq calls always sent all 8 tool
schemas regardless of the classified intent — this module's job is to
restrict the schema list to one specialist's owned tools, which is the
single biggest remaining token cost the earlier intent-routing work hadn't
addressed. Tests verify the partitioning is complete/non-overlapping, the
intent→specialist mapping covers every real intent, and navigator.py's
_groq_tools() actually respects the restriction.
"""

from api.intent_router import INTENT_CONFIGS, classify_specialist
from src.ai.navigator import TOOLS, _groq_tools
from src.ai.specialists import (
    INTENT_TO_SPECIALIST,
    SPECIALISTS,
    is_compound_request,
    specialist_for_intent,
    tools_for_specialist,
)

ALL_TOOL_NAMES = {t["name"] for t in TOOLS}


def test_specialist_tools_partition_full_registry_with_no_overlap():
    """Every tool belongs to exactly one specialist — no tool is owned by two
    specialists (ambiguous routing) and no tool is orphaned (unreachable via
    specialist-scoped calls)."""
    seen: dict[str, str] = {}
    for spec_name, spec in SPECIALISTS.items():
        for tool in spec.tools:
            assert tool not in seen, f"{tool} owned by both {seen.get(tool)} and {spec_name}"
            seen[tool] = spec_name

    assert set(seen.keys()) == ALL_TOOL_NAMES


def test_every_intent_maps_to_a_real_specialist():
    """Every intent_router.py intent has a specialist mapping, and every
    mapped specialist actually exists — a typo here would silently fall back
    to "analyst" via specialist_for_intent's default, masking the bug."""
    for intent_name in INTENT_CONFIGS:
        assert intent_name in INTENT_TO_SPECIALIST, f"{intent_name} has no specialist mapping"
        assert INTENT_TO_SPECIALIST[intent_name] in SPECIALISTS


def test_specialist_for_intent_falls_back_to_analyst():
    assert specialist_for_intent("some_future_intent_not_yet_mapped") == "analyst"


def test_classify_specialist_matches_intent_mapping():
    """classify_specialist should agree with classify_intent + the static
    mapping — it's a thin wrapper, not an independent classifier."""
    cases = [
        ("Cross satisfaction by gender", "analyst"),
        ("Make a bar chart of region", "showcase"),
        ("Check data quality", "steward"),
        ("Write python to correlate age and satisfaction", "concierge"),
        ("Compare t1 and t2", "showcase"),  # deck_reference -> showcase
    ]
    for question, expected_specialist in cases:
        specialist, intent = classify_specialist(question)
        assert specialist == expected_specialist, f"{question!r} -> {specialist}, intent={intent}"
        assert specialist_for_intent(intent) == specialist


def test_tools_for_specialist_returns_only_owned_tools():
    assert set(tools_for_specialist("steward")) == {
        "data_quality",
        "check_respondent_quality",
        "get_question_details",
        "explain_question_base",
        "search_questionnaire",
        "check_question_routing",
    }
    assert set(tools_for_specialist("concierge")) == {"generate_code"}
    assert set(tools_for_specialist("analyst")) == {
        "show_table",
        "generate_pivot",
        "query_data",
        "get_question_context",
    }


def test_tools_for_specialist_compound_adds_showcase_tools():
    """A compound request routed to a non-showcase specialist should gain
    Showcase's tools (visualization is the most common cross-specialist ask,
    e.g. "check data quality then chart the cleanest variable")."""
    plain = set(tools_for_specialist("steward", compound=False))
    compound = set(tools_for_specialist("steward", compound=True))
    steward_tools = {
        "data_quality",
        "check_respondent_quality",
        "get_question_details",
        "explain_question_base",
        "search_questionnaire",
        "check_question_routing",
    }
    assert plain == steward_tools
    assert compound == steward_tools | {"generate_chart", "compare_tables", "navigate_to"}


def test_tools_for_specialist_compound_is_noop_for_showcase_itself():
    """Showcase requesting compound tools shouldn't duplicate its own tools."""
    assert tools_for_specialist("showcase", compound=True) == tools_for_specialist(
        "showcase", compound=False
    )


def test_is_compound_request_detects_conjunctions():
    assert is_compound_request("Cross satisfaction by gender and chart it")
    assert is_compound_request("Show region then chart the result")
    assert not is_compound_request("Cross satisfaction by gender")
    assert not is_compound_request("Make a bar chart of region")


def test_groq_tools_full_registry_when_no_subset_given():
    schemas = _groq_tools(None)
    assert {s["function"]["name"] for s in schemas} == ALL_TOOL_NAMES


def test_groq_tools_restricted_to_subset():
    """This is the actual token-saving mechanism: only the named tools'
    schemas are serialized, not all 8."""
    schemas = _groq_tools(["show_table", "generate_pivot"])
    names = {s["function"]["name"] for s in schemas}
    assert names == {"show_table", "generate_pivot"}
    assert len(schemas) == 2


def test_groq_tools_empty_subset_sends_no_tools():
    assert _groq_tools([]) == []

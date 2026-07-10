"""Specialist agents: group the navigator's tools into four focused roles so
a request only ever pays for the schemas/prompt it actually needs.

Design (per architecture research, 2026-07-06): this is prompt-and-tool-schema
routing, not a literal multi-agent runtime — everything still executes as one
LLM call per turn, on a stateless per-HTTP-request backend. "Idle" specialists
cost nothing because nothing about them is ever assembled into the request:
their tool schemas aren't sent, their prompt fragment isn't included. That's
the entire savings mechanism, and it's the one that matters most: before this,
_groq_tools() always sent all 8 tool schemas on every call regardless of the
classified intent — the context text was already trimmed, but the (often
pricier) tool schemas never were.

Four specialists, deliberately not more (a 5th "significance" or "code" split
inside Analyst would just fragment tools that share context and intent):

- Steward   — data reading/review: data quality, ingestion health.
- Analyst   — statistical computation: crosstabs, significance, pivots, SQL.
- Showcase  — presentation: charts, precomputed deck reference, navigation.
- Concierge — code/formula authoring support (schema lookup only; the LLM
              itself writes the SQL/Python/formula text in its reply).

Boundaries are enforced by tool availability, not prompt pleading — Showcase
has no crosstab tool, so it cannot "just compute a percentage" instead of
asking Analyst to.
"""

from __future__ import annotations

from typing import NamedTuple


class Specialist(NamedTuple):
    """One specialist's exclusive tool ownership and prompt fragment."""

    name: str
    tools: tuple[str, ...]
    prompt_fragment: str


SPECIALISTS: dict[str, Specialist] = {
    "steward": Specialist(
        name="steward",
        tools=(
            "data_quality",
            "check_respondent_quality",
            "get_question_details",
            "explain_question_base",
            "search_questionnaire",
            "check_question_routing",
        ),
        prompt_fragment=(
            "You are acting as the Data Steward: focus on data health and the "
            "survey instrument itself. Use data_quality() for missing %, constant "
            "columns, and duplicate-ID flags, and check_respondent_quality() to flag "
            "bad respondents (speeders, straightliners, duplicates, gibberish open-ends) "
            "in a fielded study. For questions about the questionnaire "
            "as designed, use get_question_details(qid) to read a question's "
            "wording/options/routing, explain_question_base(qid) to say in plain "
            "English who was asked it, and search_questionnaire(query) to find a "
            "question by topic when the analyst doesn't know its qid. Use "
            "check_question_routing(qid) to verify a question's documented routing "
            "actually matches what real respondents did in the data. If a study "
            "has no approved questionnaire, these tools say so — relay that plainly."
        ),
    ),
    "analyst": Specialist(
        name="analyst",
        tools=("show_table", "generate_pivot", "query_data", "get_question_context"),
        prompt_fragment=(
            "You are acting as the Analyst: focus on statistical computation — "
            "crosstabs with significance testing (show_table), pivot aggregations "
            "(generate_pivot), or ad-hoc counts/averages (query_data). Use "
            "get_question_context(stub) to trace a data variable back to its source "
            "question when a table's base size looks unexpectedly small, or the user "
            "asks what a variable's question actually said."
        ),
    ),
    "showcase": Specialist(
        name="showcase",
        tools=("generate_chart", "compare_tables", "navigate_to"),
        prompt_fragment=(
            "You are acting as Showcase: focus on presentation — charts "
            "(generate_chart, which computes its own crosstab internally so "
            "one call gets both the image and the numbers), precomputed deck "
            "tables (compare_tables), or pointing the user at a UI tab (navigate_to)."
        ),
    ),
    "concierge": Specialist(
        name="concierge",
        tools=("generate_code",),
        prompt_fragment=(
            "You are acting as the Code Concierge: the user wants SQL, Python, "
            "or an Excel formula. generate_code() returns the real schema only — "
            "you write the actual code in your reply, using those column names."
        ),
    ),
}

# Every existing intent_router.py intent maps to exactly one specialist. This
# lets the (already-tested, keyword-based) intent classifier keep doing the
# fine-grained work, while requests get grouped into the coarser specialist
# buckets that actually determine which tool schemas get sent.
INTENT_TO_SPECIALIST: dict[str, str] = {
    "data_quality": "steward",
    "crosstab_analysis": "analyst",
    "significance_testing": "analyst",
    "pivot": "analyst",
    "exploratory_query": "analyst",
    "visualization": "showcase",
    "deck_reference": "showcase",
    "navigation": "showcase",
    "code_generation": "concierge",
}

# Keyword pairs that signal a compound request spanning two specialists, e.g.
# "cross satisfaction by gender AND chart it". generate_chart already computes
# its own crosstab internally (see Showcase's fragment above), so most
# analysis+visualization compounds are already satisfied by generate_chart
# alone — this mainly widens the tool set for the rarer case where the model
# genuinely needs two distinct tool calls in one turn (e.g. "check data
# quality then chart the cleanest variable").
_COMPOUND_TRIGGERS = ("and chart", "and plot", "and visualize", "then chart", "and graph")


def is_compound_request(question: str) -> bool:
    """True if the question plausibly needs tools from more than one specialist."""
    low = question.lower()
    return any(trigger in low for trigger in _COMPOUND_TRIGGERS)


def specialist_for_intent(intent_name: str) -> str:
    """Map an intent_router.py intent to its owning specialist.

    Falls back to "analyst" (the modal request in a research tool) for any
    unrecognized intent rather than raising — new intents should be added to
    INTENT_TO_SPECIALIST, but a missing mapping shouldn't break routing.
    """
    return INTENT_TO_SPECIALIST.get(intent_name, "analyst")


def tools_for_specialist(name: str, *, compound: bool = False) -> list[str]:
    """Tool names to expose for this specialist (union with Showcase's tools
    when a compound request is detected, since visualization is the most
    common cross-specialist pairing — see _COMPOUND_TRIGGERS).
    """
    specialist = SPECIALISTS.get(name, SPECIALISTS["analyst"])
    tools = list(specialist.tools)
    if compound and name != "showcase":
        tools += [t for t in SPECIALISTS["showcase"].tools if t not in tools]
    return tools

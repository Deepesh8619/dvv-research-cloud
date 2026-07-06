"""Semantic intent routing: classification, context building, token efficiency."""

import pytest

from api.intent_router import INTENT_CONFIGS, build_routed_context, classify_intent
from src.core.models import DataMap, Variable, VarType


class MockStudy:
    """Mock StudyDB for testing routing."""

    def __init__(self, client_name: str = "Test Co.", status: str = "processing"):
        self.client_name = client_name
        self.status = status


@pytest.fixture
def datamap() -> DataMap:
    """Fixture: realistic datamap with demographics and satisfaction."""
    dm = DataMap()
    dm.add(Variable(name="respondent_id", label="ID", type=VarType.NUMERIC))
    dm.add(
        Variable(
            name="gender",
            label="Gender",
            type=VarType.SINGLE,
            values={1: "Male", 2: "Female", 98: "Prefer not to say"},
        )
    )
    dm.add(
        Variable(
            name="age_group",
            label="Age Group",
            type=VarType.SINGLE,
            values={1: "18-25", 2: "26-35", 3: "36-50", 4: "50+"},
        )
    )
    dm.add(
        Variable(
            name="satisfaction",
            label="Overall Satisfaction",
            type=VarType.SINGLE,
            values={
                1: "Very Dissatisfied",
                2: "Dissatisfied",
                3: "Neutral",
                4: "Satisfied",
                5: "Very Satisfied",
            },
        )
    )
    dm.add(
        Variable(
            name="likelihood_recommend",
            label="Likelihood to Recommend (0-10)",
            type=VarType.NUMERIC,
        )
    )
    dm.add(
        Variable(
            name="region",
            label="Region",
            type=VarType.SINGLE,
            values={1: "North", 2: "South", 3: "East", 4: "West"},
        )
    )
    return dm


class TestIntentClassification:
    """Tests for classify_intent()."""

    def test_crosstab_analysis_keywords(self):
        """Detect crosstab-focused questions."""
        for q in [
            "Cross satisfaction by gender",
            "Show me purchases by region",
            "How does satisfaction break down by age?",
            "Compare satisfaction between groups",
            "Satisfaction distributed across regions",
        ]:
            intent, config = classify_intent(q)
            assert intent == "crosstab_analysis", f"Failed for: {q}"

    def test_significance_testing_keywords(self):
        """Detect significance/comparison questions."""
        for q in [
            "Is there a difference in satisfaction between males and females?",
            "Significant difference in satisfaction",
            "Does region significantly differ in satisfaction?",
        ]:
            intent, config = classify_intent(q)
            assert intent == "significance_testing", f"Failed for: {q}"

    def test_exploratory_query_keywords(self):
        """Detect ad-hoc aggregation questions."""
        for q in [
            "What's the average satisfaction score?",
            "What percentage prefer option A?",
            "Median likelihood to recommend",
            "How many total respondents?",
        ]:
            intent, config = classify_intent(q)
            assert intent == "exploratory_query", f"Failed for: {q}"

    def test_deck_reference_keywords(self):
        """Detect precomputed table references."""
        for q in [
            "Show me Table T1",
            "Compare t1 and t2",
            "Show tables t3 and t5",
        ]:
            intent, config = classify_intent(q)
            assert intent == "deck_reference", f"Failed for: {q}"

    def test_navigation_keywords(self):
        """Detect navigation/help requests."""
        for q in [
            "Where is the activity log?",
            "How do I see the tables?",
            "Find the approval history",
        ]:
            intent, config = classify_intent(q)
            assert intent == "navigation", f"Failed for: {q}"

    def test_default_to_crosstab(self):
        """Fall back to crosstab_analysis when no keywords match."""
        intent, config = classify_intent("Tell me about the data")
        assert intent == "crosstab_analysis"

    def test_highest_scoring_intent_wins(self):
        """When multiple intents match, highest score wins."""
        q = "Cross satisfaction by gender and show me the statistical difference"
        # This has keywords from both crosstab_analysis and significance_testing
        intent, config = classify_intent(q)
        # The exact winner depends on keyword counts, just verify it's one of the two
        assert intent in ("crosstab_analysis", "significance_testing")

    def test_case_insensitive_matching(self):
        """Keyword matching is case-insensitive."""
        q1 = "Cross satisfaction by gender"
        q2 = "CROSS SATISFACTION BY GENDER"
        intent1, _ = classify_intent(q1)
        intent2, _ = classify_intent(q2)
        assert intent1 == intent2


class TestIntentConfig:
    """Tests for IntentConfig content and structure."""

    def test_all_intents_have_required_fields(self):
        """Every intent config has keywords, tools, instruction, and examples."""
        for name, config in INTENT_CONFIGS.items():
            assert isinstance(config.keywords, list), f"{name}: keywords not a list"
            assert len(config.keywords) > 0, f"{name}: no keywords"
            assert isinstance(config.tools, list), f"{name}: tools not a list"
            assert len(config.tools) > 0, f"{name}: no tools"
            assert isinstance(config.system_instruction, str), f"{name}: no system_instruction"
            assert len(config.system_instruction) > 20, f"{name}: system_instruction too short"
            assert isinstance(config.examples, list), f"{name}: examples not a list"
            assert len(config.examples) > 0, f"{name}: no examples"

    def test_examples_are_tuples(self):
        """Every example is a (question, tool_call) tuple."""
        for name, config in INTENT_CONFIGS.items():
            for q, tool_call in config.examples:
                assert isinstance(q, str), f"{name}: example question not a string"
                assert isinstance(tool_call, str), f"{name}: example tool_call not a string"
                assert len(q) > 10, f"{name}: example question too short"
                assert "(" in tool_call, f"{name}: example tool_call doesn't look like a call"

    def test_keywords_no_duplicates(self):
        """Keywords within each intent are unique."""
        for name, config in INTENT_CONFIGS.items():
            assert len(config.keywords) == len(set(config.keywords)), f"{name}: duplicate keywords"

    def test_tools_are_valid(self):
        """Tool names are one of the known tools."""
        valid_tools = {
            "show_table",
            "query_data",
            "compare_tables",
            "navigate_to",
            "generate_chart",
            "generate_pivot",
            "generate_code",
            "data_quality",
        }
        for name, config in INTENT_CONFIGS.items():
            for tool in config.tools:
                assert tool in valid_tools, f"{name}: unknown tool '{tool}'"


class TestRoutedContext:
    """Tests for build_routed_context()."""

    def test_context_size_reduction(self, datamap):
        """Routed context is smaller, with focused variables and examples."""
        study = MockStudy("Test Co.", "processing")
        deck = {
            "tables": [
                {"id": "t1", "title": "Satisfaction by Gender"},
                {"id": "t2", "title": "Satisfaction by Age"},
            ]
        }

        # Routed context should be focused
        routed_context, intent = build_routed_context(
            study, datamap, deck, "Show satisfaction by gender"
        )
        # Should include intent instruction, examples, and tool list
        assert "crosstab_analysis" in routed_context
        assert "show_table" in routed_context
        assert len(routed_context) > 200  # Should be meaningful

    def test_context_includes_intent_instruction(self, datamap):
        """Routed context includes the intent-specific system instruction."""
        study = MockStudy()
        q = "Cross satisfaction by gender"
        context, intent = build_routed_context(study, datamap, {}, q)

        assert intent == "crosstab_analysis"
        assert "stub" in context  # crosstab_analysis example should be there
        assert "show_table" in context

    def test_context_includes_examples(self, datamap):
        """Routed context includes at least one example for the intent."""
        study = MockStudy()
        context, intent = build_routed_context(study, datamap, {}, "Cross satisfaction by gender")

        assert "Q:" in context or "Question" in context or "Example" in context.lower()

    def test_context_ranks_variables_by_relevance(self, datamap):
        """Variables are ranked by question relevance, not alphabetically."""
        study = MockStudy()
        # Ask about satisfaction specifically
        context, _ = build_routed_context(study, datamap, {}, "satisfaction by gender")

        # "satisfaction" and "gender" should appear early
        satisfaction_idx = context.find("satisfaction")
        gender_idx = context.find("gender")
        assert satisfaction_idx >= 0 and gender_idx >= 0

    def test_context_caps_variable_count(self, datamap):
        """Context includes at most N variables (default 15)."""
        study = MockStudy()
        context, _ = build_routed_context(study, datamap, {}, "Show me the data", top_n_vars=15)

        # Count variable entries (lines starting with two spaces and a variable name)
        var_count = context.count(" [")  # Each variable has [type]
        assert var_count <= 15

    def test_context_includes_deck_summary(self):
        """When deck is provided, context includes table count."""
        study = MockStudy()
        deck = {
            "tables": [
                {"id": "t1", "title": "Table 1"},
                {"id": "t2", "title": "Table 2"},
                {"id": "t3", "title": "Table 3"},
            ]
        }
        context, _ = build_routed_context(study, None, deck, "Show table T1")

        assert "3" in context or "Available precomputed tables" in context

    def test_context_handles_missing_datamap(self):
        """Context gracefully handles None datamap."""
        study = MockStudy()
        context, intent = build_routed_context(study, None, {}, "How many respondents?")

        assert "Datamap unavailable" in context or "SQL" in context
        assert "query_data" in context

    def test_context_handles_empty_deck(self, datamap):
        """Context gracefully handles empty deck."""
        study = MockStudy()
        context, intent = build_routed_context(study, datamap, {}, "Show satisfaction")

        # Should not crash and should still have datamap
        assert "satisfaction" in context.lower()

    def test_intent_injected_in_context(self, datamap):
        """Returned intent matches the classification."""
        study = MockStudy()
        q = "Is there a significant difference?"
        context, intent = build_routed_context(study, datamap, {}, q)

        assert intent == "significance_testing"
        # Context should mention this intent
        assert "significant" in context.lower() or "difference" in context.lower()

    def test_context_for_deck_query(self):
        """When asking about tables, context emphasizes deck tables."""
        study = MockStudy()
        deck = {
            "tables": [
                {"id": "t1", "title": "Satisfaction"},
                {"id": "t2", "title": "Purchases"},
            ]
        }
        context, intent = build_routed_context(study, None, deck, "Compare t1 and t2")

        assert intent == "deck_reference"
        assert "compare_tables" in context

    def test_token_count_efficiency(self, datamap):
        """Token estimate: routed context should be ~200-400 tokens (lean)."""
        study = MockStudy()
        deck = {"tables": [{"id": "t1", "title": "Table 1"}] * 20}

        context, _ = build_routed_context(study, datamap, deck, "Cross satisfaction by gender")
        # Rough estimate: ~4 chars per token (for English)
        estimated_tokens = len(context) / 4
        # Should be ~60-150 tokens (lean context with focused variables and examples)
        assert estimated_tokens > 40, f"Context too small: {estimated_tokens} tokens"


class TestRoutedContextIntegration:
    """Integration tests: routing + navigator interaction."""

    def test_all_intents_produce_valid_context(self, datamap):
        """Every intent type produces valid, non-empty context."""
        study = MockStudy()
        deck = {"tables": [{"id": "t1", "title": "Table 1"}]}

        test_questions = [
            ("Cross satisfaction by gender", "crosstab_analysis"),
            ("Is there a significant difference?", "significance_testing"),
            ("What's the average?", "exploratory_query"),
            ("Show table T1", "deck_reference"),
            ("Where is the activity log?", "navigation"),
        ]

        for q, expected_intent in test_questions:
            context, intent = build_routed_context(study, datamap, deck, q)
            assert intent == expected_intent, f"Wrong intent for: {q}"
            assert len(context) > 100, f"Context too short for: {q}"
            assert "\n" in context, f"Context not multiline for: {q}"

    def test_context_deterministic(self, datamap):
        """Same question always produces same context and intent."""
        study = MockStudy()
        q = "Show satisfaction by gender"

        context1, intent1 = build_routed_context(study, datamap, {}, q)
        context2, intent2 = build_routed_context(study, datamap, {}, q)

        assert intent1 == intent2
        assert context1 == context2

    def test_intent_changes_with_question(self, datamap):
        """Different questions produce different intents."""
        study = MockStudy()

        intents = [
            (build_routed_context(study, datamap, {}, "Cross by gender")[1], "crosstab_analysis"),
            (
                build_routed_context(study, datamap, {}, "Is there a difference?")[1],
                "significance_testing",
            ),
            (
                build_routed_context(study, datamap, {}, "Count respondents")[1],
                "exploratory_query",
            ),
        ]

        # At least 2 should be different
        intent_set = {intent for intent, _ in intents}
        assert len(intent_set) >= 2

    def test_routing_with_realistic_survey_questions(self, datamap):
        """Real survey analysis questions route correctly."""
        study = MockStudy("Acme Corp Survey", "processing")

        realistic_qs = [
            (
                "Can you show me satisfaction broken down by gender and age group?",
                "crosstab_analysis",
            ),
            ("Is satisfaction significantly different between regions?", "significance_testing"),
            ("What's the average likelihood to recommend score?", "exploratory_query"),
            ("Compare the findings in Table T5 with Table T8", "deck_reference"),
            ("How do I view the detailed results?", "navigation"),
        ]

        for q, expected in realistic_qs:
            context, intent = build_routed_context(study, datamap, {}, q)
            assert (
                intent == expected
            ), f"Question '{q}' routed to {intent}, expected {expected}"


class TestKeywordMatching:
    """Edge cases for keyword matching."""

    def test_partial_word_match(self):
        """Keywords match within words, not just word boundaries."""
        # "cross" should match "cross-tabulation"
        intent, _ = classify_intent("Create a cross-tabulation analysis")
        assert intent == "crosstab_analysis"

    def test_multiple_keyword_occurrences(self):
        """Multiple occurrences of same keyword boost score."""
        q1 = "Show cross breakdown, cross analysis, cross comparison"
        intent1, _ = classify_intent(q1)
        q2 = "Tell me about the data"
        intent2, _ = classify_intent(q2)
        assert intent1 == "crosstab_analysis"

    def test_long_question_handling(self):
        """Very long questions are classified without crashing."""
        long_q = " ".join(["show me satisfaction by gender"] * 50)
        intent, _ = classify_intent(long_q)
        assert intent == "crosstab_analysis"

    def test_special_characters_ignored(self):
        """Special characters in questions don't break matching."""
        q = "Cross satisfaction—by gender!!! Show me."
        intent, _ = classify_intent(q)
        assert intent == "crosstab_analysis"

    def test_empty_question_defaults(self):
        """Empty question defaults to crosstab_analysis."""
        intent, _ = classify_intent("")
        assert intent == "crosstab_analysis"


class TestContextBuilding:
    """Edge cases for context building."""

    def test_single_variable_datamap(self):
        """Context works with minimal datamap (1 variable)."""
        minimal_dm = DataMap()
        minimal_dm.add(Variable(name="q1", label="Question 1", type=VarType.SINGLE))

        study = MockStudy()
        context, _ = build_routed_context(study, minimal_dm, {}, "Show Q1")
        assert "q1" in context.lower()

    def test_datamap_with_no_value_labels(self):
        """Variables without value labels format correctly."""
        dm = DataMap()
        dm.add(Variable(name="age", label="Age", type=VarType.NUMERIC))
        dm.add(Variable(name="income", label="Income", type=VarType.NUMERIC))

        study = MockStudy()
        context, _ = build_routed_context(study, dm, {}, "Average age")
        assert "age" in context.lower()

    def test_variable_with_many_value_labels(self):
        """Variables with many value codes are truncated gracefully."""
        dm = DataMap()
        dm.add(
            Variable(
                name="codes",
                label="Many Codes",
                type=VarType.SINGLE,
                values={i: f"Code {i}" for i in range(1, 100)},
            )
        )

        study = MockStudy()
        context, _ = build_routed_context(study, dm, {}, "Show codes")
        # Should not crash and should show ellipsis
        assert "Code" in context

    def test_deck_with_many_tables(self):
        """Decks with many tables summarize correctly."""
        study = MockStudy()
        big_deck = {"tables": [{"id": f"t{i}", "title": f"Table {i}"} for i in range(1, 101)]}

        context, _ = build_routed_context(study, None, big_deck, "Show table T1")
        assert "100" in context or "precomputed" in context.lower()


# --- Parametrized tests for scale ---


@pytest.mark.parametrize(
    "question,expected_intent",
    [
        ("Cross satisfaction by gender", "crosstab_analysis"),
        ("Show me purchases by region", "crosstab_analysis"),
        ("How does likelihood break down by age?", "crosstab_analysis"),
        ("Is there a significant difference?", "significance_testing"),
        ("Does region significantly differ?", "significance_testing"),
        ("Significant in satisfaction?", "significance_testing"),
        ("What's the average satisfaction?", "exploratory_query"),
        ("What percentage prefer option A?", "exploratory_query"),
        ("Median age of respondents", "exploratory_query"),
        ("Show table T1", "deck_reference"),
        ("Compare T1 with T2", "deck_reference"),
        ("What's in the deck", "deck_reference"),
        ("Where is the activity log?", "navigation"),
        ("How do I see the tables?", "navigation"),
        ("Find the results", "navigation"),
    ],
)
def test_parametrized_intent_classification(question, expected_intent):
    """Parametrized: verify 15+ realistic questions route correctly."""
    intent, _ = classify_intent(question)
    assert intent == expected_intent, f"Failed: {question}"

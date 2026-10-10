"""Every known failure reaches every agent it is meant for.

A lesson written down and never put in front of the model that needs it is not
learned. This fails the moment a prompt stops carrying one of its rules.
"""

from src.ai import copilot
from src.ai.known_failures import KNOWN_FAILURES, rules_for
from src.engines.questionnaire import schema, study_understanding

_PROMPTS = {
    "extract": lambda: schema.build_extraction_prompt("Q1. Text?", "Q1"),
    "residue": lambda: schema.build_residue_prompt(qid="Q1", missing=["type"]),
    "understand": lambda: study_understanding.build_prompt("The brief.", ["Q1: Text?"]),
    "copilot": lambda: copilot.SYSTEM_PROMPT.format(
        known_failures=copilot.block_for("copilot"), today="d", user="u", role="r", page=""),
}


def test_every_rule_reaches_every_agent_it_names():
    for agent, build in _PROMPTS.items():
        prompt = build()
        missing = [r for r in rules_for(agent) if r not in prompt]
        assert not missing, f"{agent} prompt is missing: {missing}"


def test_every_failure_names_an_agent_that_exists_and_its_incident():
    for failure in KNOWN_FAILURES:
        assert failure.agents and failure.agents <= set(_PROMPTS)
        assert failure.incident.strip()

"""Mistakes this system has already made, so its AI agents do not make them again.

Each entry is a rule, the real incident that taught it, and the agents it binds:

  extract     the model that reads a whole question chunk
  residue     the model asked only for what the parsers could not settle
  understand  the model that reads the study's instructions first
  copilot     the assistant in the side panel

This is the ONE list. Prompts are built from it, so a lesson added here reaches
every agent it names, and `tests/unit/test_known_failures.py` fails if a prompt
stops carrying a rule meant for it. The deterministic agents (the parsers, the
QA loop) learn the same lessons as code and tests; this is for the agents that
learn from words.

Rules are short on purpose: every line is paid for on every call.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KnownFailure:
    rule: str
    incident: str
    agents: frozenset[str]
    # The same rule in as few words as possible, for the residue prompt — the
    # smallest call, made most often, with a hard size budget.
    short: str = ""


KNOWN_FAILURES: tuple[KnownFailure, ...] = (
    KnownFailure(
        "Empty boxes under \"MUST SUM TO N\" or \"ADD TO AT LEAST N\" are one number per row "
        "(type numeric, one row per item) — never a rating grid.",
        "TRD referrer Q2 (\"proportion in each category, must sum to 100%\") was read as a "
        "rating grid with no scale; respondents saw nothing to fill in. 2026-10-10.",
        frozenset({"extract", "residue"}),
        "\"MUST SUM TO N\" over empty boxes = numeric, one row per item; never a grid.",
    ),
    KnownFailure(
        "A table with a \"COLUMNS:\" row, a row of codes, then a \"[ROWS ...]\" or "
        "\"[RANDOMIZE]\" row is a grid: the coded row is the scale, the rows below are the items.",
        "Both TRD studies wrote every rating grid that way; eight reached respondents with no "
        "rows and no columns. 2026-10-10.",
        frozenset({"extract", "residue"}),
        "COLUMNS: row + code row + [ROWS] row = a grid: codes are the scale, rows are items.",
    ),
    KnownFailure(
        "A choice question must have answers to choose from. If the source gives none, do not "
        "return a choice type with an empty list — leave the type undecided.",
        "A question with no answers was approved and shipped to a test link. 2026-10-10.",
        frozenset({"extract", "residue"}),
        "Never a choice type with no answers; leave the type undecided.",
    ),
    KnownFailure(
        "Never copy a scripter note ([...], PN:, ANCHOR, EXCLUSIVE, TERMINATE, SHOW IF) or a "
        "table separator \"|\" into question text or an answer label.",
        "Respondents saw \"Other (specify) [ANCHOR] |\" and \"Randomize: Yes | Q type: Custom\" "
        "as answers. 2026-10-10.",
        frozenset({"extract"}),
    ),
    KnownFailure(
        "One chunk is one question. A second question sentence after the answer list belongs "
        "to the NEXT question — never read it as an option.",
        "A question with a forgotten [BASE:] tag rode inside the question before it, in both "
        "TRD studies, and was never asked. 2026-10-10.",
        frozenset({"extract"}),
    ),
    KnownFailure(
        "An instruction about who is NOT screened out (\"Do not term respondents from the CRM\") "
        "governs every screen-out in the survey — name every screener question it applies to.",
        "Bubble's brief exempted CRM respondents; every screen-out would have removed them. "
        "2026-10-06.",
        frozenset({"understand"}),
    ),
    KnownFailure(
        "Compare the screening criteria with the quota groups and screen-outs. A quota group "
        "that screening excludes is a contradiction to raise with the client.",
        "Google Ads screened for 10-999 employees but set a minimum quota for 2-9. 2026-10-06.",
        frozenset({"understand"}),
    ),
    KnownFailure(
        "Never call a questionnaire ready while its QA record has items that need a person or "
        "the document shows more questions than the study holds. Say which, and where.",
        "\"Approve clean\" approved a question with nothing to answer; a study once held 1 of "
        "80 questions with no error shown. 2026-10.",
        frozenset({"copilot"}),
    ),
    KnownFailure(
        "A test link opening proves nothing: it skips the pre-launch check. Report the checks "
        "it lists as failing.",
        "A broken constant-sum question reached a test link because test links are never "
        "refused. 2026-10-10.",
        frozenset({"copilot"}),
    ),
    KnownFailure(
        "Never suggest sending client questionnaires or data to a free AI tier or any outside "
        "service. Client documents are under NDA.",
        "A local measurement silently sent client questionnaires to Groq's free tier. "
        "2026-10-04.",
        frozenset({"copilot"}),
    ),
)


def rules_for(agent: str) -> list[str]:
    """The rules one agent's prompt carries — short forms for the residue call."""
    return [
        (f.short or f.rule) if agent == "residue" else f.rule
        for f in KNOWN_FAILURES if agent in f.agents
    ]


def block_for(agent: str, heading: str = "Known mistakes — never repeat these:") -> str:
    """The rules for one agent, as a prompt block ("" when it has none)."""
    rules = rules_for(agent)
    if not rules:
        return ""
    return heading + "\n" + "\n".join(f"- {r}" for r in rules) + "\n"

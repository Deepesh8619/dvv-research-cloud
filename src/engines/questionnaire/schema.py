"""JSON schemas and prompts for questionnaire extraction + verification.

Two LLM shapes live here, both consumed through `src.ai.router.run_task`:

1. EXTRACTION_SCHEMA / build_extraction_prompt — "one text chunk in, one
   structured Question out". Sent to Claude with `tool_choice` forcing a
   schema-valid `emit` call (router `_claude_generate`), and degradable to a
   local model that imitates the schema-derived instruction+example
   (router `_schema_to_instruction`).

2. VERIFICATION_SCHEMA / build_verification_prompt — the second, grounded
   "does this structured Question faithfully represent the source?" pass. This
   is the core hallucination mitigation, not optional polish.

The schemas are intentionally *flat and permissive* (nullable structured
routing, strings for enums we re-validate in code). We do NOT trust the model
to enforce cross-field invariants; `extractor.py` does that deterministically
after the fact. The schema's only job is to guarantee we get parseable JSON
with the keys we need.
"""

from __future__ import annotations

import json

from src.core.survey_definition import QuestionType

# The exact enum values the model is allowed to choose. Derived from the real
# QuestionType StrEnum so the prompt can never drift from the model this
# populates. Passed literally into both the schema and the prompt text.
QUESTION_TYPE_VALUES: list[str] = [t.value for t in QuestionType]

# Flags the extractor / verifier can attach. Kept here as the single source of
# truth so tests and callers reference constants, not magic strings.
FLAG_AMBIGUOUS_ROUTING = "AMBIGUOUS_ROUTING"
FLAG_UNRESOLVED_ROUTING_QID = "UNRESOLVED_ROUTING_QID"
FLAG_DUPLICATE_OPTION_CODE = "DUPLICATE_OPTION_CODE"
FLAG_EMPTY_OPTIONS = "EMPTY_OPTIONS"
FLAG_VERIFICATION_DISCREPANCY = "VERIFICATION_DISCREPANCY"
FLAG_VERIFICATION_UNAVAILABLE = "VERIFICATION_UNAVAILABLE"
FLAG_EXTRACTION_UNAVAILABLE = "EXTRACTION_UNAVAILABLE"
# Config-shaped extraction failure (e.g. a missing/unset API key): one root
# cause, fixable in one place — distinct from a transient EXTRACTION_UNAVAILABLE
# outage and from a model-rejected-input EXTRACTION_MODEL_ERROR, so a reviewer
# can tell "the deploy is missing a key" from "this document is unparseable".
FLAG_EXTRACTION_CONFIG_ERROR = "EXTRACTION_CONFIG_ERROR"
# The model responded but its output didn't match the schema (ModelOutputError):
# a real document/extraction problem for this chunk, not an outage — never
# triggers the circuit breaker.
FLAG_EXTRACTION_MODEL_ERROR = "EXTRACTION_MODEL_ERROR"


# --- Extraction schema ------------------------------------------------------

# Note on shape: `run_task`'s `_validate` only checks top-level required keys;
# Claude's tool_choice enforces the full nested schema; the local path only
# sees top-level field descriptions (`_schema_to_instruction` doesn't recurse).
# So we keep the invariants the model MUST get right at the top level and
# describe nested structure in prose in the prompt as well.
EXTRACTION_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "qid": {
            "type": "string",
            "description": "Question id exactly as labelled in the source (e.g. Q5, S3). "
            "Never invent one.",
        },
        "text": {
            "type": "string",
            "description": "The question wording shown to the respondent, verbatim.",
        },
        "type": {
            "type": "string",
            "enum": QUESTION_TYPE_VALUES,
            "description": "One of the allowed question types.",
        },
        "options": {
            "type": "array",
            "description": "Answer choices only. NOT interviewer instructions.",
            "items": {
                "type": "object",
                "properties": {
                    "code": {"type": "integer"},
                    "label": {"type": "string"},
                },
                "required": ["code", "label"],
            },
        },
        "rows": {
            "type": "array",
            "description": "Grid row items (statements), for grid types only.",
            "items": {
                "type": "object",
                "properties": {
                    "code": {"type": "integer"},
                    "label": {"type": "string"},
                },
                "required": ["code", "label"],
            },
        },
        "cols": {
            "type": "array",
            "description": "Grid column scale points, for grid types only.",
            "items": {
                "type": "object",
                "properties": {
                    "code": {"type": "integer"},
                    "label": {"type": "string"},
                },
                "required": ["code", "label"],
            },
        },
        "required": {"type": "boolean", "description": "Must the respondent answer?"},
        "instructions": {
            "type": "array",
            "description": "Interviewer/programmer notes extracted as CONTEXT, never as options "
            "(e.g. 'INTERVIEWER: READ LIST', 'DO NOT READ').",
            "items": {"type": "string"},
        },
        "routing": {
            "type": "object",
            "description": "Skip/show/terminate logic. Fill verbatim_text with the exact routing "
            "sentence copied from the source (or empty string if none). Only fill the structured "
            "show_if/terminate_if when the source explicitly names WHICH prior question and answer "
            "gates this one; otherwise leave them null and set ambiguous=true.",
            "properties": {
                "verbatim_text": {
                    "type": "string",
                    "description": "Exact routing sentence from the source, copied not "
                    "paraphrased. Empty string if the chunk states no routing.",
                },
                "ambiguous": {
                    "type": "boolean",
                    "description": "True if routing is mentioned but the source does NOT make "
                    "clear which prior qid/answer it depends on.",
                },
                "show_if": {
                    "description": "Structured condition to SHOW this question, or null. Shape: "
                    '{"op": "eq"|"ne"|"in"|"gt"|"lt"|"selected"|"and"|"or"|"not", '
                    '"qid": "<prior qid>", "value": <code>, "values": [<codes>], '
                    '"children": [<nested>]}. Null unless unambiguous.',
                    "type": ["object", "null"],
                },
                "terminate_if": {
                    "description": "Structured condition to TERMINATE/screen-out, or null. Same "
                    "shape as show_if. Null unless unambiguous.",
                    "type": ["object", "null"],
                },
            },
            "required": ["verbatim_text", "ambiguous", "show_if", "terminate_if"],
        },
        "confidence": {
            "type": "object",
            "description": "Your 0.0-1.0 confidence per major field.",
            "properties": {
                "text": {"type": "number"},
                "type": {"type": "number"},
                "options": {"type": "number"},
                "routing": {"type": "number"},
            },
            "required": ["text", "type", "options", "routing"],
        },
    },
    "required": ["qid", "text", "type", "options", "routing", "confidence"],
}


# --- Verification schema ----------------------------------------------------

VERIFICATION_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "faithful": {
            "type": "boolean",
            "description": "True only if the structured Question faithfully represents the source "
            "text with no invented content, correct type, and no missed routing.",
        },
        "discrepancies": {
            "type": "array",
            "description": "One short string per problem found (invented option, wrong type, "
            "missed routing, wrong qid, missing option). Empty if faithful.",
            "items": {"type": "string"},
        },
    },
    "required": ["faithful", "discrepancies"],
}


# --- Prompts ----------------------------------------------------------------

_TYPE_GUIDANCE = (
    f"Allowed types (choose exactly one): {QUESTION_TYPE_VALUES}\n"
    "  - 'SELECT ALL THAT APPLY' / 'CHECK ALL' / 'multiple' -> multi\n"
    "  - 'SELECT ONE' / a plain single-choice list -> single\n"
    "  - a table/grid of statements rated on a scale -> grid_single "
    "(put statements in rows, scale points in cols)\n"
    "  - numeric entry (age, count, amount) -> numeric\n"
    "  - free text / open-end -> open_text\n"
    "  - a long list intended as a dropdown -> dropdown\n"
    "  - ranking task -> rank\n"
    "  - display/instructional text that is not a question -> info"
)

_RULES = (
    """CRITICAL RULES (follow exactly — this is the whole point):
1. Never invent an option, code, qid, or wording that is not present in the source text.
   If a code number isn't given, number options 1,2,3... in the order they appear.
2. Interviewer/programmer instructions ("INTERVIEWER: READ LIST", "DO NOT READ",
   "PROGRAMMER: RANDOMIZE") are NOT answer options. Put them in `instructions`, never `options`.
3. Routing: copy the exact routing sentence into routing.verbatim_text, word for word.
   - Fill routing.show_if / routing.terminate_if ONLY when the source explicitly states which
     prior question AND which answer gates this one (e.g. "ASK IF Q3 = YES (code 1)").
   - If the source only says something like "ASK IF AWARE OF BRAND" WITHOUT naming the prior
     qid/answer that established awareness, DO NOT guess the qid. Leave show_if/terminate_if
     null and set routing.ambiguous = true. Preserve the verbatim sentence regardless.
   - "TERMINATE IF ..." / "SCREEN OUT IF ..." -> terminate_if. "ASK IF ..." / "SHOW IF ..." -> """
    """show_if.
4. Set confidence honestly per field. Ambiguous routing means routing confidence should be low.
"""
)


def build_extraction_prompt(chunk_text: str, candidate_qid: str | None) -> str:
    """Prompt for the single-chunk extraction pass."""
    hint = (
        f"The document segmenter tagged this chunk's question id as: {candidate_qid}. "
        "Use it unless the text clearly shows a different id.\n\n"
        if candidate_qid
        else "The segmenter could not detect a question id for this chunk.\n\n"
    )
    return (
        "You extract ONE market-research survey question from the text chunk below into "
        "structured JSON. The chunk was segmented from a real questionnaire document.\n\n"
        f"{_TYPE_GUIDANCE}\n\n"
        f"{_RULES}\n"
        f"{hint}"
        "----- SOURCE CHUNK -----\n"
        f"{chunk_text}\n"
        "----- END CHUNK -----\n"
    )


def build_verification_prompt(chunk_text: str, extracted: dict) -> str:
    """Prompt for the grounded second-pass verification.

    `extracted` is the first-pass Question as a dict (its `to_dict()` form is
    fine). We show the model the ORIGINAL text and the structured result and ask
    it to hunt for discrepancies — the standard extract-then-verify mitigation.
    """
    return (
        "You are auditing an automated extraction of ONE survey question. Compare the "
        "ORIGINAL source text against the STRUCTURED extraction and decide if the extraction "
        "is faithful.\n\n"
        "Flag a discrepancy for ANY of:\n"
        "  - an option/code/label that is NOT in the source (invented content)\n"
        "  - a wrong question type (e.g. multi labelled single)\n"
        "  - routing present in the source but missing/wrong in the extraction\n"
        "  - a wrong or invented qid\n"
        "  - an answer option in the source that the extraction dropped\n"
        "Interviewer instructions living in `instructions` instead of `options` is CORRECT, "
        "not a discrepancy.\n\n"
        "----- ORIGINAL SOURCE -----\n"
        f"{chunk_text}\n"
        "----- STRUCTURED EXTRACTION -----\n"
        f"{json.dumps(extracted, ensure_ascii=False, indent=2)}\n"
        "----- END -----\n"
    )

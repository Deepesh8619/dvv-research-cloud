"""JSON schemas and prompts for questionnaire extraction + verification.

Two LLM shapes live here, both consumed through `src.ai.router.run_task`:

1. EXTRACTION_SCHEMA / build_extraction_prompt — "one text chunk in, one
   structured Question out". Sent to cloud models with `tool_choice` forcing a
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

from src.ai.known_failures import block_for
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
# Document-level (NOT per-question): the optional LLM reconciliation pass that
# hunts for questions the regex segmenter missed could not run (backend down /
# no route / bad output). Regex segmentation still stands, so extraction proceeds
# on the original chunks — this flag just records that the safety net was skipped.
FLAG_RECONCILIATION_UNAVAILABLE = "RECONCILIATION_UNAVAILABLE"
# The study's instructions state a rule about this question (study_understanding).
# Not blocking: it keeps the question out of auto-approval so an analyst checks
# it against the rule, quoted in validation["brief_rules"].
FLAG_BRIEF_RULE = "BRIEF_RULE"
# A hidden/derived variable (qid prefixed HID_): a programmer-computed flag
# derived from other answers, NOT a respondent-facing question. The platform has
# no derived-variable engine, so the honest handling is detect + label (force
# type=info + this flag) so a reviewer knows it needs manual setup — never a
# mis-extracted, unanswerable "question".
FLAG_DERIVED_VARIABLE = "DERIVED_VARIABLE"
# Read as a hidden variable on WORDING alone — an all-caps "CODE AGE" with no
# HID_ prefix and no named source. The reading is usually right and occasionally
# it is a real question a scripter shouted, and the difference decides whether
# anybody is asked it. So the reviewer is asked to confirm rather than told.
FLAG_DERIVED_VARIABLE_UNCONFIRMED = "DERIVED_VARIABLE_UNCONFIRMED"
# The answer list was copied from an earlier question the wording points at
# ("each of these brands") but does not name. Usually right; the list decides
# the data columns, so a reviewer confirms which one.
FLAG_LIST_INHERITED = "LIST_INHERITED"
# A MaxDiff or conjoint block: an experimental design, not a question. The
# platform has no design generator and no utility model, so fielding it would
# show every respondent the same static screen and export a file the analyst
# cannot model. Blocking, and cleared only by a reviewer who has decided to
# script the exercise elsewhere.
FLAG_TRADE_OFF_UNSUPPORTED = "TRADE_OFF_UNSUPPORTED"


# --- Extraction schema ------------------------------------------------------

# Note on shape: `run_task`'s `_validate` only checks top-level required keys;
# Cloud tool_choice enforces the full nested schema; the local path only
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
    "  - display/instructional text that is not a question -> info\n"
    # The house vocabulary, taken from an inventory of every bracketed
    # directive across the three real client questionnaires. These are the
    # exact strings scripters write; recognising them is not inference.
    "Scripter shorthand seen in these documents:\n"
    "  - 'TEXTBOX', 'OPEN END', 'OE: n-CHARACTER MINIMUM', a written blank "
    "'_ _ _ _' -> open_text\n"
    "  - 'NUMERIC OE', 'RECORD NUMBER n TO n', 'ENTER NUMBER n-n' -> numeric "
    "(note 'NUMERIC OE' is a number, not an open end)\n"
    "  - a scale row written '0=Not Important | 1 | ... | 10=Very Important', "
    "or 'IN ROWS' / 'RANDOMIZE STATEMENTS' -> grid_single; the listed items "
    "are the ROWS, not the answer options\n"
    "  - 'RANK n,n,n' or 'rank your top three' -> rank\n"
    "  - a superlative ('best describes', 'which one') or a Yes/No pair -> "
    "single, whatever the type cell says\n"
    "  - an all-caps 'CODE ... BASED ON ...' wording -> info: this is computed "
    "by the scripter from other answers and is never asked"
)

# Directives that carry meaning beyond the type. Each appears verbatim in the
# client documents; the deterministic parsers handle them, and the model needs
# the same reading for the chunks that still reach it.
_DIRECTIVE_GUIDANCE = (
    "DIRECTIVE SHORTHAND:\n"
    "  - 'RANDOMIZE' and 'ROTATE' both mean randomize=true; 'KEEP ORDER' and "
    "'DO NOT RANDOMIZE' mean false.\n"
    "  - '[ANCHOR]' pins an option to the end of the list; '[EXCLUSIVE]' means "
    "selecting it clears the others; '[TERMINATE]' on an option screens the "
    "respondent out when that option is chosen.\n"
    "  - '[SHOW Q26 LIST]' or 'INSERT RESPONSES FROM Q4' means the options come "
    "from another question. Do NOT invent them — leave options empty and record "
    "the reference in instructions."
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


# GBK/Google-style "programming spec" questions are authored as a labelled table
# that the loader serializes into `Label: value` lines. When those labels are
# present the model must map each row to the right structured field — teaching
# that mapping is what makes routing/terminate/validation extraction accurate
# instead of the model guessing what "Among who" / "Other details" mean. Only
# appended when the convention is detected, so ordinary prose chunks aren't
# burdened with it.
_FIELD_SPEC_GUIDANCE = (
    """PROGRAMMING-SPEC TABLE: this chunk uses labelled rows (`Q Name:`, `Response list:`,
`Among who:`, `Other details:`, ...). Map each label to the right field:
  - `Q Name:` -> the qid (trust the segmenter hint).
  - `Response list:` and the `code: label` lines under it -> options.
  - `Among who:` -> the SHOW condition (routing.show_if).
      * "(blank)" means asked of EVERYONE: no show_if, routing.verbatim_text = "".
      * A simple "VAR = value" -> {"op":"eq","qid":"VAR","value":N}.
      * Multi-branch / bracketed / heavy "and/or" logic: DO NOT guess a structure.
        Set routing.ambiguous = true and copy the FULL text into routing.verbatim_text.
  - `Other details:` can hold SEVERAL different things — read carefully:
      * "TERMINATE if ..." -> routing.terminate_if (patterns below).
      * A numeric entry range like "Allow 0-999 to be entered" is INPUT VALIDATION,
        NOT routing — never put it in show_if/terminate_if; record it in `instructions`.
      * "Make N exclusive" -> note in `instructions` (option code N is exclusive).
      * A fielding quota ("Quota of n=100 ...") is NOT a question field -> `instructions`.
  - `Randomize:` with real (non-blank) content -> the options are randomized.
  - `Q type:` with real content (e.g. "Multi-select up to 3") is a type/behaviour hint.

TERMINATE patterns in `Other details:` — the qid is THIS question's own qid unless
another is explicitly named. Map with existing ops only:
  - "TERMINATE if 3-99 selected" (contiguous code range) ->
      {"op":"and","children":[{"op":"gt","qid":Q,"value":2},{"op":"lt","qid":Q,"value":100}]}
      (value > low-1 AND value < high+1). If the codes are enumerated, not a true
      range, use {"op":"in","qid":Q,"values":[...]} instead.
  - "TERMINATE if 5,6 selected" (specific codes) -> {"op":"in","qid":Q,"values":[5,6]}.
  - "TERMINATE if 1 not selected" (negation) ->
      {"op":"not","children":[{"op":"selected","qid":Q,"value":1}]}.
  - COMPOUND "OR" mixing a numeric comparison, a placeholder, and a selected code,
    e.g. "TERMINATE if age<25 OR age>?? OR if 99 selected": the "age>??" branch has a
    missing value, so the FULL OR cannot be faithfully captured. Do NOT emit a partial
    terminate_if holding only the confident "99 selected" branch — a partial OR silently
    UNDER-terminates (age<25 respondents would slip through), which is worse than leaving
    the whole thing for the human. Set terminate_if = null, routing.ambiguous = true, and
    copy the ENTIRE sentence into routing.verbatim_text.
  - If a condition references a value that is MISSING, blank, or a placeholder like
    "??" in the source, DO NOT invent a number. Extract only the sub-conditions you
    ARE confident about, set routing.ambiguous = true, and keep the full sentence
    verbatim in routing.verbatim_text.
"""
)


# Appended when the whole-document profile detector could not confidently
# recognize the format (profile 'unknown'). It does NOT change the extraction
# rules — it only tells the model to stay literal and flag rather than guess, so
# an unrecognized document is handled no worse than today, just more cautiously.
_UNKNOWN_FORMAT_GUIDANCE = (
    "UNRECOGNIZED FORMAT: the document's authoring convention was not confidently "
    "identified. Extract conservatively — copy wording verbatim, do NOT infer structure "
    "or routing that the text does not state explicitly, and prefer routing.ambiguous=true "
    "over guessing.\n"
)


def _has_field_spec(text: str) -> bool:
    """True when a chunk carries the programming-spec label convention."""
    low = text.lower()
    return "q name:" in low or "among who:" in low or "other details:" in low


def build_learned_rules_block(learned_rules: list[str] | None) -> str:
    """Render founder-taught extraction rules as a prompt block.

    Empty/None -> empty string, so a profile nobody has corrected yet produces a
    prompt byte-identical to the pre-correction-loop one. That no-op property is
    what lets the correction loop ship without putting existing extraction at
    risk, and it is asserted directly in the tests.

    Rules are numbered in their stored order because order carries meaning: a
    later rule is allowed to override an earlier one, and the model is told so.
    """
    rules = [r.strip() for r in (learned_rules or []) if r and r.strip()]
    if not rules:
        return ""
    body = "\n".join(f"{i}. {r}" for i, r in enumerate(rules, 1))
    return (
        "----- CLIENT-SPECIFIC RULES -----\n"
        "Our researchers taught us how THIS client writes questionnaires. These "
        "rules describe the actual document convention and override the general "
        "guidance above where they conflict. A later rule wins over an earlier "
        "one.\n"
        f"{body}\n"
        "----- END CLIENT-SPECIFIC RULES -----\n"
    )


def build_extraction_prompt(
    chunk_text: str,
    candidate_qid: str | None,
    profile_hint: str | None = None,
    learned_rules: list[str] | None = None,
    study_context: str | None = None,
) -> str:
    """Prompt for the single-chunk extraction pass.

    `profile_hint` comes from the document's detected format profile
    (profiles.QuestionnaireProfile.prompt_hint) and selects the RIGHT guidance
    block: 'field_spec' appends the GBK spec-table mapping, 'conservative'
    appends the unknown-format note, 'prose'/None append neither. The per-chunk
    `_has_field_spec` check still fires independently (belt and suspenders) so a
    stray spec-table chunk is handled even if whole-doc detection guessed wrong.

    `learned_rules` are the founder-approved rules stored against that profile
    (see api.db.active_rules_for_profile). The hint SELECTS a canned block; the
    rules CARRY taught content — that split is the whole widening the correction
    loop needed. An empty rule list changes nothing about the prompt.
    """
    want_field_spec = profile_hint == "field_spec" or _has_field_spec(chunk_text)
    spec = f"{_FIELD_SPEC_GUIDANCE}\n" if want_field_spec else ""
    if profile_hint == "conservative":
        spec = f"{_UNKNOWN_FORMAT_GUIDANCE}\n{spec}"
    spec = f"{spec}{build_learned_rules_block(learned_rules)}"
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
        f"{_DIRECTIVE_GUIDANCE}\n\n"
        f"{_RULES}\n"
        f"{block_for('extract')}\n"
        f"{spec}"
        f"{_study_block(study_context)}"
        f"{hint}"
        "----- SOURCE CHUNK -----\n"
        f"{chunk_text}\n"
        "----- END CHUNK -----\n"
    )


def _study_block(study_context: str | None) -> str:
    """What the study's instructions say that bears on this question."""
    if not study_context:
        return ""
    return (
        "The study's instructions, as an analyst read them before this question "
        "(use them to understand the question; never copy them into it):\n"
        f"{study_context}\n\n"
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


# --- Segmentation reconciliation (LLM safety net over the regex segmenter) ---

# Third LLM shape: run once over the WHOLE document AFTER the free regex
# segmenter, to catch questions the regex could not anticipate. Every finding is
# anchored to a numbered BLOCK INDEX (position in the DocumentBlock list), never
# a free-text snippet — an index is exact where a paraphrased snippet is fragile
# on merged-cell tables. The model is told which qids/ranges the regex ALREADY
# found so it only reports what was MISSED. Consumed by
# `src.engines.questionnaire.reconciler.reconcile_segmentation`.
RECONCILE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "missed_questions": {
            "type": "array",
            "description": "One entry per question the regex segmenter MISSED. Empty if none.",
            "items": {
                "type": "object",
                "properties": {
                    "qid": {
                        "type": ["string", "null"],
                        "description": "A question id if the source shows one; null (with a "
                        "note to review) if there is no visible label. Never guess a wrong id.",
                    },
                    "start_block_index": {
                        "type": "integer",
                        "description": "Index of the block where this missed question STARTS.",
                    },
                    "note_block_indices": {
                        "type": "array",
                        "description": "Indices of any note/instruction blocks immediately BEFORE "
                        "start_block_index that belong WITH this question, not the preceding one.",
                        "items": {"type": "integer"},
                    },
                },
                "required": ["start_block_index"],
            },
        }
    },
    "required": ["missed_questions"],
}


def build_reconcile_prompt(numbered_blocks: str, known_ranges: str) -> str:
    """Prompt for the whole-document segmentation reconciliation pass.

    `numbered_blocks` is the document window with each block prefixed by its
    zero-padded global index (e.g. `[003] Q Name | RESP_AGE ...`). `known_ranges`
    lists the qids + block ranges the regex segmenter already found so the model
    can skip them and report only what was missed.
    """
    return (
        "A market-research questionnaire was split into questions by a fast REGEX "
        "segmenter. Regex misses formats it wasn't built for. Your ONLY job is to find "
        "questions the regex MISSED and anchor each to a block index.\n\n"
        "Every block below is prefixed with its index like [003]. Reference blocks by that "
        "index ONLY — never by quoting their text.\n\n"
        "The regex ALREADY found these questions (qid: block start-end) — do NOT re-report "
        "any of them:\n"
        f"{known_ranges}\n\n"
        "Report a MISSED question only when it is genuinely a distinct question the ranges "
        "above did not capture. Specifically look for:\n"
        "  (a) a question embedded in a prose paragraph with no table and no label at all;\n"
        "  (b) a novel label convention that is neither 'Q5.'-style nor 'Q Name |'-style;\n"
        "  (c) a scripter/programmer note sitting on its own that logically belongs to a "
        "nearby question.\n\n"
        "Rules:\n"
        "  - Do NOT invent a question that isn't really there.\n"
        "  - Prefer qid=null with a 'no visible label, review' intent over guessing a wrong id.\n"
        "  - note_block_indices are only the note blocks immediately BEFORE start_block_index "
        "that belong WITH the missed question.\n"
        "  - Return an EMPTY list if the regex caught everything (the common case).\n\n"
        "----- NUMBERED BLOCKS -----\n"
        f"{numbered_blocks}\n"
        "----- END BLOCKS -----\n"
    )


# --- Residue extraction: ask ONLY about what the table did not state ---------

# Full extraction sends ~6,100 tokens per question — question text, every
# guidance block, and a ten-field schema — even when the table already states
# the id, the wording, the option list and the screen-out. When a structured
# parse has answered most of it, asking about the remainder costs a fraction of
# that: measured across a real 74-question document, 451,400 tokens -> 14,264.
#
# One field per entry: the JSON-schema fragment, and the sentence that tells the
# model what is actually being asked. Keeping them together stops the two
# drifting apart.
_RESIDUE_FIELDS: dict[str, tuple[dict, str]] = {
    "type": (
        {
            "type": "string",
            "enum": [
                "single",
                "multi",
                "open_text",
                "numeric",
                "grid_single",
                "rank",
                "info",
            ],
            "description": "The answer format this question collects",
        },
        # Conventions inventoried from every bracketed directive across the
        # three real client questionnaires. The parser resolves these on its
        # own; they are repeated here because the questions that still reach a
        # model are the ones where the signal is prose rather than a directive,
        # and the model was previously guessing against no house style at all.
        "type: the answer format.\n"
        "  single — exactly one option; superlatives ('best describes', "
        "'which one') and yes/no pairs are always single.\n"
        "  multi — several options; 'select all that apply' or an exclusive "
        "'None of these' code indicate multi.\n"
        "  grid_single — the options are ROWS rated on a shared scale; look for "
        "'each of the following', 'IN ROWS', 'RANDOMIZE STATEMENTS', or a "
        "scale written as '0=Low | 1 | ... | 10=High'.\n"
        "  rank — 'rank your top three', 'RANK 1,2,3'.\n"
        "  numeric — 'ENTER NUMBER', 'RECORD NUMBER n TO n', 'NUMERIC OE'.\n"
        "  open_text — 'OPEN END', 'TEXTBOX', 'OE: n-CHARACTER MINIMUM', or a "
        "written blank '_ _ _ _'.\n"
        "  info — not asked: display text, or a scripter-computed variable "
        "(an all-caps 'CODE ... BASED ON ...' wording).",
    ),
    "text": (
        {"type": "string", "description": "The question as asked to a respondent"},
        "text: the wording a respondent reads, without question ids or "
        "scripter notes.",
    ),
    "base": (
        {
            "type": "object",
            "description": "Who is asked this question, as a condition",
            "properties": {
                "verbatim_text": {"type": "string"},
                "ambiguous": {"type": "boolean"},
                "show_if": {"type": ["object", "null"]},
            },
            "required": ["verbatim_text", "ambiguous"],
        },
        "base: turn the base/'among who' sentence into a condition on an earlier "
        "question. Set ambiguous=true and show_if=null if it cannot be resolved "
        "to a specific question and codes.",
    ),
    "terminate": (
        {
            "type": "object",
            "description": "The screen-out condition",
            "properties": {
                "verbatim_text": {"type": "string"},
                "ambiguous": {"type": "boolean"},
                "terminate_if": {"type": ["object", "null"]},
            },
            "required": ["verbatim_text", "ambiguous"],
        },
        "terminate: turn the screen-out sentence into a condition. Set "
        "ambiguous=true and terminate_if=null rather than guessing which codes "
        "it means.",
    ),
}


def residue_schema(missing: list[str]) -> dict:
    """A JSON schema covering ONLY the fields a structured parse could not fill."""
    wanted = [f for f in missing if f in _RESIDUE_FIELDS]
    return {
        "type": "object",
        "properties": {f: _RESIDUE_FIELDS[f][0] for f in wanted},
        "required": wanted,
    }


def build_residue_prompt(
    *,
    qid: str,
    missing: list[str],
    text: str | None = None,
    options: list[tuple[int, str]] | None = None,
    base: str | None = None,
    notes: str | None = None,
    study_context: str | None = None,
) -> str:
    """Ask about the residue only, giving just enough context to answer it.

    Deliberately omits the guidance blocks the full prompt carries: those exist
    to teach a model how to read a whole questionnaire chunk, and here the
    reading has already been done deterministically.
    """
    wanted = [f for f in missing if f in _RESIDUE_FIELDS]
    known = [f"Question id: {qid}"]
    if text:
        known.append(f"Question text: {text}")
    if options:
        shown = ", ".join(f"{code}={label}" for code, label in options[:20])
        known.append(f"Answer options: {shown}")
    if base:
        known.append(f"Base / among who: {base}")
    if notes:
        known.append(f"Scripter notes: {notes}")
    if study_context:
        known.append(f"From the study's instructions:\n{study_context}")

    asks = "\n".join(f"- {_RESIDUE_FIELDS[f][1]}" for f in wanted)
    return (
        "A market-research question has already been read from a structured "
        "spec table. Everything below is known and correct — do NOT restate or "
        "second-guess it.\n\n"
        + "\n".join(known)
        + "\n\nDetermine ONLY these remaining fields:\n"
        + asks
        + "\n"
        + block_for("residue", "Avoid these known mistakes:")
    )

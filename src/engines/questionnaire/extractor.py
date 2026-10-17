"""LLM questionnaire extraction: chunks -> structured SurveyDefinition.

This is the second (LLM) stage of questionnaire ingestion. `questionnaire_loader`
turns a .docx/PDF into deterministic `QuestionChunk`s; this module turns each
chunk into a structured `Question` via a three-step pipeline:

    1. extract   — one schema-constrained LLM call per chunk (open-weight model via router)
    2. validate  — deterministic, no LLM: duplicate codes, empty options,
                   self-referential / ambiguous routing
    3. verify    — a second grounded LLM call that audits the extraction against
                   the source text for hallucinated / dropped content

Every step is failure-tolerant: a backend outage on one chunk flags that chunk
and lets the rest of the document proceed, because a partial draft a human can
fix beats a hard crash on question 40 of 60.

Cross-chunk routing resolution (an ``ASK IF Q3=1`` on Q7 needs Q3 to exist) can
only run once *every* chunk is extracted, so it lives in
`extract_survey_definition`, not `extract_question` — a single chunk has no view
of the whole document's qid set. See the note in `_validate_question`.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from src.ai.router import (
    AIResponse,
    BackendUnavailable,
    ModelOutputError,
    run_task,
)
from src.core.models import DataMap
from src.core.survey_definition import (
    Block,
    Expression,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
)
from src.engines.questionnaire import schema
from src.engines.questionnaire.block_notes import apply as apply_block_notes
from src.engines.questionnaire.carousel import apply as apply_carousels
from src.engines.questionnaire.carousel import capture as capture_carousel
from src.engines.questionnaire.constant_sum import apply as apply_constant_sums
from src.engines.questionnaire.constant_sum import make_constant_sum
from src.engines.questionnaire.constant_sum import read as read_constant_sum
from src.engines.questionnaire.datamap_recovery import (
    DatamapQuestion,
    fill_from_datamap,
    index_by_qid,
    non_answerable_qids,
    normalize_qid,
    recover_missing,
)
from src.engines.questionnaire.decoding_report import decoding_report
from src.engines.questionnaire.extraction_diagnosis import document_markers
from src.engines.questionnaire.flag_reasons import mark_derived_facts
from src.engines.questionnaire.grid_subquestions import split_subquestion_grids
from src.engines.questionnaire.learned import LearnedPattern, learned_hidden_variable
from src.engines.questionnaire.list_sources import fill_lists, points_at_a_list
from src.engines.questionnaire.logic_review import review as review_logic
from src.engines.questionnaire.loop_bases import (
    resolve_answer_bases_from_hidden,
    resolve_loop_bases_from_hidden,
)
from src.engines.questionnaire.loops import expand_loops
from src.engines.questionnaire.mr_techniques import Mask, detect_techniques
from src.engines.questionnaire.per_item import apply as apply_per_item_grids
from src.engines.questionnaire.profiles import detect_profile
from src.engines.questionnaire.prose_directives import (
    ProseFields,
    is_derived_wording,
    order_terminal_options,
    parse_prose_question,
)
from src.engines.questionnaire.qa_loop import run_qa
from src.engines.questionnaire.reconciler import reconcile_segmentation
from src.engines.questionnaire.relative_bases import (
    resolve_counted_bases,
    resolve_relative_bases,
)
from src.engines.questionnaire.routing_prose import (
    apply_choice_bands,
    apply_placeholder_pipes,
    apply_skips,
    hoist_loose_routing,
)
from src.engines.questionnaire.section_paths import (
    apply_intro_screens,
    apply_section_bases,
    apply_section_paths,
    apply_worded_asks,
)
from src.engines.questionnaire.spec_table import SpecTableFields, parse_spec_table
from src.engines.questionnaire.study_overview import parse_overview
from src.engines.questionnaire.study_understanding import (
    context_for,
    governing_rules,
    understand_study,
)
from src.engines.questionnaire.swallowed_options import repair_swallowed_options
from src.engines.questionnaire.table_questions import (
    entry_table,
    labelled_sections,
    question_from_tables,
)
from src.engines.questionnaire.trade_off import apply as flag_trade_offs
from src.engines.questionnaire.verification import (
    covers_the_question,
    failed,
    verify_against_source,
)
from src.engines.questionnaire.write_ins import mark_write_ins
from src.intake.questionnaire_loader import DocumentBlock, QuestionChunk

# Question types whose whole point is a fixed answer list; an empty option list
# on one of these is almost certainly a dropped/failed extraction, not a design.
_OPTION_BEARING_TYPES = frozenset(
    {
        QuestionType.SINGLE,
        QuestionType.MULTI,
        QuestionType.DROPDOWN,
        QuestionType.GRID_SINGLE,
    }
)

# Below this, per-field routing confidence is treated as ambiguous even if the
# model didn't set routing.ambiguous itself.
_ROUTING_CONFIDENCE_FLOOR = 0.5

# Where we stash the verbatim routing sentence so it survives even when a
# structured show_if/terminate_if is later nulled out as unresolvable. The
# schema's whole design is "preserve verbatim_text regardless" — Question has no
# dedicated field for it, so it lives in the (otherwise-unused-at-extraction)
# `validation` metadata dict rather than being lost.
_ROUTING_VERBATIM_KEY = "routing_verbatim"

# Where per-chunk failure diagnostics are stashed on Question.validation, mirroring
# the routing_verbatim pattern above. `extraction_error` for a failed extract
# pass, `verification_error` for a failed verify pass — each is
# {"error_type": <exc class name>, "error_detail": <str(exc)>}. Preserved so a
# reviewer (and the T8 navigator) sees the real root cause instead of an opaque
# flag; str(BackendUnavailable("GROQ_API_KEY not set")) names it exactly.
_EXTRACTION_ERROR_KEY = "extraction_error"
_VERIFICATION_ERROR_KEY = "verification_error"

# The circuit breaker aborts the whole run once this many of the first processed
# chunks have ALL failed with BackendUnavailable — an N/N failure is one root
# cause (a missing key / dead backend), not N review items, and shouldn't cost
# 2*N doomed LLM attempts to discover.
_CIRCUIT_BREAKER_CHUNKS = 3

#: Set once the circuit breaker has tripped: the rest of the document is then read
#: by the deterministic parsers alone, and no further model call is attempted.
_MODEL_OFF: ContextVar[bool] = ContextVar("dvv_extract_model_off", default=False)


def _ask_model(*args: Any, **kwargs: Any) -> AIResponse:
    """`run_task`, unless the breaker has already shown the model unreachable."""
    if _MODEL_OFF.get():
        raise BackendUnavailable("model calls stopped for this run (circuit breaker)")
    return run_task(*args, **kwargs)

# Exception-based review (product decision): a question is auto-approved
# (pre-cleared so a human only reviews exceptions) ONLY when the pipeline is
# fully confident in it — the grounded verify pass ran AND agreed, it carries
# ZERO flags, and the LOWEST per-field confidence the model reported is at or
# above this floor. Anything short of all three stays unapproved for human eyes.
AUTO_APPROVE_CONFIDENCE_FLOOR = 0.85

# Skip the grounded verify pass when the extraction is already unambiguous.
#
# Verify is a SECOND LLM call per question, so it doubles both cost and — on a
# token-capped tier — wall time. Measured on Groq's free tier (8k tokens/min):
# ~6.1k tokens per call, two calls per question, so a 75-question document
# spends ~38 minutes almost entirely waiting on the rate limit.
#
# The floor here is deliberately HIGHER than the auto-approve floor. Verify
# exists to catch hallucination, so skipping it is only defensible where the
# model was maximally confident on EVERY field and nothing was flagged — and
# even then a skipped question is NOT auto-approved (see `_is_auto_approvable`,
# which still requires `verified`), so a human reviews it. The trade is
# "unverified but human-reviewed" against "verified but 2x slower", never
# "unverified and auto-approved".
#
# Set QRE_VERIFY_ALWAYS=1 to force verification on every question.
SKIP_VERIFY_CONFIDENCE_FLOOR = 0.95


def _needs_verification(result: ExtractionResult) -> bool:
    """False only when extraction was flawless and maximally confident."""
    if os.environ.get("QRE_VERIFY_ALWAYS", "").strip().lower() in ("1", "true", "yes"):
        return True
    if result.flags:
        return True
    confidences = result.confidence.values()
    if not confidences:
        return True
    return min(confidences) < SKIP_VERIFY_CONFIDENCE_FLOOR


def _is_auto_approvable(result: ExtractionResult) -> bool:
    """True if a question is clean enough to pre-approve (see the floor above)."""
    if not result.verified or result.flags:
        return False
    confidences = result.confidence.values()
    lowest = min(confidences) if confidences else 1.0
    return lowest >= AUTO_APPROVE_CONFIDENCE_FLOOR


@dataclass
class ExtractionResult:
    """One extracted `Question` plus the metadata a reviewer/linker needs.

    `extract_question` returns this rather than a bare `Question` because the
    downstream review UI and the question<->variable linker need the raw
    per-field confidence and the accumulated flags alongside the structured
    result to decide what to trust and what to surface for human check.
    """

    question: Question
    confidence: dict[str, float] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)
    verified: bool = False  # True once the verification pass ran successfully

    def add_flag(self, flag: str) -> None:
        """Add a flag once (idempotent) and mirror it onto the Question."""
        if flag not in self.flags:
            self.flags.append(flag)
        if flag not in self.question.flags:
            self.question.flags.append(flag)


def _empty_question(chunk: QuestionChunk) -> Question:
    """A minimal placeholder Question for a chunk whose extraction failed.

    Preserves the source text/location so a human can extract it by hand; typed
    `info` (display content) so it never masquerades as an answerable question
    with a bogus empty option list.
    """
    return Question(
        qid=chunk.candidate_qid or "UNKNOWN",
        text=chunk.chunk_text[:200],
        type=QuestionType.INFO,
        options=[],
        required=False,
        source_text=chunk.chunk_text,
        source_location=chunk.provenance,
    )


def _parse_expression(raw: Any, verbatim: str | None) -> Expression | None:
    """Deserialize a raw routing dict into an Expression, or None.

    Uses the model's own `Expression.from_dict` (never a hand-rolled parser).
    Attaches the verbatim routing sentence when the expression itself carries
    none, so the structured form always keeps a line back to the source prose.
    """
    if not isinstance(raw, dict) or not raw.get("op"):
        return None
    expr = Expression.from_dict(raw)
    if expr.verbatim_text is None and verbatim:
        expr.verbatim_text = verbatim
    return expr


def _options_from(raw: Any) -> list[Option]:
    out: list[Option] = []
    for item in raw or []:
        if not isinstance(item, dict) or "code" not in item or "label" not in item:
            continue
        out.append(Option(code=int(item["code"]), label=str(item["label"])))
    return out


def _question_from_raw(content: dict, chunk: QuestionChunk) -> tuple[Question, dict]:
    """Build a Question from a schema-valid extraction dict.

    Returns (question, routing_dict) so the caller can inspect the raw routing
    block (ambiguous flag, verbatim) during validation without re-reading it.
    """
    # A weaker model sometimes emits `routing` as a bare STRING (the routing
    # sentence) instead of the object the schema asks for. Treat that string as
    # the verbatim rather than discarding it: it is exactly the sentence a
    # reviewer needs to resolve the routing by hand, and crashing here would
    # break extract_question's contract of never raising on model output.
    raw_routing = content.get("routing")
    if isinstance(raw_routing, str):
        routing: dict = {"verbatim_text": raw_routing, "ambiguous": True}
    elif isinstance(raw_routing, dict):
        routing = raw_routing
    else:
        routing = {}
    verbatim = (routing.get("verbatim_text") or "").strip() or None

    try:
        qtype = QuestionType(content["type"])
    except (ValueError, KeyError):
        # Model returned a type outside the enum — keep the content, don't crash;
        # default to single and let validation/verify surface the mismatch.
        qtype = QuestionType.SINGLE

    rows = _options_from(content.get("rows"))
    cols = _options_from(content.get("cols"))

    validation: dict[str, Any] = {}
    if verbatim:
        validation[_ROUTING_VERBATIM_KEY] = verbatim

    question = Question(
        qid=str(content.get("qid") or chunk.candidate_qid or "UNKNOWN"),
        text=str(content.get("text", "")),
        type=qtype,
        options=_options_from(content.get("options")),
        rows=rows or None,
        cols=cols or None,
        required=bool(content.get("required", True)),
        show_if=_parse_expression(routing.get("show_if"), verbatim),
        terminate_if=_parse_expression(routing.get("terminate_if"), verbatim),
        validation=validation or None,
        source_text=chunk.chunk_text,
        source_location=chunk.provenance,
        extraction_confidence=dict(content.get("confidence") or {}),
        extraction_method="llm",
    )
    return question, routing


def _flag_derived_variable(
    result: ExtractionResult,
    chunk: QuestionChunk,
    patterns: list[LearnedPattern] | None = None,
) -> None:
    """Detect a hidden/derived variable and label it honestly (no derivation).

    A ``HID_*`` qid is a programmer-computed flag derived from OTHER answers, not
    a respondent-facing question — its derivation logic usually isn't even in the
    document. The platform has no derived-variable engine, so the honest fix is
    detection + labelling, not derivation: force ``type=info`` and flag
    ``DERIVED_VARIABLE`` so the review UI says "needs manual setup" instead of
    surfacing a mis-extracted, unanswerable question. Run BEFORE validation so
    the info type suppresses the (irrelevant) empty-options check.

    Three signals: the ``HID_`` qid prefix (GBK), a founder-taught
    ``hidden_variable`` phrase ("SAMPLE SOURCE", "AUDIENCE CODE" — exact,
    never a guess), and an all-caps ``CODE ...`` wording addressed to the
    scripter (Bubble, Vivo — "C1. CODE AGE - BASED ON RESPONSES AT S1...").
    The last two live in prose_directives so both parsers apply the same rule.
    """
    qid = chunk.candidate_qid or result.question.qid or ""
    text = result.question.text or ""
    named_source = re.search(r"\bBASED\s+ON\b|\bFROM\b\s+[A-Z]{1,4}\d", text, re.I)
    by_prefix = qid.upper().startswith("HID_")
    taught = learned_hidden_variable(patterns, text)
    if by_prefix or taught or is_derived_wording(text):
        result.question.type = QuestionType.INFO
        result.add_flag(schema.FLAG_DERIVED_VARIABLE)
        result.question.extraction_evidence.setdefault(
            "derived",
            "the HID_ prefix" if by_prefix
            else (f"learned: {taught}" if taught
            else (f"{text[:40]} — it names its source" if named_source
                  else f"{text[:40]} — an all-caps instruction to the scripter")),
        )
        if not by_prefix and not taught and not named_source:
            # Wording alone. Usually right, occasionally a real question somebody
            # shouted, and the difference decides whether anybody is asked it.
            result.add_flag(schema.FLAG_DERIVED_VARIABLE_UNCONFIRMED)


def _validate_question(result: ExtractionResult, routing: dict) -> None:
    """Deterministic per-chunk validation. No LLM calls.

    Cross-chunk checks (does a referenced qid exist ANYWHERE in the document?)
    cannot run here — a chunk is extracted in isolation with no view of the full
    qid set — so `UNRESOLVED_ROUTING_QID` is deferred to
    `extract_survey_definition`. What we can catch per-chunk we catch here.
    """
    q = result.question

    # Duplicate option codes across options/rows/cols.
    for group in (q.options, q.rows or [], q.cols or []):
        codes: set[int] = set()
        for opt in group:
            if opt.code in codes:
                result.add_flag(schema.FLAG_DUPLICATE_OPTION_CODE)
                break
            codes.add(opt.code)

    # Empty options on a type that requires an answer list.
    if q.type is QuestionType.GRID_SINGLE:
        # A grid is judged on rows AND columns, never on `options`. It emits one
        # data column per ROW with the COLUMNS as that column's value labels, so
        # a grid holding a dozen options but no rows delivers nothing at all —
        # and used to pass this check on the strength of options it cannot use.
        if not (q.rows and q.cols):
            result.add_flag(schema.FLAG_EMPTY_OPTIONS)
    elif q.type in _OPTION_BEARING_TYPES and not q.options:
        result.add_flag(schema.FLAG_EMPTY_OPTIONS)

    # Self-referential show_if/terminate_if. A question cannot gate its own
    # display on its own (not-yet-given) answer, so a show_if/terminate_if that
    # references ONLY this qid is almost always an extraction error for these
    # two fields specifically. NOTE: survey_definition.validate() deliberately
    # *allows* self-reference (e.g. `terminate_if age < 18` on the age question)
    # because there a leaf op reads the answer just given on THIS screen. That's
    # a legitimate terminate pattern, so we do NOT hard-error self-reference; we
    # only flag it as ambiguous routing worth a human glance, and only when the
    # expression references nothing BUT itself (a mixed `Q7 and Q3` staying is
    # fine). This keeps us from fighting the model's own tested fixture while
    # still surfacing the far-more-common "gated its own display" mistake.
    # Only the SHOW condition: screening out on this question's own answer
    # ("TERMINATE if 5,6 selected") is the commonest screener rule there is, and
    # flagging it put an unexplained routing flag on every screener question.
    if q.show_if is not None and q.show_if.referenced_qids() == {q.qid}:
        result.add_flag(schema.FLAG_AMBIGUOUS_ROUTING)

    # Ambiguous routing from the model, or low routing confidence.
    routing_conf = float(result.confidence.get("routing", 1.0))
    if routing.get("ambiguous") or routing_conf < _ROUTING_CONFIDENCE_FLOOR:
        result.add_flag(schema.FLAG_AMBIGUOUS_ROUTING)


def _stash_error(question: Question, key: str, exc: Exception) -> None:
    """Record a failure's type + detail on Question.validation under `key`.

    Follows the routing_verbatim stash convention: Question has no dedicated field
    for a failure diagnostic, so it lives in the otherwise-unused-at-extraction
    `validation` metadata dict rather than being discarded (the exact bug this
    task fixes). str(exc) for a config fault literally names the root cause.
    """
    if question.validation is None:
        question.validation = {}
    question.validation[key] = {
        "error_type": type(exc).__name__,
        "error_detail": str(exc),
    }


def _extraction_flag_for(exc: Exception) -> str:
    """Pick the extraction flag that matches this failure's actionability.

    - BackendUnavailable with `config_error` (missing key) -> CONFIG_ERROR:
      one fix in one place. Detected via the structured attribute on
      BackendUnavailable, not by string-sniffing the message.
    - other BackendUnavailable (unreachable/timeout) -> UNAVAILABLE: transient.
    - ModelOutputError (schema-rejected output) -> MODEL_ERROR: a real
      document/extraction problem for this chunk.
    - KeyError (no route configured) -> CONFIG_ERROR: also a deploy-time
      misconfiguration a human fixes once.
    """
    if isinstance(exc, BackendUnavailable):
        return (
            schema.FLAG_EXTRACTION_CONFIG_ERROR
            if getattr(exc, "config_error", False)
            else schema.FLAG_EXTRACTION_UNAVAILABLE
        )
    if isinstance(exc, ModelOutputError):
        return schema.FLAG_EXTRACTION_MODEL_ERROR
    # KeyError: no route configured for the task — a config fault.
    return schema.FLAG_EXTRACTION_CONFIG_ERROR



def _spec_table_fields(
    chunk: QuestionChunk, patterns: list[LearnedPattern] | None = None
) -> SpecTableFields | None:
    """The structured parse of this chunk's table, if it is one."""
    for block in chunk.source_blocks or []:
        if getattr(block, "block_type", None) == "table":
            fields = parse_spec_table(block, patterns)
            if fields is not None:
                return fields
    return None


# A spec-table "Randomize:" cell is free text: "Yes", "Randomize", "Rotate
# rows", "(blank)", "No". Only an affirmative word turns it on — an unreadable
# value leaves it off and stays visible in `validation` for a reviewer, because
# randomising when the document did not ask is a data error, not a nicety.
_RANDOMIZE_YES = re.compile(r"\byes\b|\brandomi[sz]e\b|\brotate\b|\bscramble\b", re.I)
_RANDOMIZE_NO = re.compile(r"\bno\b|\bnone\b|\bkeep order\b|\bdo not\b|\(blank\)", re.I)


def _randomize_from_cell(stated: str | None) -> bool:
    text = (stated or "").strip()
    if not text or _RANDOMIZE_NO.search(text):
        return False
    return bool(_RANDOMIZE_YES.search(text))


#: "Show / HV: Hidden variable" — the spec table stating that this row is
#: computed rather than asked. Deliberately narrow: "Show respondent directly"
#: is the other value of that same cell and must never match.
_HIDDEN_VARIABLE = re.compile(r"\bhidden\b.{0,12}\bvariable\b|\bHV\b\s*$", re.I)


def _question_from_fields(fields: SpecTableFields, chunk: QuestionChunk) -> Question:
    """Build a Question from what the table itself stated."""
    validation: dict[str, Any] = {}
    if fields.terminate_verbatim:
        validation[_ROUTING_VERBATIM_KEY] = fields.terminate_verbatim
    if fields.extras:
        # An untaught client convention: surfaced for review, never dropped.
        validation["unrecognised_rows"] = fields.extras
    if fields.randomize:
        validation["randomize"] = fields.randomize
    if fields.numeric_range:
        # "Allow 0-999": the bounds the compiler turns into a validator.
        validation.update(fields.numeric_range)
    techniques = detect_techniques(fields.text or "", fields.notes or "", fields.show or "")
    # "Response list: [INSERT SELECTIONS FROM DA_GOALS]" — the whole answer
    # list is another question's selections. That IS a mask, and saying so
    # rather than inventing a second mechanism gets three things at once: the
    # pass that fills lists borrows DA_GOALS' items, the compiler narrows them
    # at run time to what this respondent actually chose, and the review UI
    # shows the link. It was being read and then dropped, so the question
    # reached a reviewer with no answers and nothing said about why.
    for source in fields.list_from:
        qid = (source.get("source_qid") or "").strip()
        if not qid or qid.upper() == fields.qid.upper():
            continue
        if any(m.source_qid.upper() == qid.upper() for m in techniques.masks):
            continue
        techniques.masks.append(
            Mask(source_qid=qid.upper(), mode="selected", verbatim=source.get("verbatim", ""))
        )
    if techniques:
        validation["techniques"] = techniques.to_dict()

    # The table SAYS so: "Show / HV: Hidden variable". A derived variable is
    # computed by the programmer from other answers and is never put to a
    # respondent, and the platform has no derivation engine — so the honest
    # handling is to label it, exactly as for a HID_ qid. This one is stated
    # outright and was still being read as a single-choice question with no
    # answers, which is how HIDDEN_TOPBENEFIT reached the reviewer looking
    # like a parser failure. A stated fact beats every wording heuristic.
    stated_hidden = _HIDDEN_VARIABLE.search(fields.show or "")
    flags = [schema.FLAG_AMBIGUOUS_ROUTING] if "terminate_gap" in fields.evidence else []
    evidence = dict(fields.evidence)
    if stated_hidden:
        flags.append(schema.FLAG_DERIVED_VARIABLE)
        evidence["derived"] = f"Show / HV: {(fields.show or '').strip()}"

    return Question(
        qid=fields.qid,
        text=fields.text or "",
        type=QuestionType.INFO if stated_hidden else (fields.qtype or QuestionType.SINGLE),
        options=order_terminal_options(list(fields.options)),
        # A grid's two lists, kept apart: the variable list is what is rated,
        # the response list is the scale. Flattened into `options` they collided
        # on code, and the grid emitted no data columns at all.
        rows=list(fields.rows) or None,
        cols=list(fields.cols) or None,
        required=_default_required(
            QuestionType.INFO if stated_hidden else fields.qtype
        ),
        randomize_options=_randomize_from_cell(fields.randomize),
        terminate_if=fields.terminate_if,
        source_text=chunk.chunk_text,
        source_location=chunk.provenance,
        validation=validation,
        extraction_method="spec_table",
        extraction_evidence=evidence,
        # A screen-out stated only in part stays in front of a reviewer.
        flags=flags,
    )


_MAKE_EXCLUSIVE = re.compile(
    r"\bmake\s+(\d{1,3}(?:\s*(?:,|and|&)\s*\d{1,3})*)\s+exclusive\b"
    r"|\bexclusive\s*:?\s*(\d{1,3}(?:\s*(?:,|and|&)\s*\d{1,3})*)",
    re.I,
)


_ANCHOR_CODES = re.compile(r"\banchor\s+(?:codes?\s+|punch(?:es)?\s+)?"
                           r"(\d{1,3}(?:\s*(?:,|and|&)\s*\d{1,3})*)", re.I)


def _limits_and_exclusives_from_cells(question: Question, fields: SpecTableFields) -> None:
    """ "Q type: Custom >>> Multi-select up to 3" and "Other details: Make 99
    exclusive" state an answer cap and an exclusive answer; both were read
    for the type only and the rest dropped."""
    from src.engines.questionnaire.select_limits import read_limits

    stated = " ".join(filter(None, [fields.evidence.get("type"), fields.notes]))
    limits = read_limits(stated) or {}
    if limits.get("max_select") and question.type is QuestionType.MULTI:
        question.validation = {**(question.validation or {}),
                               "max_select": limits["max_select"]}
    codes = {int(c) for m in _MAKE_EXCLUSIVE.finditer(fields.notes or "")
             for c in re.findall(r"\d+", m.group(1) or m.group(2))}
    # "If Yes, details: randomize 1 and 2, anchor 3" — the code that stays put.
    anchored = {int(c) for m in _ANCHOR_CODES.finditer(question.source_text or "")
                for c in re.findall(r"\d+", m.group(1))}
    for option in question.options or []:
        if option.code in codes:
            option.exclusive = True
        if option.code in anchored:
            option.anchored = True


def _extract_via_spec_table(
    chunk: QuestionChunk,
    fields: SpecTableFields,
    datamap_question: DatamapQuestion | None = None,
    patterns: list[LearnedPattern] | None = None,
    study_context: str | None = None,
) -> ExtractionResult | None:
    """Deterministic parse, plus ONE small call for whatever it could not state.

    Returns None if the residue call fails, so the caller falls back to normal
    full extraction rather than shipping a half-read question.
    """
    # Anything the document left unstated that the data map already records —
    # the question text, its type, its option list — is filled from the script's
    # own output before a model is asked. A residue call for something written
    # down in the data map is a call paid for twice.
    filled_from_datamap = (
        fill_from_datamap(fields, datamap_question) if datamap_question else []
    )
    question = _question_from_fields(fields, chunk)
    question.show_if = fields.show_if
    # "Google Ads [IF GOOGLE ADS CURRENT USER]": a base on ONE answer that names
    # no question. Read, then dropped — now kept for the pass that resolves it
    # from the hidden variables, and flagged by QA if nothing does.
    labels = {o.code: o.label for o in [*fields.options, *fields.rows]}
    unresolved = {labels[b["code"]]: b["verbatim"] for b in fields.option_bases
                  if not b.get("resolved") and b.get("code") in labels}
    if unresolved:
        question.validation = {**(question.validation or {}), "answer_conditions": unresolved}
    _limits_and_exclusives_from_cells(question, fields)
    # Fields read straight from the table are not model guesses.
    confidence = {"text": 1.0, "type": 1.0, "options": 1.0, "routing": 1.0}
    if filled_from_datamap:
        question.extraction_evidence.update(
            {name: "the data map" for name in filled_from_datamap}
        )

    if fields.complete:
        result = ExtractionResult(question=question, confidence=confidence)
        # A HID_* variable is programmer-computed, not respondent-facing. The
        # full-extraction path runs this before validation; the deterministic
        # paths must too, or a derived variable surfaces as a real question.
        _flag_derived_variable(result, chunk, patterns)
        # No model-emitted routing block exists on a structured parse; the
        # terminate came from the table and is already on the Question.
        _validate_question(result, {})
        return result

    prompt = schema.build_residue_prompt(
        qid=fields.qid,
        missing=fields.missing,
        text=fields.text,
        options=[(o.code, o.label) for o in fields.options],
        base=fields.base,
        notes=fields.notes,
        study_context=study_context,
    )
    try:
        response = _ask_model(
            "questionnaire.extract", prompt, schema.residue_schema(fields.missing)
        )
    except (BackendUnavailable, ModelOutputError, KeyError):
        if set(fields.missing) != {"base"}:
            return None
        # Only the base needs judgment. The rest of the question was read
        # exactly; discarding it for a full model read produced a question whose
        # answers were the table's own labels. Keep it and show the base words.
        question.flags.append(schema.FLAG_AMBIGUOUS_ROUTING)
        question.validation[_ROUTING_VERBATIM_KEY] = fields.base
        result = ExtractionResult(question=question, confidence=confidence)
        _flag_derived_variable(result, chunk, patterns)
        _validate_question(result, {})
        return result

    content = response.content if isinstance(response.content, dict) else {}
    if "type" in fields.missing:
        try:
            question.type = QuestionType(content["type"])
        except (KeyError, ValueError):
            return None
        confidence["type"] = 0.9
    if "text" in fields.missing:
        question.text = str(content.get("text") or "").strip()
        confidence["text"] = 0.9
    for name, attr in (("base", "show_if"), ("terminate", "terminate_if")):
        if name not in fields.missing:
            continue
        raw = content.get(name)
        # A model can return a bare STRING here instead of the object the
        # residue schema asks for — same failure already seen and fixed on the
        # full-extraction path. Treat the string as the verbatim rather than
        # crashing: it is exactly the sentence a reviewer needs to resolve the
        # condition by hand.
        if isinstance(raw, str):
            block = {"verbatim_text": raw, "ambiguous": True}
        elif isinstance(raw, dict):
            block = raw
        else:
            block = {}
        verbatim = str(block.get("verbatim_text") or "").strip() or None
        expression = None if block.get("ambiguous") else block.get(attr)
        setattr(question, attr, _parse_expression(expression, verbatim))
        confidence["routing"] = 0.9
        if verbatim:
            question.validation[_ROUTING_VERBATIM_KEY] = verbatim

    result = ExtractionResult(question=question, confidence=confidence)
    _flag_derived_variable(result, chunk, patterns)
    _validate_question(result, {})
    return result



def _apply_labelled_sections(question: Question, chunk: QuestionChunk) -> bool:
    """A grid written as "COLUMNS" / "ROWS" headings over plain lines.

    Q18a of the Vivo wave-2 document is written this way. Its column list was
    read as the question's own ANSWER options, so the grid arrived with no
    rows and no columns — a respondent saw the wording and nothing else.
    """
    lines = [
        line
        for block in chunk.source_blocks
        if block.block_type != "table"
        for line in (block.text or "").splitlines()
    ]
    rows, cols = labelled_sections(lines)
    if not cols:
        return False
    question.type = QuestionType.GRID_SINGLE
    question.cols = cols
    if rows:
        question.rows = rows
    # Those lines were being offered as this question's answers.
    question.options = []
    question.extraction_evidence["options"] = (
        f"a COLUMNS list of {len(cols)} points"
        + (f" over a ROWS list of {len(rows)}" if rows else ", its rows stated elsewhere")
    )
    if schema.FLAG_EMPTY_OPTIONS in question.flags:
        question.flags.remove(schema.FLAG_EMPTY_OPTIONS)
    return True


def _apply_table_shape(question: Question, chunk: QuestionChunk) -> None:
    """Take a grid's rows and columns from the table's real cells.

    The loader keeps a table's cells on the block; the prose parser only ever
    saw the serialized "a | b | c" text, so a grid arrived with its header as
    "option 1", its punch codes as "option 2", and rows=cols=[] — and a grid
    with no rows emits NO columns in the data file. Eighteen questions of one
    real 91-question instrument were in that state: typed correctly, and
    delivering nothing.
    """
    if _apply_labelled_sections(question, chunk):
        return
    tables = [b.rows for b in chunk.source_blocks if b.rows]
    if not tables:
        return
    entry = next((found for t in tables if (found := entry_table(t))), None)
    if entry is not None and question.type is not QuestionType.GRID_SINGLE:
        rows, percent = entry
        question.type = QuestionType.NUMERIC
        question.rows, question.options, question.cols = rows, [], None
        if percent:
            question.validation = {**(question.validation or {}), "min": 0, "max": 100}
        question.extraction_evidence["options"] = (
            f"{len(rows)} rows to fill in{' (a percentage each)' if percent else ''}"
        )
        if schema.FLAG_EMPTY_OPTIONS in question.flags:
            question.flags.remove(schema.FLAG_EMPTY_OPTIONS)
        return
    shape = question_from_tables(tables, list(question.options))
    if shape is None:
        return
    if not shape.rows and not points_at_a_list(question.text):
        # "How do you feel about Bubble now?" over a 0-10 strip: ONE thing
        # rated, so one answer from the scale — not a grid waiting for rows.
        question.type = QuestionType.SINGLE
        question.options = list(shape.cols)
        question.extraction_evidence["options"] = f"a {len(shape.cols)}-point scale"
        if schema.FLAG_EMPTY_OPTIONS in question.flags:
            question.flags.remove(schema.FLAG_EMPTY_OPTIONS)
        return
    question.type = QuestionType.GRID_SINGLE
    question.rows = shape.rows
    question.cols = shape.cols
    question.options = []  # a grid answers per row, not from one option list
    question.randomize_options = question.randomize_options or shape.randomize_rows
    question.extraction_evidence["options"] = shape.evidence
    if shape.row_conditions:
        question.validation = {
            **(question.validation or {}), "row_conditions": shape.row_conditions,
        }


def _default_required(qtype: QuestionType | None) -> bool:
    """Must a respondent answer this before moving on?

    Every deterministic path was setting `required=False`, and the compiler
    emits `isRequired` straight from it — so on the three real client documents
    0%, 6% and 0% of asked questions had to be answered. A respondent could
    click through an entire interview answering nothing, and the file comes
    back full of holes that look exactly like questions people declined.

    In market research a question is required unless the document says
    otherwise; the way out is a "Don't know" or "Prefer not to answer" ANSWER,
    not a skipped question — which is why a required question with neither is
    now warned about at preflight.

    An open end is the exception both ways round: "anything else you would like
    to add?" genuinely has nothing to add, and forcing text there buys a file
    full of "n/a" and "none".
    """
    return qtype not in (None, QuestionType.INFO, QuestionType.OPEN_TEXT)


def _question_from_prose(fields: ProseFields, chunk: QuestionChunk) -> Question:
    """Build a Question from what a prose question's own directives state."""
    validation: dict[str, Any] = {}
    if fields.terminate_verbatim:
        validation[_ROUTING_VERBATIM_KEY] = fields.terminate_verbatim
    if fields.unknown_answer_tags:
        validation["unknown_answer_tags"] = sorted(set(fields.unknown_answer_tags))
    if fields.directives:
        # Scripter notes we recognised but do not model (SHOW IMAGE, quota
        # instructions). Surfaced for review rather than dropped.
        validation["scripter_directives"] = fields.directives
    if fields.randomize:
        validation["randomize"] = True
    # "Select up to 2." A cap nobody records is a cap nobody enforces, and the
    # question looks right on screen because the sentence is still in it.
    validation.update(fields.select_limits)
    validation.update(fields.bounds)
    # MR techniques the question declares about itself. Recorded structurally
    # so the review UI can show them and the data cross-check can verify a mask
    # actually held — a mis-scripted mask is invisible in the output otherwise.
    techniques = detect_techniques(fields.text or "", *fields.directives)
    if techniques:
        validation["techniques"] = techniques.to_dict()
    flags: list[str] = []
    if fields.derived:
        # The prose parser recognised a scripter-computed variable and typed it
        # `info`, and the FLAG was then dropped on the floor. Everything
        # downstream keys on the flag, not on the type: materialize decides
        # whether to emit a column by it, the review UI says "needs manual
        # setup" by it, the buckets group by it. So the detection worked and
        # nothing acted on it — on the Bubble document that is eleven hidden
        # variables typed correctly and labelled nowhere, and the coded ones
        # (`CODE SAMPLE SOURCE` with its values under it) lost their column
        # entirely.
        flags.append(schema.FLAG_DERIVED_VARIABLE)

    question = Question(
        qid=fields.qid,
        text=fields.text or "",
        type=fields.qtype or QuestionType.SINGLE,
        options=order_terminal_options(list(fields.options)),
        flags=flags,
        required=_default_required(fields.qtype),
        # The field fielding actually reads. It was being recorded only in
        # `validation`, which nothing downstream consults — so a question the
        # parser correctly read as "[PN: RANDOMIZE]" was still fielded in fixed
        # order. Detecting randomisation and then not applying it is worse than
        # not detecting it, because the review UI showed it as handled.
        randomize_options=fields.randomize,
        terminate_if=fields.terminate_if,
        # The base the directive states. Without it `show_if` was empty on
        # every prose document, so routing cross-validation and the condition
        # audit had nothing to check even though the base was written down.
        show_if=fields.show_if,
        source_text=chunk.chunk_text,
        source_location=chunk.provenance,
        validation=validation,
        extraction_method="prose_directives",
        extraction_evidence=dict(fields.evidence),
    )
    _apply_table_shape(question, chunk)
    # Never a select cap on a grid, and only settled HERE: a grid written as a
    # Word table is still typed `multi`/`single` while its prose is read, and
    # only becomes a grid once the table's shape is applied. A cap counts
    # answers to ONE list; a grid's answer is one per row, so a cap kept on it
    # says the whole grid takes that many answers in total — which is not what
    # the question asks. Seen on a real client grid ("Select one answer per
    # row", which arrived here as a cap of 1).
    if question.type is QuestionType.GRID_SINGLE and question.validation:
        for key in ("min_select", "max_select"):
            question.validation.pop(key, None)
    _codes_from_list_numbers(question, chunk)
    return question


_WORD_CODE = re.compile(r"^\s*(\d{1,3})\s*[.)]?\s*$")


def _answer_key(text: str) -> str:
    return " ".join(re.sub(r"\[[^\]]*\]", " ", text or "").lower().split())


def _codes_from_list_numbers(question: Question, chunk: QuestionChunk) -> None:
    """Answer codes as the document prints them, when Word draws them.

    "Don't Know" numbered 99 by Word's list numbering is code 99 in the data;
    counting the lines made it 6, and a tab of "Don't Know" read an empty
    column. Applied only when EVERY answer carries a distinct printed number;
    the question's own screen-out moves with the codes it names.
    """
    options = question.options or []
    if not options:
        return
    printed: dict[str, int] = {}
    for block in chunk.source_blocks:
        number = _WORD_CODE.match(block.list_label or "")
        if number and block.block_type != "table":
            printed.setdefault(_answer_key(block.text), int(number.group(1)))
    mapping = {o.code: printed.get(_answer_key(o.label)) for o in options}
    codes = list(mapping.values())
    if None in codes or len(set(codes)) != len(codes) or all(k == v for k, v in mapping.items()):
        return
    for option in options:
        option.code = mapping[option.code]

    def move(expr: Expression | None) -> None:
        if expr is None:
            return
        if expr.qid == question.qid:
            if expr.values:
                expr.values = [mapping.get(v, v) for v in expr.values]
            if expr.op in ("eq", "selected") and isinstance(expr.value, int):
                expr.value = mapping.get(expr.value, expr.value)
        for child in expr.children or []:
            move(child)

    move(question.terminate_if)
    question.extraction_evidence["codes"] = "the numbers Word prints beside each answer"


def _rebuild_table_rows(source_text: str) -> list[list[str]]:
    """Recover a table's raw cells from the `source_text` a Question kept.

    Only `source_text` survives on a stored Question — the loader's real
    `DocumentBlock.rows` is never persisted. A generic (non Q-Name) table is
    serialized one row per line, cells joined by " | " (questionnaire_loader's
    plain pipe-join fallback), so splitting each such line back on " | "
    recovers the same cell list `_apply_table_shape`/`capture_carousel`
    originally saw. Lines with no " | " are not a table row and are dropped.
    """
    return [line.split(" | ") for line in (source_text or "").splitlines() if " | " in line]


def reread_question(
    question: Question, patterns: list[LearnedPattern] | None = None
) -> Question | None:
    """Re-run the CURRENT deterministic parsers on one question's own text.

    No model call — ever. A stored question was parsed by whatever parser code
    existed the day it was extracted, and a fix since then only reaches it on a
    full re-extract. This replays `parse_prose_question` (and, when the source
    text still carries a table, `_apply_table_shape`/`capture_carousel` against
    rows rebuilt by `_rebuild_table_rows`) over the SAME `source_text` captured
    then, so a parser improvement reaches this one question for free.

    `patterns` is the profile's founder-taught patterns (T33 correction loop).
    Without this, a founder teaching a convention could never see it applied to
    the very document they were looking at — only to the next one uploaded.
    `question_from_tables` (the grid-shape pass) reads no patterns, so there is
    no separate spec-table path here to thread them into.

    Returns None when there is no source_text, or nothing in it the parser can
    read (no label the segmenter would have recognised) — the caller decides
    whether the rebuilt question is actually an improvement over what is
    stored.
    """
    text = question.source_text or ""
    if not text.strip():
        return None

    # A question read from a SPEC TABLE must not be re-read by the prose
    # parser. This path replays `parse_prose_question` only — it never calls
    # `parse_spec_table` — so on a "Q Name: / Q type:" document it does not
    # re-apply the table rules, it applies DIFFERENT ones. Measured on the
    # Google Ads study: HIDDEN_TOPBENEFIT came back as a 9-option question
    # instead of the hidden variable the table declares, and DA_TOP_GOAL lost
    # an option. Neither tripped the regression guard, so both would have been
    # saved over.
    #
    # Refusing is the honest answer until re-read can run the spec-table path
    # (roadmap 2.2). A spec-table study picks up parser fixes by re-upload.
    if (question.extraction_method or "") == "spec_table":
        return None

    table_rows = _rebuild_table_rows(text)
    blocks: list[DocumentBlock] = []
    if table_rows:
        blocks.append(
            DocumentBlock(block_type="table", text=text, paragraph_index=0, rows=table_rows)
        )
    chunk = QuestionChunk(
        chunk_text=text,
        candidate_qid=question.qid,
        source_blocks=blocks,
        provenance=question.source_location or "",
    )

    fields = parse_prose_question(chunk.chunk_text, chunk.candidate_qid, patterns)
    if fields is None:
        return None

    new_question = _question_from_prose(fields, chunk)  # also runs _apply_table_shape
    if blocks:
        capture_carousel(new_question, [table_rows])
    result = ExtractionResult(question=new_question)
    _flag_derived_variable(result, chunk, patterns)
    _validate_question(result, {})
    mark_write_ins([new_question])
    return new_question


def _extract_via_prose(
    chunk: QuestionChunk,
    fields: ProseFields,
    datamap_question: DatamapQuestion | None = None,
    patterns: list[LearnedPattern] | None = None,
    study_context: str | None = None,
) -> ExtractionResult | None:
    """Prose directives, plus one small call for what they do not state.

    Mirrors the spec-table path. Returns None so the caller falls back to full
    extraction rather than shipping a half-read question.
    """
    filled_from_datamap = (
        fill_from_datamap(fields, datamap_question) if datamap_question else []
    )
    question = _question_from_prose(fields, chunk)
    if fields.missing == ["type"] and question.rows and question.cols:
        # The table said what the prose could not. A grid written as a table has
        # no type in its wording — the shape IS the type — so every one of them
        # escalated to a model call for something already known from the cells.
        # Costly, and a needless source of run-to-run variance.
        fields.missing = []
        question.extraction_evidence.setdefault("type", "a grid table")
    elif fields.missing == ["type"] and (summed := read_constant_sum(chunk.chunk_text)):
        # "MUST SUM TO 100%" over a table of empty boxes states the type outright:
        # one number per row. Asked instead, a model called it a rating grid
        # with no scale, and the respondent got nothing to fill in.
        make_constant_sum(question, *summed)
        fields.missing = []
    confidence = {"text": 1.0, "type": 1.0, "options": 1.0, "routing": 1.0}
    if filled_from_datamap:
        question.extraction_evidence.update(
            {name: "the data map" for name in filled_from_datamap}
        )

    if fields.complete:
        result = ExtractionResult(question=question, confidence=confidence)
        _validate_question(result, {})
        return result

    prompt = schema.build_residue_prompt(
        qid=fields.qid,
        missing=fields.missing,
        text=fields.text,
        options=[(o.code, o.label) for o in fields.options],
        notes=fields.terminate_verbatim,
        study_context=study_context,
    )
    try:
        response = _ask_model(
            "questionnaire.extract", prompt, schema.residue_schema(fields.missing)
        )
    except (BackendUnavailable, ModelOutputError, KeyError):
        return None

    content = response.content if isinstance(response.content, dict) else {}
    if "type" in fields.missing:
        try:
            question.type = QuestionType(content["type"])
        except (KeyError, ValueError):
            return None
        confidence["type"] = 0.9
    if "text" in fields.missing:
        question.text = str(content.get("text") or "").strip()
        confidence["text"] = 0.9
    if "terminate" in fields.missing:
        raw = content.get("terminate")
        block = ({"verbatim_text": raw, "ambiguous": True} if isinstance(raw, str)
                 else raw if isinstance(raw, dict) else {})
        verbatim = str(block.get("verbatim_text") or "").strip() or None
        expression = None if block.get("ambiguous") else block.get("terminate_if")
        question.terminate_if = _parse_expression(expression, verbatim)
        confidence["routing"] = 0.9

    question.validation["extracted_by"] = f"{response.backend}:{response.model}"
    result = ExtractionResult(question=question, confidence=confidence)
    _flag_derived_variable(result, chunk, patterns)
    _validate_question(result, {})
    return result


def extract_question(
    chunk: QuestionChunk,
    profile_hint: str | None = None,
    learned_rules: list[str] | None = None,
    patterns: list[LearnedPattern] | None = None,
    datamap_question: DatamapQuestion | None = None,
    study_context: str | None = None,
) -> ExtractionResult:
    """Extract one chunk into a structured Question + confidence + flags.

    `profile_hint` is the document's detected format hint (see
    profiles.detect_profile); it selects the per-profile extraction guidance in
    build_extraction_prompt. None keeps the current (pre-profile) prompt.

    `learned_rules` are founder-taught rules for that profile (correction loop).
    None/empty keeps the prompt byte-identical to the pre-loop one.

    `patterns` are the machine-applicable half of the same corrections: the
    parsers consult them directly, so a taught convention costs nothing on
    every later document instead of a model call each time.

    Never raises on a backend/model failure: a failed chunk comes back flagged
    (CONFIG_ERROR / UNAVAILABLE / MODEL_ERROR by actionability) with a minimal
    placeholder Question AND the exception's type+detail preserved in
    `question.validation["extraction_error"]`, so the caller can keep processing
    the rest of the document and a reviewer can see the real root cause.

    The document-level circuit breaker in `extract_survey_definition` inspects
    the flag/error on the returned result to decide whether to abort — see
    `_is_backend_unavailable_result`.
    """
    # Structured first: a labelled spec table already states the id, wording,
    # options and often the screen-out. Reading those costs no tokens, and the
    # remainder is a far smaller call — measured 451,400 -> 14,264 tokens across
    # one real 74-question document. Falls through to full extraction whenever
    # the table is not parseable or the residue call fails.
    fields = _spec_table_fields(chunk, patterns)
    if fields is not None:
        structured = _extract_via_spec_table(
            chunk, fields, datamap_question, patterns, study_context
        )
        if structured is not None:
            return structured

    # Prose questionnaires have no tables, but their scripter directives are
    # exact where present: "Select all that apply" is the type, "[TERMINATE]"
    # on an option is a screen-out, "[PN: RANDOMIZE]" is randomisation. Two of
    # the three real client documents are prose, so this is not a niche path.
    prose = parse_prose_question(chunk.chunk_text, chunk.candidate_qid, patterns)
    # `prose.complete` FIRST. The gate used to be options-or-terminate alone,
    # which silently threw away every question the prose parser resolves best:
    # an open end, a numeric entry and a display screen all have no options and
    # no screen-out, so a fully-read "[PN: TEXTBOX]" question fell through to a
    # full LLM extraction anyway. Measured on a 3-question document: 951s and
    # three model calls, for questions the parser answers in milliseconds.
    if prose is not None and (prose.complete or prose.options or prose.terminate_if):
        via_prose = _extract_via_prose(chunk, prose, datamap_question, patterns, study_context)
        if via_prose is not None:
            return via_prose

    prompt = schema.build_extraction_prompt(
        chunk.chunk_text, chunk.candidate_qid, profile_hint, learned_rules, study_context
    )
    try:
        response: AIResponse = _ask_model(
            "questionnaire.extract", prompt, schema.EXTRACTION_SCHEMA
        )
    except (BackendUnavailable, ModelOutputError, KeyError) as exc:
        # Keep whatever the parsers already read. A question whose nine options
        # were read perfectly and whose only unknown is single-vs-multi used to
        # come back as an empty placeholder the moment a backend was down —
        # throwing away work that cost nothing and is not in doubt. Downstream
        # then had no options to align codes against and no answers to check,
        # so one outage quietly disabled the checks on every question it hit.
        # Flagged exactly as before, so it still lands in front of a reviewer.
        if prose is not None and (prose.options or prose.qtype):
            question = _question_from_prose(prose, chunk)
            question.validation["unresolved_fields"] = sorted(prose.missing)
            result = ExtractionResult(question=question)
        else:
            result = ExtractionResult(question=_empty_question(chunk))
        result.add_flag(_extraction_flag_for(exc))
        _stash_error(result.question, _EXTRACTION_ERROR_KEY, exc)
        return result

    question, routing = _question_from_raw(response.content, chunk)
    # Which model actually answered. The chain can fall back to a DIFFERENT Groq
    # model when the primary's daily quota is spent (allowances are per model),
    # and that must never be silent: a reviewer comparing two questions needs to
    # know if one of them was read by a different model.
    # `validation` is None on a question carrying no metadata, so create it.
    if question.validation is None:
        question.validation = {}
    question.validation["extracted_by"] = f"{response.backend}:{response.model}"
    result = ExtractionResult(
        question=question,
        confidence=dict(question.extraction_confidence),
    )
    _flag_derived_variable(result, chunk, patterns)  # may flip type -> info before validation
    _validate_question(result, routing)
    return result


def verify_deterministically(
    chunk: QuestionChunk, result: ExtractionResult
) -> bool:
    """Run the checklist first. True when it settled the question on its own.

    A failure here is worth more than a model's opinion: it names the line in
    the document that disagrees with the definition. A pass, where the checklist
    genuinely covered the question, means the model pass has nothing left to
    add — and every model call skipped is a call that cannot answer differently
    tomorrow.
    """
    checks = verify_against_source(result.question, chunk.chunk_text)
    problems = failed(checks)
    if problems:
        result.add_flag(schema.FLAG_VERIFICATION_DISCREPANCY)
        result.question.validation = result.question.validation or {}
        result.question.validation["verification_checks"] = [
            {"check": c.name, "detail": c.detail, "evidence": c.evidence} for c in problems
        ]
        for field_name in ("options", "type", "routing"):
            result.confidence[field_name] = min(result.confidence.get(field_name, 1.0), 0.5)
        return True  # settled: something concrete is wrong, and we can say what
    if covers_the_question(result.question, checks):
        result.verified = True
        result.question.extraction_evidence.setdefault(
            "verified", f"{len(checks)} checks against the source text"
        )
        return True
    return False


def verify_question(chunk: QuestionChunk, result: ExtractionResult) -> ExtractionResult:
    """Grounded second-pass audit of an extraction against its source text.

    On backend/model failure: flag `VERIFICATION_UNAVAILABLE` and return the
    result otherwise unchanged — a flagged "couldn't verify" is honest and tells
    a human to hand-check, which is far better than crashing the pipeline over a
    verification-pass outage.

    When the verifier reports the extraction is NOT faithful, flag
    `VERIFICATION_DISCREPANCY` and lower the confidence of whichever fields the
    discrepancy strings implicate (all of them if it's unclear).
    """
    prompt = schema.build_verification_prompt(chunk.chunk_text, result.question.to_dict())
    try:
        response = _ask_model("questionnaire.verify", prompt, schema.VERIFICATION_SCHEMA)
    except (BackendUnavailable, ModelOutputError, KeyError) as exc:
        result.add_flag(schema.FLAG_VERIFICATION_UNAVAILABLE)
        _stash_error(result.question, _VERIFICATION_ERROR_KEY, exc)
        return result

    result.verified = True
    faithful = bool(response.content.get("faithful", True))
    discrepancies = [str(d) for d in response.content.get("discrepancies") or []]

    if not faithful:
        result.add_flag(schema.FLAG_VERIFICATION_DISCREPANCY)
        result.question.validation = {
            **(result.question.validation or {}),
            "verification_discrepancies": discrepancies,
        }
        _downgrade_confidence(result, discrepancies)

    return result


# Maps a keyword found in a discrepancy string to the confidence field it
# implicates. A discrepancy that matches none of these downgrades every field,
# because "something is wrong but we can't localize it" should lower trust
# across the board rather than nowhere.
_DISCREPANCY_FIELD_HINTS: dict[str, str] = {
    "option": "options",
    "code": "options",
    "label": "options",
    "type": "type",
    "rout": "routing",  # rout(e|ing)
    "skip": "routing",
    "terminate": "routing",
    "wording": "text",
    "text": "text",
    "qid": "text",
}


def _downgrade_confidence(result: ExtractionResult, discrepancies: list[str]) -> None:
    """Halve the confidence of fields implicated by the discrepancy strings.

    Mirrors the lowered scores onto the Question's `extraction_confidence` so a
    reviewer reading either the result or the persisted question sees the same
    downgraded numbers.
    """
    targeted: set[str] = set()
    for text in discrepancies:
        low = text.lower()
        for keyword, field_name in _DISCREPANCY_FIELD_HINTS.items():
            if keyword in low:
                targeted.add(field_name)

    # Ambiguous / empty discrepancies -> lower everything we track.
    if not targeted:
        targeted = {"text", "type", "options", "routing"}

    for field_name in targeted:
        current = float(result.confidence.get(field_name, 1.0))
        lowered = round(current * 0.5, 4)
        result.confidence[field_name] = lowered
        result.question.extraction_confidence[field_name] = lowered


def _match_qid_case(expr: Expression, known_qids: set[str]) -> None:
    """Rewrite each referenced qid to the known qid it equals ignoring case."""
    by_upper = {k.upper(): k for k in known_qids}
    if expr.qid and expr.qid not in known_qids:
        expr.qid = by_upper.get(expr.qid.upper(), expr.qid)
    for child in expr.children or []:
        _match_qid_case(child, known_qids)


def _resolve_cross_chunk_routing(
    results: list[ExtractionResult], known_qids: set[str]
) -> None:
    """Deferred cross-chunk check: null routing that references a missing qid.

    Runs only after every chunk is extracted, when the full qid set is known.
    A show_if/terminate_if referencing a qid that exists nowhere in the document
    is unresolvable, so we flag `UNRESOLVED_ROUTING_QID` and null out THAT
    expression only (not the whole question) — the verbatim routing sentence is
    preserved in `question.validation` so a human can still repair it by hand.
    """
    for result in results:
        q = result.question
        for kind in ("show_if", "terminate_if"):
            expr: Expression | None = getattr(q, kind)
            if expr is None:
                continue
            # "HID_B2B_B2c = 2" means HID_B2B_B2C: a typed lowercase letter is
            # not a different question. Fix the case before calling it missing.
            _match_qid_case(expr, known_qids)
            # A referenced qid that is neither known in the document nor this
            # question itself is unresolvable.
            unresolved = {
                ref for ref in expr.referenced_qids() if ref not in known_qids
            }
            if unresolved:
                result.add_flag(schema.FLAG_UNRESOLVED_ROUTING_QID)
                # Kept for the reviewer: the expression itself is about to go.
                q.validation = {
                    **(q.validation or {}),
                    "unresolved_routing": [
                        *(q.validation or {}).get("unresolved_routing", []),
                        {"field": kind, "qids": sorted(unresolved)},
                    ],
                }
                setattr(q, kind, None)


def _is_backend_unavailable_result(result: ExtractionResult) -> bool:
    """True if this chunk's EXTRACT pass failed with a BackendUnavailable.

    The circuit breaker counts these: both a transient outage
    (EXTRACTION_UNAVAILABLE) and a config fault (EXTRACTION_CONFIG_ERROR) map to
    BackendUnavailable and mean "the backend can't serve ANY chunk". A per-chunk
    MODEL_ERROR (schema-rejected output) does NOT count — that's this document's
    problem, not the backend's, so it accumulates a flag without aborting.
    """
    validation = result.question.validation or {}
    err = validation.get(_EXTRACTION_ERROR_KEY)
    if not err:
        return False
    return err.get("error_type") == BackendUnavailable.__name__


def _extraction_summary(results: list[ExtractionResult]) -> dict[str, Any]:
    """Reviewer-facing counts: totals, per-flag counts, avg confidence,
    plus a `failure_reasons` counter keyed by the preserved error_type."""
    flag_counts: dict[str, int] = {}
    failure_reasons: dict[str, int] = {}
    conf_sum = 0.0
    conf_n = 0
    for result in results:
        for flag in result.flags:
            flag_counts[flag] = flag_counts.get(flag, 0) + 1
        validation = result.question.validation or {}
        for key in (_EXTRACTION_ERROR_KEY, _VERIFICATION_ERROR_KEY):
            err = validation.get(key)
            if err:
                etype = err.get("error_type", "Unknown")
                failure_reasons[etype] = failure_reasons.get(etype, 0) + 1
        for value in result.confidence.values():
            conf_sum += float(value)
            conf_n += 1
    return {
        "total_chunks": len(results),
        "flag_counts": flag_counts,
        "failure_reasons": failure_reasons,
        "average_confidence": round(conf_sum / conf_n, 4) if conf_n else 0.0,
        "verified_chunks": sum(1 for r in results if r.verified),
        # Derived, not counted: a question that skipped verification is
        # unverified AND unflagged. A verify FAILURE always sets a flag, so the
        # two cases stay distinguishable. Surfaced so "why was this not
        # verified" is answerable without reading logs.
        "skipped_verification": sum(
            1 for r in results if not r.verified and not r.flags
        ),
    }



def _maybe_checkpoint(
    results: list[ExtractionResult],
    chunks: list[QuestionChunk],
    study_id: str,
    detection,
    reconcile_meta: dict | None,
    on_checkpoint: Callable[[int, int, SurveyDefinition], None] | None,
    every: int,
) -> None:
    """Hand the caller a persistable snapshot, every `every` chunks.

    Never lets a checkpoint failure kill the extraction it is protecting: the
    point is to lose less work, so a broken save must cost one snapshot, not the
    whole run.
    """
    if not on_checkpoint or not results:
        return
    done, total = len(results), len(chunks)
    if done % every and done != total:
        return
    try:
        on_checkpoint(done, total, _assemble_survey(
            list(results), study_id, detection, reconcile_meta, partial=True
        ))
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"[{study_id}] checkpoint failed at {done}/{total}: {exc}\n")


def _derived_result(chunk: QuestionChunk, role: str) -> ExtractionResult:
    """A chunk the data map says was computed, not asked — no model call.

    Typed `info` and flagged so the review UI says "needs manual setup" rather
    than surfacing an unanswerable question, and so it never reaches a
    respondent.
    """
    question = Question(
        qid=chunk.candidate_qid or "UNKNOWN",
        text=(chunk.chunk_text or "").strip().splitlines()[0][:300]
        if chunk.chunk_text
        else "",
        type=QuestionType.INFO,
        required=False,
        source_text=chunk.chunk_text,
        source_location=chunk.provenance,
        extraction_method="datamap",
        extraction_evidence={"type": f"the data map records this as a {role} variable"},
    )
    result = ExtractionResult(question=question, confidence={"type": 1.0})
    result.add_flag(schema.FLAG_DERIVED_VARIABLE)
    return result


def extract_survey_definition(
    chunks: list[QuestionChunk],
    study_id: str,
    blocks: list[DocumentBlock] | None = None,
    rules_for_profile: Callable[[str], list[str]] | None = None,
    patterns_for_profile: Callable[[str], list[LearnedPattern]] | None = None,
    datamap: DataMap | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    on_checkpoint: Callable[[int, int, SurveyDefinition], None] | None = None,
    checkpoint_every: int = 5,
) -> SurveyDefinition:
    """Extract + verify every chunk and assemble one draft SurveyDefinition.

    When `blocks` (the full ordered DocumentBlock list the chunks came from) is
    supplied, one LLM reconciliation pass runs FIRST to recover any questions the
    regex segmenter missed (see reconciler.reconcile_segmentation); recovered
    chunks then flow through the exact same extract->validate->verify pipeline.
    Omitting `blocks` skips reconciliation — kept optional so unit tests that
    hand-build chunk lists don't need real blocks (and don't pay an LLM call).

    Sequential per chunk. Parallelising would NOT help on a token-capped tier:
    the limit is tokens per minute, not concurrency, so running chunks at once
    just reaches the ceiling faster.

    `on_progress(done, total)` fires after each chunk so the caller can show a
    heartbeat.

    `on_checkpoint(done, total, partial_survey)` fires every `checkpoint_every`
    chunks with a REAL SurveyDefinition covering the work done so far, so the
    caller can persist it. This is the difference between a run killed at
    question 70 of 75 leaving seventy usable questions and leaving nothing —
    which is exactly what happened in production: a 15-minute run persisted
    ZERO. Both callbacks keep this engine free of the database.
    """
    reconcile_meta: dict = {}
    # Recognize the document's authoring convention (GBK spec-table / prose /
    # numbered / unknown) once, from structural signals, and route the per-chunk
    # extraction prompt accordingly. Needs the full block list; when omitted
    # (unit tests that hand-build chunks) detection is skipped and the prompt is
    # unchanged from today.
    detection = detect_profile(blocks) if blocks is not None else None
    profile_hint = detection.profile.prompt_hint if detection else None
    # Founder-taught rules for the DETECTED profile (correction loop). Resolved
    # through a callable so this engine never imports api.db — only detection
    # knows the slug, so the caller can't look the rules up ahead of time. No
    # provider, or a profile nobody has corrected, yields [] and the prompt is
    # byte-identical to the pre-correction-loop one (the T32 no-op guarantee).
    learned_rules: list[str] = []
    if detection is not None and rules_for_profile is not None:
        learned_rules = list(rules_for_profile(detection.profile.slug) or [])
    # The machine-applicable half of the same corrections. Same provider seam,
    # same no-op guarantee: no patterns means the parsers behave exactly as
    # they did before anyone taught them anything.
    patterns: list[LearnedPattern] = []
    if detection is not None and patterns_for_profile is not None:
        patterns = list(patterns_for_profile(detection.profile.slug) or [])
    if blocks is not None:
        # Heartbeat BEFORE and AFTER reconciliation. This is a single
        # whole-document LLM call that runs before any chunk is extracted, and
        # it was completely silent: measured at 12 minutes on one upload,
        # against a 15-minute staleness watchdog. The run was declared dead
        # ("the background worker was likely killed by a restart") while it was
        # working perfectly — and on a larger document it never survived at all.
        # Two callbacks cost nothing and make the pre-pass visible as progress.
        if on_progress is not None:
            on_progress(0, len(chunks))
        chunks = reconcile_segmentation(blocks, chunks, reconcile_meta)
        if on_progress is not None:
            on_progress(0, len(chunks))

    # "Base: ..." lines written above a question arrive at the tail of the
    # question before it; screen-out lines below a question can arrive alone.
    chunks = hoist_loose_routing(chunks)

    # Read the study's instructions before any question, as an analyst does:
    # what the survey is for, its groups, and the rules that govern later
    # questions. One model call per document; each question's own model call
    # then receives the parts that concern it. Real documents only — unit
    # tests that hand-build chunks pass no blocks and pay no call.
    understanding = understand_study(chunks) if blocks is not None else None
    if blocks is not None and on_progress is not None:
        on_progress(0, len(chunks))

    # The script's own record of what was fielded, when the data arrived first.
    # Used two ways: to answer a parser's open questions without a model call,
    # and afterwards to add questions the document reader never found at all.
    datamap_index = index_by_qid(datamap) if datamap is not None else {}
    # Columns the script computes rather than asks. The document describes them
    # inline like questions, so without this the reader pays a model call per
    # hidden variable and gets a fabricated answer type for something nobody
    # was ever asked.
    datamap_not_asked = non_answerable_qids(datamap) if datamap is not None else {}

    results: list[ExtractionResult] = []
    consecutive_backend_failures = 0
    tripped: BackendUnavailable | None = None
    _MODEL_OFF.set(False)
    for chunk in chunks:
        key = normalize_qid(chunk.candidate_qid or "")
        not_asked = datamap_not_asked.get(key)
        if not_asked and key not in datamap_index:
            results.append(_derived_result(chunk, not_asked))
            if on_progress:
                on_progress(len(results), len(chunks))
            _maybe_checkpoint(
                results, chunks, study_id, detection, reconcile_meta,
                on_checkpoint, checkpoint_every,
            )
            continue
        known = datamap_index.get(key)
        result = extract_question(
            chunk, profile_hint, learned_rules, patterns, known,
            study_context=context_for(understanding, chunk.candidate_qid),
        )

        if _is_backend_unavailable_result(result):
            consecutive_backend_failures += 1
            # Circuit breaker: if the FIRST N processed chunks ALL failed with a
            # BackendUnavailable, the backend can serve nothing — abort now
            # instead of burning the remaining ~2*(len-N) doomed LLM calls. The
            # raise propagates to api/routes/questionnaire.py::_run_extraction's
            # except-path, flipping the row to 'error' with this detail. We skip
            # the verify call for a failed-extract chunk regardless, so each
            # doomed chunk costs one attempt, not two.
            results.append(result)
            if on_progress:
                on_progress(len(results), len(chunks))
            _maybe_checkpoint(
                results, chunks, study_id, detection, reconcile_meta,
                on_checkpoint, checkpoint_every,
            )
            if (
                tripped is None
                and len(results) == consecutive_backend_failures
                and consecutive_backend_failures >= _CIRCUIT_BREAKER_CHUNKS
            ):
                err = (result.question.validation or {}).get(_EXTRACTION_ERROR_KEY, {})
                detail = err.get("error_detail", "backend unavailable")
                # Stop asking the model, but keep reading: a document whose first
                # chunks are front matter would otherwise lose every question the
                # deterministic parsers can read on their own. The error is
                # raised below only if NOTHING was readable that way.
                tripped = BackendUnavailable(
                    f"extraction aborted after {consecutive_backend_failures} consecutive "
                    f"backend failures (circuit breaker): {detail}",
                    config_error=bool(
                        err.get("error_type") == BackendUnavailable.__name__
                        and schema.FLAG_EXTRACTION_CONFIG_ERROR in result.flags
                    ),
                )
                _MODEL_OFF.set(True)
            continue

        # Extraction succeeded (or failed in a non-backend way, e.g. MODEL_ERROR):
        # the backend is reachable, so reset the consecutive-failure streak and
        # run the grounded verify pass as normal.
        consecutive_backend_failures = 0
        # Halves LLM calls on clean questions — see SKIP_VERIFY_CONFIDENCE_FLOOR.
        # A skipped question stays `verified=False`, so it is never
        # auto-approved and always reaches a human.
        # The checklist runs on EVERY question, not only the ones that failed a
        # confidence gate. It costs nothing, and confidence says how sure the
        # reader was — not whether it was right. The option silently dropped
        # from a live instrument came from the deterministic parser at
        # confidence 1.0, so it never reached a check of any kind.
        settled = verify_deterministically(chunk, result)
        if _needs_verification(result) and not settled:
            # Only what the checklist could not settle reaches the model.
            result = verify_question(chunk, result)
        results.append(result)
        if on_progress:
            on_progress(len(results), len(chunks))
        _maybe_checkpoint(
            results, chunks, study_id, detection, reconcile_meta,
            on_checkpoint, checkpoint_every,
        )

    if tripped is not None:
        _MODEL_OFF.set(False)
        if all(_is_backend_unavailable_result(r) for r in results):
            raise tripped

    # What a question's tables state beyond its own answers: a carousel of
    # categories, a brand list named once for a range of questions. Captured
    # while each chunk's tables are in hand; applied once the survey exists.
    for result, chunk in zip(results, chunks, strict=False):
        tables = [b.rows for b in chunk.source_blocks if b.rows]
        if tables:
            capture_carousel(result.question, tables)

    # A question the brief states a rule about is never pre-approved: an analyst
    # checks it against that rule. The rule travels with it, in the brief's words.
    governed = governing_rules(understanding)
    for result in results:
        rules = governed.get((result.question.qid or "").upper())
        if rules:
            result.add_flag(schema.FLAG_BRIEF_RULE)
            result.question.validation["brief_rules"] = rules

    survey = _assemble_survey(results, study_id, detection, reconcile_meta)
    if understanding:
        survey.metadata["study_understanding"] = understanding

    # What the document shows as taken out — struck through, or a Word tracked
    # deletion. It is not in the survey, and a reviewer has to be told: "the
    # brand is missing from Q3" and "the brand was struck out of Q3" are
    # different problems with different fixes.
    struck = [text for chunk in chunks for block in chunk.source_blocks for text in block.removed]
    if struck:
        survey.metadata["struck_out"] = struck[:200]

    # A recruitment path's header ("... (IF S0=1 AND S1=26-60) ASK P1-P3") states
    # the base for questions in OTHER chunks, so it can only be applied now that
    # the whole survey exists. Also emits the path as a HID_ variable.
    apply_section_paths(survey)
    # "BUBBLE CRM INTRODUCTION & SCREENER (S0=2)": a heading that gates the
    # questions under it.
    apply_section_bases(survey)
    apply_intro_screens(survey)
    # "Q2a. ever heard of? | Q2b. Ever purchased from?" across one brand list is
    # two questions, not one grid allowing a single answer per brand.
    split_subquestion_grids(survey)
    # "FOR Q6-Q10 ... SET OF 10 BRANDS", and Q6 asked once per category.
    apply_carousels(survey)
    # "___ % item" lines that must total 100: a number box per item.
    apply_constant_sums(survey)
    # "… either in-person or through online/delivery) Yes" with "No" as the only
    # answer: the first option was written on the question's own line.
    repair_swallowed_options(survey)
    # "How familiar are you with EACH of these pharmacies?" answered Yes/No:
    # a grid whose items were lost, not a single question. Before fill_lists,
    # which is what then supplies its rows.
    apply_per_item_grids(survey)
    # "[SHOW Q26 LIST]", "each of these brands": a list stated once and used
    # again. Filled before loops, which copy each template's list.
    fill_lists(survey)
    # A question asked once per brand is not one question: held as one it is
    # eight columns with no way to tell one brand's answers from another's.
    expand_loops(survey)

    # A MaxDiff or conjoint block is an experiment, not a question. Last, so
    # every other pass has finished shaping the question it reads.
    flag_trade_offs(survey)
    # "Google Ads Block of questions: ..." — the study's own structure, stated
    # in a scripter note. After every question exists, since it names them.
    apply_block_notes(survey)

    # What the document says about the STUDY, not about one question: sample
    # size, length of interview, the screening requirements, and the quota table
    # with its minimum and maximum per group. Read from the opening chunk, which
    # carries no question label and was therefore set aside entirely. Documents
    # that have no overview are the normal other case and cost nothing here.
    if chunks:
        overview = parse_overview(chunks[0].source_blocks)
        if not overview.is_empty():
            survey.metadata["study_overview"] = overview.to_dict()

    # Whatever the document reader never found at all. The data map lists every
    # question that was actually fielded, so a question it names and no chunk
    # produced can be rebuilt from the script's own text and punches instead of
    # being lost. Measured on a real study: 24 questions read from the document,
    # 105 more recovered here — and each one would otherwise have been a model
    # call, or simply absent.
    if datamap is not None:
        recovered, _links = recover_missing(survey, datamap)
        for question in recovered:
            survey.questions.append(question)
            if survey.blocks:
                survey.blocks[-1].question_ids.append(question.qid)
        if recovered:
            survey.metadata["recovered_from_datamap"] = [q.qid for q in recovered]

    if blocks is not None:
        survey.metadata["document_markers"] = document_markers(blocks)

    # "Other (Specify)": the option that asks the respondent to type. After
    # every pass that builds or copies option lists (loops, datamap recovery).
    mark_write_ins(survey)

    # "[BASE: ANSWERED YES ABOVE]": a base that points instead of naming. Before
    # QA, so QA judges the survey with these bases applied.
    resolve_relative_bases(survey)
    # "ASK Q12-Q19 IF EVER PURCHASED BUBBLE AT Q2b" — needs the final lists.
    apply_worded_asks(survey)
    # "IF A3 = 3 OR 4 SKIP TO A6": gates the questions the skip jumps over.
    apply_skips(survey)
    apply_choice_bands(survey)
    apply_placeholder_pipes(survey)
    # "MULTIPLE AT S6", "= 3-5 for at least one option".
    resolve_counted_bases(survey)
    # A loop turn's base, pointing at a hidden variable that names its brand.
    resolve_loop_bases_from_hidden(survey)
    # "Google Ads [IF GOOGLE ADS CURRENT USER]": one answer's own base.
    resolve_answer_bases_from_hidden(survey)
    # Automatic answers and typed-vs-choice contradictions, before QA so the
    # base flag an automatic answer does not deserve is gone when QA looks.
    mark_derived_facts(survey)

    # The QA loop, as the last pass: a QA agent tests every question as the
    # respondent will meet it, a programmer agent repairs what it safely can,
    # and what is left is flagged for a person. Deterministic — no model call.
    run_qa(survey)
    # A second opinion over the whole survey, for problems between questions no
    # rule covers. Real documents only (one model call); never blocks anything.
    if blocks is not None:
        survey.metadata["logic_review"] = review_logic(survey)

    # Last, after every pass that changes the survey: the list was taken before
    # lists were filled and sections applied, and the review screen showed four
    # questions "with no rows" that had them.
    survey.metadata["validation_errors"] = survey.validate()
    # Which skills decoded this document, what none could, and what to teach —
    # so an unfamiliar format is announced, not found question by question.
    survey.metadata["decoding"] = decoding_report(survey)
    return survey



def _deduplicate_qids(results: list[ExtractionResult]) -> None:
    """Make every qid unique, in place.

    A qid is an ADDRESS: the review UI looks a question up by it, approval is
    recorded against it, routing references it, and the data column takes its
    name from it. Two questions sharing one makes the second unreachable —
    approving it clears flags on the first, so its blocking flag can never be
    cleared and the questionnaire can never be finalised. That is a real dead
    end a reviewer hit.

    Duplicates arise legitimately: every chunk whose extraction failed is named
    UNKNOWN, and a document can repeat a label across sections.

    The FIRST occurrence keeps the original qid, so existing routing references
    still resolve to what they most likely meant. Later ones are suffixed and
    keep their original in `validation.original_qid`, so the rename is visible
    rather than silent.
    """
    seen: dict[str, int] = {}
    for result in results:
        qid = result.question.qid or "UNKNOWN"
        if qid not in seen:
            seen[qid] = 1
            continue
        seen[qid] += 1
        new_qid = f"{qid}_{seen[qid]}"
        while new_qid in seen:  # a document may already contain "S6_2"
            seen[qid] += 1
            new_qid = f"{qid}_{seen[qid]}"
        if result.question.validation is None:
            result.question.validation = {}
        result.question.validation["original_qid"] = qid
        result.question.qid = new_qid
        seen[new_qid] = 1
        # Its own screen-out named itself by the shared name; left alone it
        # screened on the FIRST question's answers ("TERMINATE if 98" against
        # a question with no 98), so it could never fire.
        _rename_qid(result.question.terminate_if, qid, new_qid)


def _rename_qid(expr: Expression | None, old: str, new: str) -> None:
    if expr is None:
        return
    if expr.qid == old:
        expr.qid = new
    for child in expr.children or []:
        _rename_qid(child, old, new)


def _assemble_survey(
    results: list[ExtractionResult],
    study_id: str,
    detection=None,
    reconcile_meta: dict | None = None,
    partial: bool = False,
) -> SurveyDefinition:
    """Build a SurveyDefinition from however many results exist.

    Split out so a CHECKPOINT can persist the same shape mid-run. A partial
    survey is a real, reviewable questionnaire covering the questions done so
    far — the alternative, which is what used to happen, is that a worker killed
    at question 70 of 75 left absolutely nothing behind.

    `partial=True` marks it so the UI and the route can tell an in-progress
    snapshot from a finished extraction.
    """
    # Before anything reads a qid as an address.
    _deduplicate_qids(results)

    known_qids = {r.question.qid for r in results}
    _resolve_cross_chunk_routing(results, known_qids)

    questions = [r.question for r in results]
    default_block = Block(
        block_id="B1",
        label="Extracted questions",
        question_ids=[q.qid for q in questions],
    )

    survey = SurveyDefinition(
        study_id=study_id,
        source="extracted",
        status="draft",
        blocks=[default_block],
        questions=questions,
    )
    survey.metadata["validation_errors"] = survey.validate()
    survey.metadata["extraction_summary"] = _extraction_summary(results)
    # Detected format profile (label + confidence + the signals that fired), so a
    # reviewer sees "Detected format: GBK scripter spec-table (0.92)". Only when
    # detection ran (blocks supplied).
    if detection is not None:
        survey.metadata["profile"] = detection.to_metadata()
    # Document-level reconciliation status (e.g. the pass was unavailable). Only
    # recorded when reconciliation ran and had something to report.
    if reconcile_meta:
        survey.metadata["reconciliation"] = reconcile_meta
    # Exception-based review: pre-approve the clean questions so the human only
    # reviews the exceptions. `verified` lives on the ExtractionResult (not the
    # persisted Question), so the clean set is computed HERE where results are in
    # scope and stashed on metadata for the route to persist into approved_qids.
    survey.metadata["auto_approved_qids"] = [
        r.question.qid for r in results if _is_auto_approvable(r)
    ]
    # A checkpoint is explicitly incomplete: cross-chunk routing has only seen
    # the qids extracted SO FAR, so a reference to a later question looks
    # unresolvable until the run finishes. Marking it stops a snapshot being
    # mistaken for a finished extraction.
    survey.metadata["partial"] = partial
    return survey


# --- CLI debugging entry point ---------------------------------------------
# There is no scripts/ convention for engine debug tools (scripts/ holds demo
# and data-generation scripts, not per-engine harnesses), so this lives inline.
# It is a manual test harness for running real .docx/PDF questionnaires through
# the full pipeline, not a product feature.


def _main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m src.engines.questionnaire.extractor <questionnaire.docx|.pdf>")
        return 2

    from pathlib import Path

    from src.intake.questionnaire_loader import (
        load_docx,
        load_pdf,
        segment_into_questions,
    )

    path = Path(argv[1])
    suffix = path.suffix.lower()
    if suffix == ".docx":
        blocks = load_docx(path)
    elif suffix == ".pdf":
        blocks = load_pdf(path)
    else:
        print(f"unsupported file type: {suffix} (expected .docx or .pdf)")
        return 2

    chunks = segment_into_questions(blocks)
    survey = extract_survey_definition(chunks, study_id=path.stem, blocks=blocks)
    summary = survey.metadata["extraction_summary"]

    print(f"questions:          {len(survey.questions)}")
    print(f"average confidence: {summary['average_confidence']}")
    print(f"flag counts:        {json.dumps(summary['flag_counts'])}")
    print(f"validation errors:  {len(survey.metadata['validation_errors'])}")
    print("---")
    print(survey.to_json())
    return 0


if __name__ == "__main__":  # pragma: no cover - manual debugging harness
    raise SystemExit(_main(sys.argv))

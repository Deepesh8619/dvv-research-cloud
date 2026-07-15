"""LLM questionnaire extraction: chunks -> structured SurveyDefinition.

This is the second (LLM) stage of questionnaire ingestion. `questionnaire_loader`
turns a .docx/PDF into deterministic `QuestionChunk`s; this module turns each
chunk into a structured `Question` via a three-step pipeline:

    1. extract   — one schema-constrained LLM call per chunk (Claude via router)
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
import sys
from dataclasses import dataclass, field
from typing import Any

from src.ai.router import (
    AIResponse,
    BackendUnavailable,
    ModelOutputError,
    run_task,
)
from src.core.survey_definition import (
    Block,
    Expression,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
)
from src.engines.questionnaire import schema
from src.engines.questionnaire.reconciler import reconcile_segmentation
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
# flag; str(BackendUnavailable("ANTHROPIC_API_KEY not set")) names it exactly.
_EXTRACTION_ERROR_KEY = "extraction_error"
_VERIFICATION_ERROR_KEY = "verification_error"

# The circuit breaker aborts the whole run once this many of the first processed
# chunks have ALL failed with BackendUnavailable — an N/N failure is one root
# cause (a missing key / dead backend), not N review items, and shouldn't cost
# 2*N doomed LLM attempts to discover.
_CIRCUIT_BREAKER_CHUNKS = 3

# Exception-based review (product decision): a question is auto-approved
# (pre-cleared so a human only reviews exceptions) ONLY when the pipeline is
# fully confident in it — the grounded verify pass ran AND agreed, it carries
# ZERO flags, and the LOWEST per-field confidence the model reported is at or
# above this floor. Anything short of all three stays unapproved for human eyes.
AUTO_APPROVE_CONFIDENCE_FLOOR = 0.85


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
    routing = content.get("routing") or {}
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
    )
    return question, routing


def _flag_derived_variable(result: ExtractionResult, chunk: QuestionChunk) -> None:
    """Detect a hidden/derived variable and label it honestly (no derivation).

    A ``HID_*`` qid is a programmer-computed flag derived from OTHER answers, not
    a respondent-facing question — its derivation logic usually isn't even in the
    document. The platform has no derived-variable engine, so the honest fix is
    detection + labelling, not derivation: force ``type=info`` and flag
    ``DERIVED_VARIABLE`` so the review UI says "needs manual setup" instead of
    surfacing a mis-extracted, unanswerable question. Run BEFORE validation so
    the info type suppresses the (irrelevant) empty-options check.

    Detection is by the ``HID_`` qid prefix, which is the reliable signal in real
    GBK QREs (and the loader now strips the "provide additional scripting
    details" boilerplate that would have been the only other, fuzzier signal).
    """
    qid = chunk.candidate_qid or result.question.qid or ""
    if qid.upper().startswith("HID_"):
        result.question.type = QuestionType.INFO
        result.add_flag(schema.FLAG_DERIVED_VARIABLE)


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
    if q.type in _OPTION_BEARING_TYPES:
        has_choices = bool(q.options) or bool(q.rows)
        if not has_choices:
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
    for expr in (q.show_if, q.terminate_if):
        if expr is None:
            continue
        refs = expr.referenced_qids()
        if refs and refs == {q.qid}:
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


def extract_question(chunk: QuestionChunk) -> ExtractionResult:
    """Extract one chunk into a structured Question + confidence + flags.

    Never raises on a backend/model failure: a failed chunk comes back flagged
    (CONFIG_ERROR / UNAVAILABLE / MODEL_ERROR by actionability) with a minimal
    placeholder Question AND the exception's type+detail preserved in
    `question.validation["extraction_error"]`, so the caller can keep processing
    the rest of the document and a reviewer can see the real root cause.

    The document-level circuit breaker in `extract_survey_definition` inspects
    the flag/error on the returned result to decide whether to abort — see
    `_is_backend_unavailable_result`.
    """
    prompt = schema.build_extraction_prompt(chunk.chunk_text, chunk.candidate_qid)
    try:
        response: AIResponse = run_task(
            "questionnaire.extract", prompt, schema.EXTRACTION_SCHEMA
        )
    except (BackendUnavailable, ModelOutputError, KeyError) as exc:
        result = ExtractionResult(question=_empty_question(chunk))
        result.add_flag(_extraction_flag_for(exc))
        _stash_error(result.question, _EXTRACTION_ERROR_KEY, exc)
        return result

    question, routing = _question_from_raw(response.content, chunk)
    result = ExtractionResult(
        question=question,
        confidence=dict(question.extraction_confidence),
    )
    _flag_derived_variable(result, chunk)  # may flip type -> info before validation
    _validate_question(result, routing)
    return result


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
        response = run_task("questionnaire.verify", prompt, schema.VERIFICATION_SCHEMA)
    except (BackendUnavailable, ModelOutputError, KeyError) as exc:
        result.add_flag(schema.FLAG_VERIFICATION_UNAVAILABLE)
        _stash_error(result.question, _VERIFICATION_ERROR_KEY, exc)
        return result

    result.verified = True
    faithful = bool(response.content.get("faithful", True))
    discrepancies = [str(d) for d in response.content.get("discrepancies") or []]

    if not faithful:
        result.add_flag(schema.FLAG_VERIFICATION_DISCREPANCY)
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
            # A referenced qid that is neither known in the document nor this
            # question itself is unresolvable.
            unresolved = {
                ref for ref in expr.referenced_qids() if ref not in known_qids
            }
            if unresolved:
                result.add_flag(schema.FLAG_UNRESOLVED_ROUTING_QID)
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
    }


def extract_survey_definition(
    chunks: list[QuestionChunk],
    study_id: str,
    blocks: list[DocumentBlock] | None = None,
) -> SurveyDefinition:
    """Extract + verify every chunk and assemble one draft SurveyDefinition.

    When `blocks` (the full ordered DocumentBlock list the chunks came from) is
    supplied, one LLM reconciliation pass runs FIRST to recover any questions the
    regex segmenter missed (see reconciler.reconcile_segmentation); recovered
    chunks then flow through the exact same extract->validate->verify pipeline.
    Omitting `blocks` skips reconciliation — kept optional so unit tests that
    hand-build chunk lists don't need real blocks (and don't pay an LLM call).

    Sequential per chunk (v1). Parallelising the per-chunk extract+verify calls
    is a reasonable follow-up once this runs against large real documents (each
    chunk is independent until the cross-chunk routing pass), but is not built
    here to keep the first version simple and its failure modes obvious.
    """
    reconcile_meta: dict = {}
    if blocks is not None:
        chunks = reconcile_segmentation(blocks, chunks, reconcile_meta)

    results: list[ExtractionResult] = []
    consecutive_backend_failures = 0
    for chunk in chunks:
        result = extract_question(chunk)

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
            if (
                len(results) == consecutive_backend_failures
                and consecutive_backend_failures >= _CIRCUIT_BREAKER_CHUNKS
            ):
                err = (result.question.validation or {}).get(_EXTRACTION_ERROR_KEY, {})
                detail = err.get("error_detail", "backend unavailable")
                raise BackendUnavailable(
                    f"extraction aborted after {consecutive_backend_failures} consecutive "
                    f"backend failures (circuit breaker): {detail}",
                    config_error=bool(
                        err.get("error_type") == BackendUnavailable.__name__
                        and schema.FLAG_EXTRACTION_CONFIG_ERROR in result.flags
                    ),
                )
            continue

        # Extraction succeeded (or failed in a non-backend way, e.g. MODEL_ERROR):
        # the backend is reachable, so reset the consecutive-failure streak and
        # run the grounded verify pass as normal.
        consecutive_backend_failures = 0
        result = verify_question(chunk, result)
        results.append(result)

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

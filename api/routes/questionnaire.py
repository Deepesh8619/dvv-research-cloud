"""Questionnaire ingestion + review endpoints — scoped to ONE study.

Wires the deterministic loader (src/intake/questionnaire_loader.py) and the LLM
extractor (src/engines/questionnaire/extractor.py) behind an upload endpoint,
then exposes the resulting `SurveyDefinition` for human review and correction.

Storage convention (T17): the full nested SurveyDefinition is persisted as a
JSON string in the `definition_json` TEXT column on the QuestionnaireDB row —
durable across Render deploys/restarts (its free tier wipes /app/studies, where
this JSON previously lived). The dataclass in src/core/survey_definition.py is
the source of truth for the instrument's structure — it is never re-modelled as
SQL. `definition_path` is now optional/vestigial: an ephemeral on-disk copy is
still written for local-dev convenience, but the column is authoritative. On
read, a row whose column is empty but whose `definition_path` file still exists
is backfilled from that file (so questionnaires already in flight at deploy time
are not lost).

Roles (api/auth.py): owner/analyst may upload and edit; reviewer is read-only
(GET allowed; upload/PATCH/approve rejected with 403).
"""

import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from api.auth import UserRole, get_current_user, require_role
from api.config import STUDIES_DIR
from api.db import (
    UserDB,
    create_questionnaire,
    get_db,
    get_study,
    list_questionnaires_for_study,
    set_questionnaire_approved_qids,
    set_questionnaire_definition,
    update_questionnaire_status,
)
from api.models import (
    QuestionCreateRequest,
    QuestionnaireCreateRequest,
    QuestionnaireSettingsRequest,
    QuestionnaireStatusResponse,
    QuestionnaireUploadResponse,
    QuestionOrderRequest,
    QuestionUpdateRequest,
)
from src.ai.router import friendly_backend_error
from src.core.survey_definition import (
    THEME_SLUGS,
    Block,
    Expression,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
)
from src.engines.questionnaire.extractor import extract_survey_definition
from src.intake.questionnaire_loader import (
    load_docx,
    load_pdf,
    segment_into_questions,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# Flags that must be clear on every question before a questionnaire can be
# approved as a whole — these mark routing/extraction the pipeline could not
# resolve on its own and that a human must have looked at.
def _is_reviewable(question) -> bool:
    """True when a reviewer has something to decide about this item.

    Display text ("Thank you for taking part") and scripter-computed variables
    carry no wording, type or routing to check, so counting them as
    unapproved questions turns the review queue into noise.
    """
    from src.core.survey_definition import QuestionType

    return question.type is not QuestionType.INFO


_BLOCKING_FLAGS = frozenset(
    {
        "AMBIGUOUS_ROUTING",
        "UNRESOLVED_ROUTING_QID",
        "EXTRACTION_UNAVAILABLE",
        # T16: a config- or model-failed extraction is an unreviewed placeholder
        # question and must block whole-questionnaire approval too.
        "EXTRACTION_CONFIG_ERROR",
        "EXTRACTION_MODEL_ERROR",
    }
)

# How long a questionnaire may sit on 'processing' before the status endpoint
# reports it as an error (T18 staleness guard). Render's free tier restarts
# routinely kill the daemon extraction thread mid-run, and the row's `error`
# flip only fires on an *in-process* exception — a killed thread leaves the row
# stuck on 'processing' with no feedback forever. This is a read-time
# computation (created_at/updated_at vs. now), not a background sweep — no
# scheduler, no new infrastructure.
#
# The original 15 minutes assumed "even ~80 chunks x 2 sequential LLM calls
# complete in a few minutes". That stopped being true once rate limits became
# the binding constraint: on a free tier capped at 8k tokens/minute a real
# 75-question document spends most of its time waiting, and a HEALTHY run was
# being reported as an error at minute 15.
#
# The window is now measured against `updated_at`, which the extractor
# heartbeats every few questions — so this asks "has it made progress
# recently?" rather than "has it been going a long time?". A genuinely killed
# worker still stops updating and is caught; a slow one is not punished for
# being slow.
# 15 minutes: the worst realistic gap between two heartbeats. A single
# question can burn several retries across several backends before it either
# succeeds or gives up, and each retry honours the provider's own wait hint.
# Anything shorter risks reporting a slow-but-alive run as dead, which is the
# failure this guard has now caused twice.
_PROCESSING_STALE_AFTER = timedelta(minutes=15)


def _is_stale_processing(questionnaire) -> bool:
    """True if a 'processing' row has not advanced within the staleness window.

    Compares the row's `updated_at` (falling back to `created_at`) against now.
    A malformed/absent timestamp is treated as not-stale — we never want a
    parse error to mask a genuinely still-running extraction as failed."""
    if questionnaire.status != "processing":
        return False
    stamp = questionnaire.updated_at or questionnaire.created_at
    if not stamp:
        return False
    try:
        started = datetime.fromisoformat(stamp)
    except (ValueError, TypeError):
        return False
    return datetime.now() - started > _PROCESSING_STALE_AFTER


# --- Storage helpers --------------------------------------------------------


def _questionnaire_dir(study_id: str) -> Path:
    """Where a study's questionnaire JSON blobs live (created on demand).

    Under the study's upload directory (STUDIES_DIR/{study_id}), not the
    orchestrator run dir — a questionnaire can be uploaded before any SPSS/CSV
    data exists for the study, so it must not depend on excel_path being set.
    """
    path = STUDIES_DIR / study_id / "questionnaires"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _definition_path(study_id: str, questionnaire_id: str) -> Path:
    return _questionnaire_dir(study_id) / f"{questionnaire_id}.json"


def _read_definition_json(db: Session, questionnaire) -> str | None:
    """Return the authoritative SurveyDefinition JSON string, or None.

    Reads the durable `definition_json` column. Backward-compat migration
    (T17): if the column is empty but `definition_path` still points at a file
    on disk (e.g. right after this deploys, before a restart wipes /app/studies),
    read the file and backfill the column on this read so in-flight review labor
    is not lost. `definition_path` is otherwise vestigial.
    """
    if questionnaire.definition_json:
        return questionnaire.definition_json
    if questionnaire.definition_path:
        path = Path(questionnaire.definition_path)
        if path.exists():
            payload = path.read_text()
            set_questionnaire_definition(db, questionnaire.id, payload)
            return payload
    return None


def _load_definition(db: Session, questionnaire) -> SurveyDefinition:
    """Load the SurveyDefinition for a questionnaire row, or 409/500.

    409 while still processing (nothing persisted yet); a row that reports a
    ready status but has neither a column value nor a backfillable file is a
    500-worthy server fault.
    """
    payload = _read_definition_json(db, questionnaire)
    if payload is None:
        if not questionnaire.definition_path and not questionnaire.definition_json:
            raise HTTPException(
                status_code=409,
                detail=f"Questionnaire is still {questionnaire.status}; no definition yet",
            )
        raise HTTPException(status_code=500, detail="Questionnaire definition missing")
    return SurveyDefinition.from_json(payload)


def _save_definition(db: Session, questionnaire, survey: SurveyDefinition) -> None:
    """Persist the SurveyDefinition to the durable column (authoritative).

    Also refreshes the ephemeral on-disk copy when a path is set, purely as
    local-dev convenience — the column is the source of truth.
    """
    payload = survey.to_json()
    set_questionnaire_definition(db, questionnaire.id, payload)
    if questionnaire.definition_path:
        # Best-effort only: on Render the path sits on the ephemeral disk and a
        # deploy wipes its parent directory — an unguarded write_text() here
        # 500'd every question-save after a restart (surfacing in the browser
        # as an opaque "Network Error", since unhandled crashes also lose their
        # CORS headers). The durable column write above already succeeded; a
        # failed file echo must never fail the request.
        try:
            path = Path(questionnaire.definition_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(payload)
        except OSError as exc:
            logger.warning(
                f"Could not refresh ephemeral definition file "
                f"{questionnaire.definition_path}: {exc}"
            )


# --- Serialization helpers --------------------------------------------------


def _status_response(questionnaire, survey: SurveyDefinition | None) -> QuestionnaireStatusResponse:
    """Build the status/summary payload; survey is None while still processing."""
    metadata = survey.metadata if survey else {}
    return QuestionnaireStatusResponse(
        id=questionnaire.id,
        study_id=questionnaire.study_id,
        status=questionnaire.status,
        source_filename=questionnaire.source_filename,
        version=questionnaire.version,
        created_at=questionnaire.created_at,
        updated_at=questionnaire.updated_at,
        title=survey.title if survey else None,
        n_questions=len(survey.questions) if survey else 0,
        # Only items a reviewer can act on. Counting display text and
        # scripter-computed variables made the denominator unreachable and the
        # progress meaningless.
        n_reviewable=(
            sum(1 for q in survey.questions if _is_reviewable(q)) if survey else 0
        ),
        n_approved=len(questionnaire.approved_qids or []),
        source=survey.source if survey else None,
        validation_errors=metadata.get("validation_errors", []),
        extraction_summary=metadata.get("extraction_summary", {}),
        # Only meaningful when status == 'error'; None otherwise. Names the root
        # cause (missing key, circuit-breaker abort) instead of a bare 'error'.
        error_detail=questionnaire.error_detail if questionnaire.status == "error" else None,
        theme=metadata.get("theme"),
        profile=metadata.get("profile"),
    )


def _question_payload(question, approved_qids: set[str]) -> dict:
    """Full per-question payload for the review UI — every field a reviewer
    needs (source_text, confidence, flags) is included, nothing trimmed.

    Surfaces the per-chunk failure diagnostic (T16): `extraction_error` /
    `verification_error`, each {"error_type", "error_detail"}, are stashed on
    Question.validation by the extractor and already ride along in to_dict()'s
    `validation`. Also lifted to the top level as `extraction_error` /
    `verification_error` so the review UI doesn't have to dig into validation."""
    data = question.to_dict()
    data["approved"] = question.qid in approved_qids
    validation = question.validation or {}
    data["extraction_error"] = validation.get("extraction_error")
    data["verification_error"] = validation.get("verification_error")
    return data


# --- Background extraction --------------------------------------------------


def _run_extraction(
    questionnaire_id: str,
    study_id: str,
    file_path: Path,
    definition_path: Path,
    source_filename: str,
) -> None:
    """Load -> segment -> extract, persist the SurveyDefinition, flip to draft.

    Runs on a daemon thread (matches studies.py's upload background pattern) so
    the HTTP response returns immediately rather than blocking on per-chunk LLM
    calls. Uses a fresh DB session — the request's session is already closed.
    Any failure flips the questionnaire to 'error' with a logged reason rather
    than leaving it stuck on 'processing' forever.
    """
    from api.db import (
        SessionLocal,
        active_patterns_for_profile,
        active_rules_for_profile,
        set_questionnaire_definition,
        touch_questionnaire,
    )

    job_db = SessionLocal()
    try:
        # Stage logs, not debug scaffolding: until these existed the worker was
        # silent from "uploaded" until its first extracted question, so a stall
        # anywhere in load/segment/detect was indistinguishable from a thread
        # that never started — and the only symptom was the 15-minute watchdog
        # reporting "the worker was likely killed by a restart".
        logger.info(f"[{study_id}] extraction thread started for {questionnaire_id}")
        suffix = file_path.suffix.lower()
        if suffix == ".docx":
            blocks = load_docx(file_path)
        else:  # .pdf — the only other accepted type (validated at upload)
            blocks = load_pdf(file_path)

        chunks = segment_into_questions(blocks)
        logger.info(
            f"[{study_id}] loaded {len(blocks)} blocks -> {len(chunks)} question chunks"
        )
        # Founder-taught rules for whichever profile this document turns out to
        # be (correction loop). Passed as a provider because only detection —
        # inside the extractor — knows the slug. This is the line that makes a
        # correction compound: a rule approved on an earlier GBK document is
        # applied to THIS one automatically, at upload, with no code change.
        # Heartbeat every few questions. A long extraction is minutes of
        # rate-limit waiting, and a restart, a deploy or an idle spin-down kills
        # the thread — a real 75-question run died at 15 minutes having
        # persisted NOTHING. Touching updated_at makes progress visible to the
        # staleness check, so "slow but working" stops looking like "dead".
        def _heartbeat(done: int, total: int) -> None:
            # EVERY question, not every fifth. A question can take minutes on a
            # rate-limited tier, so a 5-question cadence could exceed the
            # staleness window on its own — which is exactly what reported a
            # healthy run as dead. One UPDATE is nothing beside an LLM call that
            # takes tens of seconds; batching it was a premature optimisation.
            try:
                touch_questionnaire(job_db, questionnaire_id)
                logger.info(f"[{study_id}] extraction progress {done}/{total}")
            except Exception as beat_exc:  # noqa: BLE001
                # Never let bookkeeping kill the extraction it is measuring.
                logger.warning(f"[{study_id}] progress heartbeat failed: {beat_exc}")

        def _checkpoint(done: int, total: int, partial) -> None:
            """Persist the questions done so far.

            The row stays on 'processing' — this is a crash-safety snapshot, not
            a finished extraction. If the worker is killed (restart, deploy,
            idle spin-down), a reviewer still finds the questions completed up
            to that point instead of an empty questionnaire.
            """
            set_questionnaire_definition(job_db, questionnaire_id, partial.to_json())
            logger.info(f"[{study_id}] checkpoint saved {done}/{total}")

        logger.info(f"[{study_id}] extracting {len(chunks)} chunks")
        survey = extract_survey_definition(
            chunks,
            study_id=study_id,
            blocks=blocks,
            rules_for_profile=lambda slug: active_rules_for_profile(job_db, slug),
            # The machine-applicable half of the same corrections. This is the
            # line that makes a correction CHEAPER as well as more accurate:
            # a convention taught on an earlier document is now read by the
            # deterministic parsers here, with no model call at all.
            patterns_for_profile=lambda slug: active_patterns_for_profile(job_db, slug),
            on_progress=_heartbeat,
            on_checkpoint=_checkpoint,
        )
        # Title from the original upload name, not the on-disk path (which is
        # renamed to the questionnaire id to avoid collisions).
        survey.title = Path(source_filename).stem

        payload = survey.to_json()
        # definition_json is authoritative + durable; the on-disk copy is an
        # ephemeral local-dev convenience (wiped on Render free-tier restarts).
        try:
            definition_path.write_text(payload)
        except OSError as write_exc:
            logger.warning(
                f"[{study_id}] Could not write ephemeral definition file "
                f"{definition_path}: {write_exc}"
            )
        update_questionnaire_status(
            job_db,
            questionnaire_id,
            "draft",
            definition_json=payload,
            definition_path=str(definition_path),
        )
        # Exception-based review: pre-approve the clean questions (computed in the
        # extractor, where per-result `verified` is in scope) so the reviewer is
        # left with only the flagged/low-confidence exceptions. Status stays
        # 'draft'; approve-all still gates on the remainder.
        auto_approved = survey.metadata.get("auto_approved_qids", [])
        if auto_approved:
            set_questionnaire_approved_qids(job_db, questionnaire_id, auto_approved)
        logger.info(
            f"[{study_id}] Questionnaire {questionnaire_id} extracted: "
            f"{len(survey.questions)} questions, {len(auto_approved)} auto-approved"
        )
    except Exception as exc:
        logger.error(
            f"[{study_id}] Questionnaire {questionnaire_id} extraction failed: {exc}",
            exc_info=True,
        )
        # Preserve the exception detail (e.g. the extractor circuit-breaker's
        # message naming a missing ANTHROPIC_API_KEY) on the row so the status
        # endpoint reports the real root cause instead of an opaque 'error' (T16).
        update_questionnaire_status(
            job_db,
            questionnaire_id,
            "error",
            # A researcher sees this on the upload screen, so it has to be
            # readable by someone who does not run the deployment. The raw
            # chain summary is still in the server log above.
            error_detail=friendly_backend_error(str(exc)),
        )
    finally:
        job_db.close()


# --- Endpoints --------------------------------------------------------------


@router.post(
    "/studies/{study_id}/questionnaire/upload",
    response_model=QuestionnaireUploadResponse,
)
async def upload_questionnaire(
    study_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Upload a .docx/.pdf questionnaire and start extraction. (Owner/Analyst)"""
    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")

    if not file.filename or not file.filename.lower().endswith((".docx", ".pdf")):
        raise HTTPException(
            status_code=400, detail="Only .docx and .pdf questionnaires are supported"
        )

    questionnaire = create_questionnaire(db, study_id, source_filename=file.filename)

    # Stream to disk in chunks — never hold the whole upload in memory (mirrors
    # studies.py's upload endpoint).
    upload_dir = _questionnaire_dir(study_id)
    file_path = upload_dir / f"{questionnaire.id}{Path(file.filename).suffix.lower()}"
    chunk_size = 1024 * 1024  # 1 MB
    with open(file_path, "wb") as f:
        while chunk := await file.read(chunk_size):
            f.write(chunk)
    logger.info(f"[{study_id}] Questionnaire {questionnaire.id} uploaded: {file_path}")

    definition_path = _definition_path(study_id, questionnaire.id)
    thread = threading.Thread(
        target=_run_extraction,
        args=(questionnaire.id, study_id, file_path, definition_path, file.filename),
        daemon=True,
    )
    thread.start()

    return QuestionnaireUploadResponse(
        questionnaire_id=questionnaire.id,
        study_id=study_id,
        status="processing",
        message=f"Questionnaire {file.filename} uploaded. Extraction started.",
    )


@router.post(
    "/studies/{study_id}/questionnaire/authored",
    response_model=QuestionnaireStatusResponse,
)
async def create_authored_questionnaire(
    study_id: str,
    request: QuestionnaireCreateRequest,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Create a blank authored questionnaire from scratch. (Owner/Analyst)

    Lands directly in 'draft' with one empty block and zero questions — from here
    the same add/edit/approve/field lifecycle an *extracted* survey follows
    applies unchanged (the T12 unification). 409 if the study already has a live
    questionnaire (matches `_latest_questionnaire`'s latest-wins assumption — we
    refuse to shadow an in-flight one; an errored one may be replaced)."""
    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")

    existing = list_questionnaires_for_study(db, study_id)
    if any(q.status != "error" for q in existing):
        raise HTTPException(
            status_code=409,
            detail="Study already has a questionnaire",
        )

    title = request.title.strip() or "Untitled survey"
    questionnaire = create_questionnaire(db, study_id, source_filename=title)
    survey = SurveyDefinition(
        study_id=study_id,
        title=title,
        source="authored",
        status="draft",
        blocks=[Block(block_id="B1", label="", question_ids=[])],
        questions=[],
    )
    survey.metadata["validation_errors"] = survey.validate()
    update_questionnaire_status(
        db, questionnaire.id, "draft", definition_json=survey.to_json()
    )
    db.refresh(questionnaire)
    logger.info(f"[{study_id}] Authored questionnaire {questionnaire.id} created by {user.name}")
    return _status_response(questionnaire, survey)


def _latest_questionnaire(db: Session, study_id: str):
    """Resolve a study's current questionnaire (most recent), or 404."""
    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")
    questionnaires = list_questionnaires_for_study(db, study_id)
    if not questionnaires:
        raise HTTPException(status_code=404, detail="No questionnaire for this study")
    return questionnaires[0]


@router.get(
    "/studies/{study_id}/questionnaire",
    response_model=QuestionnaireStatusResponse,
)
async def get_questionnaire_status(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """Current questionnaire status + extraction summary. (Authenticated)

    A row stuck on 'processing' past the staleness window (its extraction thread
    was almost certainly killed by a Render restart) is reported as 'error' with
    an explaining `error_detail`, computed read-time here rather than swept by a
    background job."""
    questionnaire = _latest_questionnaire(db, study_id)
    survey = None
    payload = _read_definition_json(db, questionnaire)
    if payload is not None:
        survey = SurveyDefinition.from_json(payload)
    response = _status_response(questionnaire, survey)
    if _is_stale_processing(questionnaire):
        response.status = "error"
        response.error_detail = (
            "Extraction did not complete within "
            f"{int(_PROCESSING_STALE_AFTER.total_seconds() // 60)} minutes "
            "(the background worker was likely killed by a restart). Re-upload "
            "the questionnaire to try again."
        )
    return response


@router.patch(
    "/studies/{study_id}/questionnaire/settings",
    response_model=QuestionnaireStatusResponse,
)
async def update_questionnaire_settings(
    study_id: str,
    request: QuestionnaireSettingsRequest,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Set survey-level presentation settings — currently the respondent theme
    slug (T24). (Owner/Analyst)

    Writes `metadata["theme"]` into the questionnaire's own definition, so every
    link opened afterwards inherits it as the default (a link may still override
    at creation). An explicit null clears it. 422 on an unknown slug."""
    if request.theme is not None and request.theme not in THEME_SLUGS:
        raise HTTPException(status_code=422, detail=f"Unknown theme slug: {request.theme}")
    questionnaire = _latest_questionnaire(db, study_id)
    survey = _load_definition(db, questionnaire)
    if request.theme is None:
        survey.metadata.pop("theme", None)
    else:
        survey.metadata["theme"] = request.theme
    _save_definition(db, questionnaire, survey)
    return _status_response(questionnaire, survey)


@router.get("/studies/{study_id}/questionnaire/questions")
async def list_questions(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """Full list of extracted questions with provenance/confidence/flags."""
    questionnaire = _latest_questionnaire(db, study_id)
    survey = _load_definition(db, questionnaire)
    approved = set(questionnaire.approved_qids or [])
    return {
        "questionnaire_id": questionnaire.id,
        "study_id": study_id,
        "status": questionnaire.status,
        "questions": [_question_payload(q, approved) for q in survey.questions],
    }


@router.get("/studies/{study_id}/questionnaire/questions/{qid}")
async def get_question(
    study_id: str,
    qid: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """Single question detail."""
    questionnaire = _latest_questionnaire(db, study_id)
    survey = _load_definition(db, questionnaire)
    if qid not in survey:
        raise HTTPException(status_code=404, detail=f"Question {qid} not found")
    approved = set(questionnaire.approved_qids or [])
    return _question_payload(survey[qid], approved)


def _rename_reference(expr, old_qid: str, new_qid: str) -> None:
    """Rewrite every `old_qid` reference to `new_qid` in an Expression tree.

    Mutates in place. The Expression AST is a plain, fully-owned dataclass tree
    (no shared/interned nodes, no eval), so in-place rewrite is safe: we walk
    `children` recursively and reassign `qid` on any leaf that matched. This is
    preferred over rejecting the rename — a rename that left a dangling reference
    to the old qid would be silently broken routing, which is unacceptable."""
    if expr.qid == old_qid:
        expr.qid = new_qid
    for child in expr.children or []:
        _rename_reference(child, old_qid, new_qid)


def _rename_qid(
    survey: SurveyDefinition, old_qid: str, new_qid: str, approved_qids: set[str]
) -> None:
    """Rename a question's qid consistently across the whole instrument.

    Updates: the Question.qid itself, every Block.question_ids list containing
    the old qid, the in-memory `approved_qids` set (caller persists it), and any
    OTHER question's show_if/terminate_if Expression referencing the old qid.
    Rebuilds the survey index afterwards so subsequent lookups resolve correctly.
    Caller must have already validated that `new_qid` is not a duplicate."""
    survey[old_qid].qid = new_qid

    for block in survey.blocks:
        block.question_ids = [
            new_qid if qid == old_qid else qid for qid in block.question_ids
        ]

    for q in survey.questions:
        for expr in (q.show_if, q.terminate_if):
            if expr is not None and old_qid in expr.referenced_qids():
                _rename_reference(expr, old_qid, new_qid)

    if old_qid in approved_qids:
        approved_qids.discard(old_qid)
        approved_qids.add(new_qid)

    survey._rebuild_index()


def _build_options(raw: list[dict]) -> list[Option]:
    """Build Option list from raw {code,label,...} dicts (shared by edit + add)."""
    return [
        Option(
            code=int(o["code"]),
            label=str(o["label"]),
            **{k: o[k] for k in ("exclusive", "anchored", "open_other") if k in o},
        )
        for o in raw
    ]


def _apply_update(question, request: QuestionUpdateRequest) -> None:
    """Mutate a Question in place from a partial update request.

    Only fields explicitly present in the request body are touched (via
    `model_fields_set`), so an omitted `show_if` is left alone while an explicit
    `show_if: null` clears it. Flags are deliberately NOT cleared on edit: they
    stay so the reviewer still sees why the question was flagged. Per-question
    approval is the separate signal that a human has resolved it.
    """
    provided = request.model_fields_set

    if "text" in provided and request.text is not None:
        question.text = request.text
    if "type" in provided and request.type is not None:
        question.type = QuestionType(request.type)
    if "options" in provided and request.options is not None:
        question.options = _build_options(request.options)
    if "rows" in provided:
        question.rows = _build_options(request.rows) if request.rows is not None else None
    if "cols" in provided:
        question.cols = _build_options(request.cols) if request.cols is not None else None
    if "required" in provided and request.required is not None:
        question.required = request.required
    if "randomize_options" in provided and request.randomize_options is not None:
        question.randomize_options = request.randomize_options
    if "show_if" in provided:
        question.show_if = Expression.from_dict(request.show_if) if request.show_if else None
    if "terminate_if" in provided:
        question.terminate_if = (
            Expression.from_dict(request.terminate_if) if request.terminate_if else None
        )


@router.patch("/studies/{study_id}/questionnaire/questions/{qid}")
async def update_question(
    study_id: str,
    qid: str,
    request: QuestionUpdateRequest,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Correct one extracted question. (Owner/Analyst)

    Delta validation (T18): the edit is rejected (422) only if it introduces a
    *new* problem — a problem in `validate()` after the edit that was not present
    before it. A document with a pre-existing problem (e.g. a real screener's
    `duplicate qid: S6`) can therefore still be edited: unrelated edits, and even
    edits to S6 itself, go through as long as they don't make things worse. This
    is what unblocks repairing a duplicate-qid extraction — the whole-survey
    re-validation used to 422 on the pre-existing duplicate for every edit.

    A `qid` rename is honoured here (the only way to actually resolve a duplicate
    qid): block membership, the approved-qids set, and any other question's
    routing that referenced the old qid are all rewritten consistently."""
    questionnaire = _latest_questionnaire(db, study_id)
    survey = _load_definition(db, questionnaire)
    if qid not in survey:
        raise HTTPException(status_code=404, detail=f"Question {qid} not found")

    problems_before = survey.validate()
    approved = set(questionnaire.approved_qids or [])

    # qid rename is applied first (and separately) because it mutates the whole
    # survey, not just this question, and shifts which qid the payload key maps to.
    provided = request.model_fields_set
    new_qid = qid
    if "qid" in provided and request.qid is not None and request.qid != qid:
        new_qid = request.qid
        # A rename onto an existing DISTINCT question would collide two questions
        # under one qid — reject; the reviewer must rename to a free qid.
        clash = sum(1 for q in survey.questions if q.qid == new_qid)
        if clash:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "Cannot rename qid — target already in use",
                    "problems": [f"qid {new_qid} is already used by another question"],
                },
            )
        _rename_qid(survey, qid, new_qid, approved)

    try:
        _apply_update(survey[new_qid], request)
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid update: {exc}") from exc

    problems_after = survey.validate()
    new_problems = [p for p in problems_after if p not in problems_before]
    if new_problems:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "Edit would introduce new validation problems",
                "problems": new_problems,
            },
        )

    survey.metadata["validation_errors"] = problems_after
    _save_definition(db, questionnaire, survey)
    if new_qid != qid:
        set_questionnaire_approved_qids(db, questionnaire.id, sorted(approved))
    update_questionnaire_status(db, questionnaire.id, "in_review")
    return _question_payload(survey[new_qid], approved)


@router.post("/studies/{study_id}/questionnaire/questions")
async def add_question(
    study_id: str,
    request: QuestionCreateRequest,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Append a new question to the questionnaire. (Owner/Analyst)

    The extraction path always pre-populated questions, so PATCH only *edits*
    existing ones — this is the authoring builder's create path (T12). Delta-
    validated exactly like PATCH: a qid that duplicates an existing one, or
    routing that references an unknown/forward qid, surfaces as a NEW problem and
    is rejected 422. The question lands at the end of both the question list and
    the last block's order."""
    questionnaire = _latest_questionnaire(db, study_id)
    survey = _load_definition(db, questionnaire)

    problems_before = survey.validate()

    try:
        question = Question(
            qid=request.qid,
            text=request.text,
            type=QuestionType(request.type),
            options=_build_options(request.options),
            rows=_build_options(request.rows) if request.rows is not None else None,
            cols=_build_options(request.cols) if request.cols is not None else None,
            required=request.required,
            randomize_options=request.randomize_options,
            show_if=Expression.from_dict(request.show_if) if request.show_if else None,
            terminate_if=Expression.from_dict(request.terminate_if)
            if request.terminate_if
            else None,
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid question: {exc}") from exc

    survey.questions.append(question)
    if not survey.blocks:
        survey.blocks.append(Block(block_id="B1", question_ids=[]))
    survey.blocks[-1].question_ids.append(question.qid)
    survey._rebuild_index()

    problems_after = survey.validate()
    new_problems = [p for p in problems_after if p not in problems_before]
    if new_problems:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "Adding this question would introduce validation problems",
                "problems": new_problems,
            },
        )

    survey.metadata["validation_errors"] = problems_after
    _save_definition(db, questionnaire, survey)
    update_questionnaire_status(db, questionnaire.id, "in_review")
    approved = set(questionnaire.approved_qids or [])
    return _question_payload(question, approved)


@router.delete("/studies/{study_id}/questionnaire/questions/{qid}")
async def delete_question(
    study_id: str,
    qid: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Remove a question. (Owner/Analyst)

    Rejected 422 if another question's show_if/terminate_if references it —
    deleting it would leave a dangling routing reference. The response names the
    referencing qids so the reviewer can fix them first."""
    questionnaire = _latest_questionnaire(db, study_id)
    survey = _load_definition(db, questionnaire)
    if qid not in survey:
        raise HTTPException(status_code=404, detail=f"Question {qid} not found")

    referencers = [
        q.qid
        for q in survey.questions
        if q.qid != qid
        for expr in (q.show_if, q.terminate_if)
        if expr is not None and qid in expr.referenced_qids()
    ]
    if referencers:
        raise HTTPException(
            status_code=422,
            detail={
                "error": f"Cannot delete {qid} — it is referenced by other questions' routing",
                "referenced_by": sorted(set(referencers)),
            },
        )

    survey.questions = [q for q in survey.questions if q.qid != qid]
    for block in survey.blocks:
        block.question_ids = [q for q in block.question_ids if q != qid]
    survey._rebuild_index()
    survey.metadata["validation_errors"] = survey.validate()
    _save_definition(db, questionnaire, survey)

    approved = set(questionnaire.approved_qids or [])
    if qid in approved:
        approved.discard(qid)
        set_questionnaire_approved_qids(db, questionnaire.id, sorted(approved))
    return {"deleted": qid, "n_questions": len(survey.questions)}


@router.put("/studies/{study_id}/questionnaire/questions/order")
async def reorder_questions(
    study_id: str,
    request: QuestionOrderRequest,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Reorder the whole question list. (Owner/Analyst)

    `qids` must be a permutation of every existing qid. Both the question list
    and each block's order are rewritten to follow it, then delta-validated —
    an order that moves a question ahead of one its routing references (a forward
    reference) is rejected 422."""
    questionnaire = _latest_questionnaire(db, study_id)
    survey = _load_definition(db, questionnaire)

    current = {q.qid for q in survey.questions}
    requested = list(request.qids)
    if set(requested) != current or len(requested) != len(current):
        raise HTTPException(
            status_code=422,
            detail={
                "error": "Order must be a permutation of the existing qids",
                "expected": sorted(current),
            },
        )

    problems_before = survey.validate()
    rank = {qid: i for i, qid in enumerate(requested)}
    survey.questions.sort(key=lambda q: rank[q.qid])
    for block in survey.blocks:
        members = set(block.question_ids)
        block.question_ids = [qid for qid in requested if qid in members]
    survey._rebuild_index()

    problems_after = survey.validate()
    new_problems = [p for p in problems_after if p not in problems_before]
    if new_problems:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "This order would introduce validation problems",
                "problems": new_problems,
            },
        )

    survey.metadata["validation_errors"] = problems_after
    _save_definition(db, questionnaire, survey)
    return {"order": survey.question_order()}


@router.post("/studies/{study_id}/questionnaire/questions/{qid}/approve")
async def approve_question(
    study_id: str,
    qid: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Mark one question as reviewer-approved. (Owner/Analyst)

    Individual approval also *clears that question's blocking flags* (T18): a
    human explicitly confirming the question is now correct is exactly what a
    blocking flag was waiting for. Without this, a flag like EXTRACTION_UNAVAILABLE
    or AMBIGUOUS_ROUTING could be hand-fixed and approved yet still block
    `approve-all` forever, since flags were otherwise never cleared. Non-blocking
    flags are left intact (they're informational, not gates). The cleared flags
    are persisted on the definition so the resolution survives a reload."""
    questionnaire = _latest_questionnaire(db, study_id)
    survey = _load_definition(db, questionnaire)
    if qid not in survey:
        raise HTTPException(status_code=404, detail=f"Question {qid} not found")

    # EVERY question carrying this qid, not just the first. Extraction now
    # de-duplicates qids, but a questionnaire extracted BEFORE that fix can
    # still hold duplicates — and `survey[qid]` returns only the first match, so
    # the second copy's blocking flag could never be cleared and the
    # questionnaire could never be finalised. A reviewer hit exactly that dead
    # end: "81/81 approved" beside "cannot approve, S6 unresolved".
    matching = [q for q in survey.questions if q.qid == qid]
    cleared = sorted({f for q in matching for f in q.flags if f in _BLOCKING_FLAGS})
    if cleared:
        for q in matching:
            q.flags = [f for f in q.flags if f not in _BLOCKING_FLAGS]
        _save_definition(db, questionnaire, survey)

    approved = set(questionnaire.approved_qids or [])
    approved.add(qid)
    set_questionnaire_approved_qids(db, questionnaire.id, sorted(approved))
    if questionnaire.status == "draft":
        update_questionnaire_status(db, questionnaire.id, "in_review")
    return {
        "qid": qid,
        "approved": True,
        "n_approved": len(approved),
        "cleared_flags": cleared,
    }


@router.post("/studies/{study_id}/questionnaire/questions/approve-clean")
async def approve_clean(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Bulk-approve every question that is clean — not yet approved AND carrying
    no blocking flag — in one shot. (Owner/Analyst)

    This is the "approve all remaining" convenience: it does the per-question
    approve for the questions that need no human judgement, so the reviewer is
    left with only the flagged ones to look at by hand. It deliberately does NOT
    touch flagged questions (and therefore never clears a blocking flag — unlike
    the single-question `approve`, which clears flags precisely because a human
    is explicitly confirming that one). This keeps the approve-all gate honest:
    a flagged question still requires a deliberate individual approval.

    Returns how many were newly approved and which remain blocked (with their
    flags), so the UI can point the reviewer straight at the leftovers.
    """
    questionnaire = _latest_questionnaire(db, study_id)
    survey = _load_definition(db, questionnaire)
    approved = set(questionnaire.approved_qids or [])

    newly_approved: list[str] = []
    still_blocked: list[dict] = []
    for q in survey.questions:
        blocking = sorted(set(q.flags) & _BLOCKING_FLAGS)
        if blocking:
            if q.qid not in approved:
                still_blocked.append({"qid": q.qid, "flags": blocking})
            continue
        if q.qid not in approved:
            approved.add(q.qid)
            newly_approved.append(q.qid)

    if newly_approved:
        set_questionnaire_approved_qids(db, questionnaire.id, sorted(approved))
        if questionnaire.status == "draft":
            update_questionnaire_status(db, questionnaire.id, "in_review")

    return {
        "n_newly_approved": len(newly_approved),
        "newly_approved": newly_approved,
        "n_approved": len(approved),
        "still_blocked": still_blocked,
    }


@router.post("/studies/{study_id}/questionnaire/approve-all")
async def approve_all(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Approve the whole questionnaire — only if every question is individually
    approved AND carries no unresolved routing/extraction flag. Returns a clear
    error listing unresolved qids if the gate fails. (Owner/Analyst)"""
    questionnaire = _latest_questionnaire(db, study_id)
    survey = _load_definition(db, questionnaire)
    approved = set(questionnaire.approved_qids or [])

    unresolved: list[dict] = []
    for q in survey.questions:
        reasons = []
        # Display text and scripter-computed variables are not questions a
        # reviewer approves — there is no wording, type or routing to check.
        # Requiring an approval for each one buried the real review work and
        # made "0/35 approved" count things nobody can act on. A blocking flag
        # on one is still reported below.
        if q.qid not in approved and not _is_reviewable(q):
            pass
        elif q.qid not in approved:
            reasons.append("not approved")
        blocking = sorted(set(q.flags) & _BLOCKING_FLAGS)
        if blocking:
            reasons.append(f"unresolved flags: {', '.join(blocking)}")
        if reasons:
            unresolved.append({"qid": q.qid, "reasons": reasons})

    if unresolved:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "Cannot approve — some questions are unresolved",
                "unresolved": unresolved,
            },
        )

    survey.status = "approved"
    _save_definition(db, questionnaire, survey)
    update_questionnaire_status(db, questionnaire.id, "approved")
    logger.info(f"[{study_id}] Questionnaire {questionnaire.id} approved by {user.name}")

    # Label merge (§3.2's datamap-merge fix): an approved questionnaire carries
    # the real question/option wording that a CSV/Excel import lacks. If this
    # study's data is already processed, regenerate its tables/decks with those
    # labels in the background — same daemon-thread pattern as _run_extraction.
    # Best-effort: a merge failure must never un-approve the questionnaire.
    relabel = _start_label_merge(db, study_id, survey)

    return {
        "questionnaire_id": questionnaire.id,
        "status": "approved",
        "label_merge_started": relabel,
    }


def _start_label_merge(db: Session, study_id: str, survey: SurveyDefinition) -> bool:
    """Kick off the background re-tab with survey labels; False if not applicable."""
    from api.db import get_study
    from src.orchestration.orchestrator import StudyOrchestrator

    study = get_study(db, study_id)
    if not study or not study.excel_path:
        return False  # no processed data yet — nothing to relabel
    run_dir = Path(study.excel_path).parent
    excel_name = Path(study.excel_path).name
    pptx_name = Path(study.pptx_path).name if study.pptx_path else None
    client_name = study.client_name

    def _job() -> None:
        try:
            summary = StudyOrchestrator().retab_with_survey_labels(
                run_dir, client_name, survey, excel_name, pptx_name
            )
            logger.info(f"[{study_id}] label merge: {summary}")
            # Mirror the enriched datamap + regenerated tables into the durable
            # DB copies so the merged labels survive a restart.
            datamap_file = run_dir / "datamap.json"
            crosstabs_file = run_dir / "crosstabs.json"
            if summary.get("n_variables_relabelled") and datamap_file.exists():
                from api.db import SessionLocal, set_study_artifacts, update_study_paths

                job_db = SessionLocal()
                try:
                    update_study_paths(
                        job_db, study_id, datamap_json=datamap_file.read_text()
                    )
                    if crosstabs_file.exists():
                        set_study_artifacts(
                            job_db, study_id, crosstabs_json=crosstabs_file.read_text()
                        )
                finally:
                    job_db.close()
        except Exception as exc:
            logger.error(f"[{study_id}] label merge failed: {exc}", exc_info=True)

    threading.Thread(target=_job, daemon=True).start()
    return True

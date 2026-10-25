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
import os
import threading
from dataclasses import fields as dataclass_fields
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.auth import UserRole, get_current_user, require_role
from api.config import STUDIES_DIR
from api.db import (
    MAX_ARCHIVED_SOURCE_BYTES,
    UserDB,
    compress_for_archive,
    create_questionnaire,
    decompress_archived,
    get_db,
    get_study,
    list_questionnaires_for_study,
    set_questionnaire_approved_qids,
    set_questionnaire_definition,
    set_questionnaire_source_archive,
    update_questionnaire_status,
)
from api.models import (
    QuestionCreateRequest,
    QuestionLinksRequest,
    QuestionnaireCreateRequest,
    QuestionnaireSettingsRequest,
    QuestionnaireStatusResponse,
    QuestionnaireUploadResponse,
    QuestionOrderRequest,
    QuestionSplitRequest,
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
    has_nothing_to_answer,
)
from src.engines.fielding.materialize import plan_datamap
from src.engines.questionnaire.extraction_diagnosis import completeness_problems, diagnose
from src.engines.questionnaire.extractor import extract_survey_definition, reread_question
from src.engines.questionnaire.flag_reasons import flag_reasons
from src.engines.questionnaire.logic_review import review as review_logic
from src.engines.questionnaire.qa_loop import run_qa
from src.engines.questionnaire.question_agent import revise as question_agent_revise
from src.engines.questionnaire.question_agent import suggest_repairs
from src.engines.questionnaire.second_look import second_look
from src.engines.rag.store import ensure_index
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


_NOTHING_TO_ANSWER = (
    "{qid} gives a respondent nothing to answer. Add its answer options or rows, "
    "or change its type, then approve it."
)

_BLOCKING_FLAGS = frozenset(
    {
        "AMBIGUOUS_ROUTING",
        "UNRESOLVED_ROUTING_QID",
        "EXTRACTION_UNAVAILABLE",
        # T16: a config- or model-failed extraction is an unreviewed placeholder
        # question and must block whole-questionnaire approval too.
        "EXTRACTION_CONFIG_ERROR",
        "EXTRACTION_MODEL_ERROR",
        # A MaxDiff/conjoint block: the platform cannot field it. Blocking so
        # nobody finalises a questionnaire containing one by accident; clearable
        # by approving the question, which is a reviewer saying "I know, it is
        # being scripted elsewhere".
        "TRADE_OFF_UNSUPPORTED",
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


def _completeness(survey: SurveyDefinition) -> list[str]:
    """Document-level reasons this definition is not the whole questionnaire."""
    return completeness_problems(survey.questions, survey.metadata.get("document_markers"))


def _status_response(
    questionnaire,
    survey: SurveyDefinition | None,
    used_datamap: bool = False,
) -> QuestionnaireStatusResponse:
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
        # Whether the script's own record was available to this extraction.
        # Without it, a reviewer watching a slow run cannot tell one that is
        # being helped from one paying full price for every question — and the
        # difference is roughly half the model calls.
        used_datamap=used_datamap,
        n_recovered_from_datamap=len(metadata.get("recovered_from_datamap") or []),
        n_reviewable=(
            sum(1 for q in survey.questions if _is_reviewable(q)) if survey else 0
        ),
        n_approved=len(questionnaire.approved_qids or []),
        source=survey.source if survey else None,
        # Read fresh, not from the snapshot taken at extraction: a definition
        # stored before that snapshot moved still reports what is true NOW.
        validation_errors=(
            _completeness(survey) + survey.validate()
            if survey
            else metadata.get("validation_errors", [])
        ),
        extraction_summary=metadata.get("extraction_summary", {}),
        # Only meaningful when status == 'error'; None otherwise. Names the root
        # cause (missing key, circuit-breaker abort) instead of a bare 'error'.
        error_detail=questionnaire.error_detail if questionnaire.status == "error" else None,
        theme=metadata.get("theme"),
        profile=metadata.get("profile"),
        study_understanding=metadata.get("study_understanding"),
        qa=metadata.get("qa"),
        logic_review=metadata.get("logic_review"),
        decoding=metadata.get("decoding"),
        second_look=metadata.get("second_look"),
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
    # One sentence per flag naming this question and what was seen — computed
    # here, not stored, so an edit changes the reason with it.
    data["flag_reasons"] = flag_reasons(question)
    return data


# --- Background extraction --------------------------------------------------


def _study_has_datamap(db, study_id: str) -> bool:
    """Does this study already hold the data map? Cheap enough for a status poll."""
    try:
        study = get_study(db, study_id)
        return bool(study is not None and study.datamap_json)
    except Exception:  # noqa: BLE001
        return False


def _study_datamap(db, study_id: str):
    """The study's data map, if a data file has already been processed.

    Never raises: a questionnaire must still extract when no data exists yet,
    or when a stored data map cannot be parsed. Losing the acceleration is a
    cost; failing the upload is a defect.
    """
    from src.core.models import DataMap

    try:
        study = get_study(db, study_id)
        if study is None or not study.datamap_json:
            return None
        return DataMap.from_json(study.datamap_json)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[{study_id}] data map unavailable for extraction: {exc}")
        return None


# Extractions share one rate-limited model quota; ten at once slow each other
# and none finish. Extra uploads wait here (status stays "processing").
# ponytail: per-process; a shared job queue comes with multiple web workers.
_EXTRACTION_SLOTS = threading.BoundedSemaphore(int(os.getenv("DVV_MAX_EXTRACTIONS", "3")))


def _run_extraction_queued(*args) -> None:
    with _EXTRACTION_SLOTS:
        _run_extraction(*args)


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
            # The script's own record of what was fielded, when the data was
            # uploaded before the questionnaire. It answers a parser's open
            # questions without a model call, tells the reader which chunks are
            # computed variables nobody was asked, and supplies the questions
            # the reader never found at all. Measured on a real study: model
            # calls 56 -> 28. None when no data has been uploaded yet, and the
            # reader then behaves exactly as it did before.
            datamap=_study_datamap(job_db, study_id),
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
        # Exception-based review: pre-approve the clean questions (computed in the
        # extractor, where per-result `verified` is in scope) so the reviewer is
        # left with only the flagged/low-confidence exceptions. Status stays
        # 'draft'; approve-all still gates on the remainder.
        #
        # Written BEFORE the status flips to 'draft'. The review page polls for
        # the status leaving 'processing', so writing approvals afterwards left
        # a window where it saw "draft, 0 approved" and only later corrected
        # itself — a reviewer opening the page the instant extraction finished
        # got the wrong count. Order is the whole fix: once the status says
        # draft, everything that status implies is already true.
        auto_approved = survey.metadata.get("auto_approved_qids", [])
        if auto_approved:
            set_questionnaire_approved_qids(job_db, questionnaire_id, auto_approved)
        update_questionnaire_status(
            job_db,
            questionnaire_id,
            "draft",
            definition_json=payload,
            definition_path=str(definition_path),
        )
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
        # message naming a missing GROQ_API_KEY) on the row so the status
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

    # Keep the document itself, not only what we extracted from it. On the free
    # tier the on-disk copy dies with the next restart, and a reviewer asking
    # "which questionnaire is this wave running?" needs the file, not a name.
    try:
        archived = compress_for_archive(file_path)
        if archived:
            set_questionnaire_source_archive(db, questionnaire.id, archived)
        else:
            logger.info(
                f"[{study_id}] questionnaire not archived: over "
                f"{MAX_ARCHIVED_SOURCE_BYTES} bytes compressed"
            )
    except Exception as exc:  # never fail an upload over the archive copy
        logger.warning(f"[{study_id}] questionnaire archive failed (non-fatal): {exc}")

    definition_path = _definition_path(study_id, questionnaire.id)
    thread = threading.Thread(
        target=_run_extraction_queued,
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


@router.get("/studies/{study_id}/questionnaire/source")
def download_questionnaire_source(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """Download the questionnaire document as it was uploaded. (Authenticated)

    Served from the database copy, so it still works after the restart that
    wiped the disk — which is exactly when someone needs to see what the client
    actually sent.
    """
    questionnaire = _latest_questionnaire(db, study_id)
    if not questionnaire:
        raise HTTPException(
            status_code=404, detail="No questionnaire has been uploaded for this project"
        )
    if not questionnaire.source_file_b64:
        raise HTTPException(
            status_code=404,
            detail=(
                "This questionnaire was uploaded before documents were archived, "
                "and the on-disk copy is gone. Re-upload it to make it downloadable."
            ),
        )

    filename = questionnaire.source_filename or f"{questionnaire.id}.docx"
    media = (
        "application/pdf"
        if filename.lower().endswith(".pdf")
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    return Response(
        content=decompress_archived(questionnaire.source_file_b64),
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/studies/{study_id}/questionnaire/source/rendered")
def rendered_questionnaire_source(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """The document as the client wrote it, for reading beside the extraction.

    The parser keeps the meaning and drops the presentation, and the
    presentation is carrying information: a highlight after an option label is
    how these documents say "a hidden variable sits behind this one", and red
    strikethrough is this wave's edits to last wave's script. A reviewer
    comparing the two needs to see both.

    Anchored to the loader's own block indices, so a question whose provenance
    reads "para 47" scrolls to `#b47`.
    """
    questionnaire = _latest_questionnaire(db, study_id)
    if not questionnaire:
        raise HTTPException(
            status_code=404, detail="No questionnaire has been uploaded for this project"
        )
    if not questionnaire.source_file_b64:
        raise HTTPException(
            status_code=404,
            detail=(
                "This questionnaire was uploaded before documents were archived, "
                "so there is no copy to show. Re-upload it to read it here."
            ),
        )
    filename = questionnaire.source_filename or ""
    if filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=415,
            detail="Only Word documents can be read side by side; this one is a PDF.",
        )

    import tempfile
    from pathlib import Path as _Path

    from src.intake.document_html import render_docx

    with tempfile.TemporaryDirectory() as folder:
        path = _Path(folder) / "source.docx"
        path.write_bytes(decompress_archived(questionnaire.source_file_b64))
        try:
            rendered = render_docx(path)
        except Exception as exc:
            # A document python-docx cannot open is a fact about the file, not
            # a server fault — and the reviewer needs to know which.
            logger.warning("could not render %s for reading: %s", questionnaire.id, exc)
            raise HTTPException(
                status_code=422,
                detail="This document could not be opened for reading side by side.",
            ) from exc

    return {"filename": filename, **rendered.to_dict()}


@router.post(
    "/studies/{study_id}/questionnaire/authored",
    response_model=QuestionnaireStatusResponse,
)
def create_authored_questionnaire(
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
def get_questionnaire_status(
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
    response = _status_response(questionnaire, survey, _study_has_datamap(db, study_id))
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
def update_questionnaire_settings(
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
def list_questions(
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
def get_question(
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


_OPTION_FIELDS = {f.name for f in dataclass_fields(Option)}


def _build_options(raw: list[dict]) -> list[Option]:
    """Build Option list from raw {code,label,...} dicts (shared by edit + add).

    Every field an Option carries survives — exclusive, anchored, "other,
    specify", a per-answer condition, a hidden variable's derivation. Keeping
    only three of them meant an answer's own condition vanished on any edit.
    """
    return [
        Option.from_dict({
            **{k: v for k, v in o.items() if k in _OPTION_FIELDS},
            "code": int(o["code"]),
            "label": str(o["label"]),
        })
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
    if "validation" in provided:
        # Merged, not replaced: `validation` also carries `techniques` (masks,
        # pipes) and extraction diagnostics that an edit to the numeric range or
        # constant-sum total must not wipe. Explicit null is still a full clear.
        question.validation = (
            {**(question.validation or {}), **request.validation}
            if request.validation is not None
            else None
        )


@router.patch("/studies/{study_id}/questionnaire/questions/{qid}")
def update_question(
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

    # A person's save is a decision: the question is looked at again as it now
    # stands (rules only, no model), so a solved problem stops showing and what
    # is still wrong is said again in one sentence.
    second_look(survey, use_model=False, locked=approved, only={new_qid})
    survey.metadata["validation_errors"] = problems_after
    _save_definition(db, questionnaire, survey)
    if new_qid != qid:
        set_questionnaire_approved_qids(db, questionnaire.id, sorted(approved))
    update_questionnaire_status(db, questionnaire.id, "in_review")
    return _question_payload(survey[new_qid], approved)


@router.post("/studies/{study_id}/questionnaire/questions")
def add_question(
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
def delete_question(
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
def reorder_questions(
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
def approve_question(
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
    # Approving says "a respondent can answer this as the document intends".
    # A question with nothing to pick cannot be that, whoever clicks.
    if any(has_nothing_to_answer(q) for q in matching):
        raise HTTPException(status_code=422, detail=_NOTHING_TO_ANSWER.format(qid=qid))
    # A model's reading is confirmed by exactly this: a person approving it.
    confirmed = _BLOCKING_FLAGS | {"MODEL_READ"}
    cleared = sorted({f for q in matching for f in q.flags if f in confirmed})
    if cleared:
        for q in matching:
            q.flags = [f for f in q.flags if f not in confirmed]
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


@router.post("/studies/{study_id}/questionnaire/logic-review")
def run_logic_review(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """A second opinion on the whole survey: one model read, for problems
    between questions that no QA rule covers. Changes no question. (Owner/Analyst)"""
    questionnaire = _latest_questionnaire(db, study_id)
    survey = _load_definition(db, questionnaire)
    result = review_logic(survey)
    survey.metadata["logic_review"] = result
    _save_definition(db, questionnaire, survey)
    logger.info(f"[{study_id}] logic review by {user.name}: "
                f"{len(result.get('findings', []))} finding(s)")
    return result


@router.post("/studies/{study_id}/questionnaire/qa")
def run_questionnaire_qa(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Run the QA loop on the stored definition. (Owner/Analyst)

    The QA agent tests every question as the respondent meets it and against
    the document; the programmer agent repairs what it safely can with today's
    parsers; what is left is flagged for a person. Questions a person approved
    are reported on but never changed. Runs automatically on extraction — this
    is for a study extracted before a parser fix, or after edits."""
    questionnaire = _latest_questionnaire(db, study_id)
    survey = _load_definition(db, questionnaire)
    locked = set(questionnaire.approved_qids or [])
    record = run_qa(survey, locked=locked)
    # Then the second look: every flagged question re-checked, solved where the
    # document settles it, explained in one sentence where it does not.
    record["second_look"] = second_look(survey, locked=locked)
    survey.metadata["validation_errors"] = survey.validate()
    _save_definition(db, questionnaire, survey)
    logger.info(
        f"[{study_id}] QA by {user.name}: {record['checked']} checked, "
        f"{record['fixed']} fixed, {record['needs_you']} need a person"
    )
    return record


def _with_edits(stored: Question, current: dict | None) -> Question:
    """The stored question with the editor's unsaved fields laid over it."""
    if not current:
        return stored
    try:
        return Question.from_dict({**stored.to_dict(), **current})
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Unreadable question: {exc}") from exc


# What the link does with an element that depends on earlier answers. In a
# preview there are no earlier answers, so each is removed and said out loud.
_DEPENDS_ON_ANSWERS = {
    "visibleIf": "In the link this question only shows when its condition is met.",
    "enableIf": "In the link this question is only answerable when its condition is met.",
    "choicesVisibleIf": "In the link only some of these answers show, depending on earlier "
                        "answers. All are shown here.",
    "rowsVisibleIf": "In the link only some rows show, depending on earlier answers.",
}


class QuestionPreviewRequest(BaseModel):
    # The question as the editor shows it, unsaved.
    current: dict | None = None


@router.post("/studies/{study_id}/questionnaire/questions/{qid}/preview")
def question_preview(
    study_id: str,
    qid: str,
    body: QuestionPreviewRequest,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """The question exactly as the survey link would render it — compiled by
    the link's own compiler from the editor's unsaved state. Saves nothing."""
    from src.renderers.surveyjs_compiler import _compile_question

    questionnaire = _latest_questionnaire(db, study_id)
    survey = _load_definition(db, questionnaire)
    if qid not in survey:
        raise HTTPException(status_code=404, detail=f"Question {qid} not found")
    question = _with_edits(survey[qid], body.current)
    by_qid = {q.qid: q for q in survey.questions}
    by_qid[qid] = question
    try:
        element = _compile_question(question, by_qid)
    except Exception as exc:  # the link could not build it either — say so
        return {"survey_json": None, "notes": [f"The link cannot build this question: {exc}"]}
    notes = [why for key, why in _DEPENDS_ON_ANSWERS.items() if element.pop(key, None)]
    if "{" in (element.get("title") or ""):
        notes.append("Text in braces is filled in from an earlier answer in the link.")
    if element.get("type") == "html" and question.type is not QuestionType.INFO:
        notes.append("The link shows this as text only — a respondent cannot answer it.")
    # What the QA agent finds on the edited question, while it is being edited:
    # the problem shows up with the change that caused it, not after saving.
    from src.engines.questionnaire.qa_loop import _tested, qa_check

    problems = [
        f"{i.detail} ({i.evidence})" if i.evidence else i.detail
        for i in (qa_check(question, by_qid) if _tested(question) else [])
    ]
    return {
        "problems": problems,
        "survey_json": {
            "showQuestionNumbers": "off",
            "showNavigationButtons": False,
            "pages": [{"name": "preview", "elements": [element]}],
        },
        "notes": notes,
        "theme": survey.metadata.get("theme"),
    }


class QuestionAgentRequest(BaseModel):
    # The analyst's words. Omitted: only the programmer agent's repairs.
    instruction: str | None = None
    # The question as the editor shows it right now, unsaved — so a follow-up
    # ("keep the text box on Other") revises what is on screen.
    current: dict | None = None


@router.post("/studies/{study_id}/questionnaire/questions/{qid}/agent")
def question_agent(
    study_id: str,
    qid: str,
    body: QuestionAgentRequest,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Suggested fixes for one question, each with before/after and QA.
    Saves nothing — the analyst chooses, edits, and saves. (Owner/Analyst)"""
    questionnaire = _latest_questionnaire(db, study_id)
    survey = _load_definition(db, questionnaire)
    if qid not in survey:
        raise HTTPException(status_code=404, detail=f"Question {qid} not found")
    base = _with_edits(survey[qid], body.current)
    by_qid = {q.qid: q for q in survey.questions}
    suggestions = suggest_repairs(base, by_qid)
    error = None
    instruction = (body.instruction or "").strip()
    if instruction:
        revised = question_agent_revise(base, instruction[:1000], by_qid)
        if "error" in revised:
            error = revised["error"]
        else:
            suggestions.insert(0, revised)
    logger.info(f"[{study_id}] question agent on {qid} by {user.name}: "
                f"{len(suggestions)} suggestion(s){' — ' + error if error else ''}")
    return {"suggestions": suggestions, "error": error}


@router.post("/studies/{study_id}/questionnaire/questions/approve-clean")
def approve_clean(
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
        # ANY flag, not only blocking ones: a flag is the system asking a person
        # to look — "Empty options", "Study instruction applies". Skipping only
        # blocking flags bulk-approved a question with no answers, which then
        # reached a test link with nothing to fill in.
        held = sorted(q.flags) + (["NOTHING_TO_ANSWER"] if has_nothing_to_answer(q) else [])
        if held:
            if q.qid not in approved:
                still_blocked.append({"qid": q.qid, "flags": held})
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


@router.get("/studies/{study_id}/questionnaire/datamap")
def questionnaire_datamap(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Every variable the data file will have, hidden ones included, and what
    fills each one. (Owner/Analyst)

    The data layout is the contract with the client, and the hidden variables
    are where it goes wrong unseen: a coded variable with no rule arrives
    empty, one computed from a base is only as right as that base. Listing
    what fills every column — asked, computed by an approved rule, computed
    from a base, assigned by the link, or not filled at all — is how someone
    checks the answers are punched right before a respondent is ever asked.
    Built by the same code that writes the file, so it cannot drift from it.
    """
    from src.engines.fielding.materialize import ALL_STATUSES, materialize_fielding
    from src.engines.questionnaire.derived import has_rule, is_coded_hidden, rule_text

    questionnaire = _latest_questionnaire(db, study_id)
    survey = _load_definition(db, questionnaire)
    datamap, _frame = materialize_fielding(survey, [], {}, ALL_STATUSES)
    by_qid = {q.qid: q for q in survey.questions}

    def filled_by(v) -> tuple[str, str]:
        q = by_qid.get(v.question_ref or "")
        if v.provenance == "fielding_paradata":
            return "recorded", "recorded by the link for every interview"
        if v.provenance == "fielding_derived" and q is not None:
            base = (q.show_if.verbatim_text if q.show_if else "") or rule_text(q.show_if)
            return "computed", f"1 when {base.removeprefix('IF ').strip()}, else 0"
        if q is not None and is_coded_hidden(q):
            if has_rule(q):
                return "computed", "; ".join(
                    f"{o.code} when {rule_text(o.derive_if)}" for o in q.options if o.derive_if)
            return "not_filled", "no approved rule — the column will be empty"
        if v.question_ref is None and v.name != "respondent_id":
            return "assigned", "assigned by the link, balanced across respondents"
        return "asked", ""

    variables = []
    for v in datamap.variables:
        kind, how = filled_by(v)
        variables.append({
            "name": v.name,
            "label": v.label,
            "type": v.type.value if hasattr(v.type, "value") else str(v.type),
            "values": {str(k): lbl for k, lbl in (v.values or {}).items()} or None,
            "question_ref": v.question_ref,
            "derived": v.provenance == "fielding_derived",
            "filled_by": kind,
            "how": how,
        })
    return {
        "questionnaire_id": questionnaire.id,
        "status": questionnaire.status,
        "n_variables": len(variables),
        "variables": variables,
    }


@router.get("/studies/{study_id}/questionnaire/quote")
def questionnaire_quote(
    study_id: str,
    completes: int | None = None,
    incidence_pct: float | None = None,
    rate_per_minute: float | None = None,
    min_cpi: float | None = None,
    project_fee: float | None = None,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """A quote from the questionnaire: length range, screeners, starts and cost.

    Before anyone scripts the study. Prices come only from the rates passed in;
    nothing here guesses what a panel charges. (Owner/Analyst)
    """
    from src.engines.fielding.quote import quote

    for name, value in (("completes", completes), ("incidence_pct", incidence_pct),
                        ("rate_per_minute", rate_per_minute), ("min_cpi", min_cpi),
                        ("project_fee", project_fee)):
        if value is not None and value < 0:
            raise HTTPException(status_code=422, detail=f"{name} cannot be negative")
    if incidence_pct is not None and incidence_pct > 100:
        raise HTTPException(status_code=422, detail="incidence_pct is a percentage, 0-100")
    survey = _load_definition(db, _latest_questionnaire(db, study_id))
    return quote(survey, completes, incidence_pct, rate_per_minute, min_cpi, project_fee)


class QuotaPlan(BaseModel):
    completes: int
    cells: list[dict] = []


@router.get("/studies/{study_id}/questionnaire/quotas")
def get_quota_plan(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """The quota plan every new link starts from. (Owner/Analyst)"""
    survey = _load_definition(db, _latest_questionnaire(db, study_id))
    plan = (survey.metadata or {}).get("quota_plan") or {"completes": None, "cells": []}
    from src.engines.fielding.quota_plan import cells

    return {**plan, "limits": cells(plan) if plan.get("completes") else []}


@router.put("/studies/{study_id}/questionnaire/quotas")
def set_quota_plan(
    study_id: str,
    body: QuotaPlan,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Save the quota plan; links opened from now on get these cells. (Owner/Analyst)"""
    from src.engines.fielding.quota_plan import cells, problems

    questionnaire = _latest_questionnaire(db, study_id)
    survey = _load_definition(db, questionnaire)
    plan = body.model_dump()
    found = problems(plan, survey)
    if found:
        raise HTTPException(status_code=422, detail=found)
    survey.metadata["quota_plan"] = plan
    _save_definition(db, questionnaire, survey)
    return {**plan, "limits": cells(plan)}


@router.get("/studies/{study_id}/questionnaire/expert-checks")
def expert_checks(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """A senior researcher's notes on the questionnaire as it stands, and its length."""
    from src.engines.questionnaire.expert_checks import check

    return check(_load_definition(db, _latest_questionnaire(db, study_id)))


@router.get("/questionnaire/library")
def questionnaire_library(user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST))):
    """Ready-made modules and study templates, with the inputs each asks for."""
    from src.engines.questionnaire.library import catalogue

    return catalogue()


class ModuleRequest(BaseModel):
    module: str
    inputs: dict = {}
    after_qid: str | None = None


class TemplateRequest(BaseModel):
    template: str
    inputs: dict = {}


def _insert(db: Session, questionnaire, survey: SurveyDefinition, added: list[Question],
            after_qid: str | None) -> list[str]:
    """Put new questions after `after_qid` (or at the end), in the list and its block."""
    before = survey.validate()
    order = [q.qid for q in survey.questions]
    at = order.index(after_qid) + 1 if after_qid in order else len(order)
    survey.questions[at:at] = added
    ids = [q.qid for q in added]
    if not survey.blocks:
        survey.blocks.append(Block(block_id="B1", question_ids=[]))
    block = next((b for b in survey.blocks if after_qid in b.question_ids), survey.blocks[-1])
    pos = block.question_ids.index(after_qid) + 1 if after_qid in block.question_ids \
        else len(block.question_ids)
    block.question_ids[pos:pos] = ids
    survey._rebuild_index()
    new = [p for p in survey.validate() if p not in before]
    if new:
        raise HTTPException(status_code=422, detail={"error": "These questions would not fit",
                                                     "problems": new})
    survey.metadata["validation_errors"] = survey.validate()
    _save_definition(db, questionnaire, survey)
    update_questionnaire_status(db, questionnaire.id, "in_review")
    return ids


@router.post("/studies/{study_id}/questionnaire/modules")
def add_module(
    study_id: str,
    body: ModuleRequest,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Add a ready-made block (screener, awareness funnel, NPS…). (Owner/Analyst)"""
    from src.engines.questionnaire.library import build_module

    questionnaire = _latest_questionnaire(db, study_id)
    survey = _load_definition(db, questionnaire)
    try:
        added = build_module(body.module, body.inputs, {q.qid for q in survey.questions})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return {"added": _insert(db, questionnaire, survey, added, body.after_qid)}


@router.post("/studies/{study_id}/questionnaire/template")
def apply_template(
    study_id: str,
    body: TemplateRequest,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Fill an empty questionnaire from a study template. (Owner/Analyst)"""
    from src.engines.questionnaire.library import build_template

    questionnaire = _latest_questionnaire(db, study_id)
    survey = _load_definition(db, questionnaire)
    if survey.questions:
        raise HTTPException(status_code=409, detail="A template starts an empty questionnaire; "
                            "add modules to this one instead")
    try:
        added = build_template(body.template, body.inputs, set())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return {"added": _insert(db, questionnaire, survey, added, None)}


@router.get("/studies/{study_id}/questionnaire/probes")
def probe_settings(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Open questions, and which get a follow-up when the answer is thin. (Owner/Analyst)"""
    from src.engines.fielding.probes import probed

    survey = _load_definition(db, _latest_questionnaire(db, study_id))
    return {"questions": [{"qid": q.qid, "text": (q.text or "")[:200], "probed": probed(q)}
                          for q in survey.questions if q.type is QuestionType.OPEN_TEXT]}


class ProbeSettings(BaseModel):
    qids: list[str]


@router.put("/studies/{study_id}/questionnaire/probes")
def set_probes(
    study_id: str,
    body: ProbeSettings,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Probe exactly these open questions; a link opened afterwards asks the follow-ups.
    (Owner/Analyst)"""
    questionnaire = _latest_questionnaire(db, study_id)
    survey = _load_definition(db, questionnaire)
    opens = {q.qid: q for q in survey.questions if q.type is QuestionType.OPEN_TEXT}
    unknown = sorted(set(body.qids) - set(opens))
    if unknown:
        raise HTTPException(status_code=422, detail=f"Not open questions: {', '.join(unknown)}")
    for qid, q in opens.items():
        rules = dict(q.validation or {})
        if qid in body.qids:
            rules["probe"] = True
        else:
            rules.pop("probe", None)
        q.validation = rules
    _save_definition(db, questionnaire, survey)
    return probe_settings(study_id, db, user)


def _language(lang: str) -> str:
    from src.engines.fielding.translation import valid_language

    if not valid_language(lang):
        raise HTTPException(status_code=422, detail="Language must be a code like hi, ta or pt-BR")
    return lang


@router.get("/studies/{study_id}/questionnaire/translations")
def translations(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Each language's progress, and how many strings a respondent reads. (Owner/Analyst)"""
    from src.engines.fielding.translation import source_strings, status

    survey = _load_definition(db, _latest_questionnaire(db, study_id))
    study = get_study(db, study_id)
    return {"n_strings": len(source_strings(survey)), "languages": status(survey),
            "draft_job": (study.study_metadata or {}).get("translation_job") if study else None}


def _set_translation_job(db: Session, study_id: str, job: dict) -> None:
    study = get_study(db, study_id)
    study.study_metadata = {**(study.study_metadata or {}), "translation_job": job}
    db.commit()


def _draft_translation(bind, study_id: str, lang: str) -> None:
    """Background: a private model's first draft of every untranslated string."""
    from sqlalchemy.orm import sessionmaker

    from src.engines.fielding.translation import draft

    db = sessionmaker(bind=bind)()
    try:
        questionnaire = _latest_questionnaire(db, study_id)
        survey = _load_definition(db, questionnaire)
        result = draft(survey, lang)
        _save_definition(db, questionnaire, survey)
        _set_translation_job(db, study_id, {"state": "done", "lang": lang, **result})
    except Exception as exc:  # recorded for the panel; never left "running"
        logger.warning(f"[{study_id}] translation draft failed: {exc}")
        reason = ("no private model is set up (Bedrock, or a local model)"
                  if "private model" in str(exc) or "backends failed" in str(exc)
                  else "the model's answer could not be used")
        _set_translation_job(db, study_id, {"state": "failed", "lang": lang,
                                            "error": f"No draft: {reason}."})
    finally:
        db.close()


@router.post("/studies/{study_id}/questionnaire/translations/{lang}/draft", status_code=202)
def draft_translation(
    study_id: str,
    lang: str,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """A model's first draft of every untranslated string, in the background, for a
    translator to check. Only Bedrock or a local model ever reads it. (Owner/Analyst)"""
    _language(lang)
    _latest_questionnaire(db, study_id)  # 404 when there is nothing to translate
    study = get_study(db, study_id)
    if ((study.study_metadata or {}).get("translation_job") or {}).get("state") == "running":
        raise HTTPException(status_code=409, detail="A draft is already being written")
    _set_translation_job(db, study_id, {"state": "running", "lang": lang})
    background.add_task(_draft_translation, db.get_bind(), study_id, lang)
    return {"state": "running", "lang": lang}


@router.get("/studies/{study_id}/questionnaire/translations/{lang}.csv")
def translation_sheet(
    study_id: str,
    lang: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """The translator's sheet for one language, already-translated rows filled.
    (Owner/Analyst)"""
    import csv
    import io

    from src.engines.fielding.translation import SHEET_COLUMNS, sheet

    survey = _load_definition(db, _latest_questionnaire(db, study_id))
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=SHEET_COLUMNS)
    writer.writeheader()
    writer.writerows(sheet(survey, _language(lang)))
    # BOM so Excel opens Hindi, Tamil or Chinese as text, not mojibake.
    return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="translation_{lang}.csv"'})


MAX_SHEET_BYTES = 5 * 1024 * 1024


@router.post("/studies/{study_id}/questionnaire/translations/{lang}")
async def upload_translation(
    study_id: str,
    lang: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Take back a filled sheet: stored, and every blank or broken token listed. (Owner/Analyst)"""
    import csv
    import io

    from src.engines.fielding.translation import status, store

    _language(lang)
    raw = await file.read(MAX_SHEET_BYTES + 1)
    if len(raw) > MAX_SHEET_BYTES:
        raise HTTPException(status_code=413, detail="Sheet larger than 5 MB")
    try:
        rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    except (UnicodeDecodeError, csv.Error):
        raise HTTPException(status_code=422, detail="Save the sheet as CSV (UTF-8)") from None
    if not rows or "key" not in rows[0] or "translation" not in rows[0]:
        raise HTTPException(status_code=422,
                            detail="The sheet needs its key and translation columns")
    questionnaire = _latest_questionnaire(db, study_id)
    survey = _load_definition(db, questionnaire)
    problems = store(survey, lang, rows)
    _save_definition(db, questionnaire, survey)
    done = next(s for s in status(survey) if s["lang"] == lang)
    return {**done, "problems": problems[:200]}


@router.post("/studies/{study_id}/questionnaire/approve-all")
def approve_all(
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
        # Even if it was approved earlier: an approval given before this check
        # existed must not carry an unanswerable question into the field.
        if has_nothing_to_answer(q):
            reasons.append("nothing for a respondent to answer — add its options or rows, "
                           "or change its type")
        if reasons:
            unresolved.append({"qid": q.qid, "reasons": reasons})

    # Every per-question check above passes on a document that lost seventy of
    # its eighty questions before review — the one that remains is fine.
    incomplete = _completeness(survey)
    if incomplete:
        unresolved.insert(0, {"qid": "Document", "reasons": incomplete})

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

    # The instrument is fixed, so the project's content goes into the database
    # as vectors the copilot can search by meaning. Best-effort on purpose:
    # a search index that could not be built must never un-approve a
    # questionnaire — the next search rebuilds it.
    try:
        ensure_index(db, study_id, survey)
    except Exception:
        logger.exception(f"[{study_id}] could not index the questionnaire for search")

    # Label merge (§3.2's datamap-merge fix): an approved questionnaire carries
    # the real question/option wording that a CSV/Excel import lacks. If this
    # study's data is already processed, regenerate its tables/decks with those
    # labels in the background — same daemon-thread pattern as _run_extraction.
    # Best-effort: a merge failure must never un-approve the questionnaire.
    relabel = _start_label_merge(db, study_id, survey)

    # The data layout is fixed the moment the instrument is: a client asking
    # "what columns will I get?" gets the answer at approval, not after the
    # first respondent.
    datamap = plan_datamap(survey)
    return {
        "questionnaire_id": questionnaire.id,
        "status": "approved",
        "label_merge_started": relabel,
        "n_variables": len(datamap.variables),
        "variables": [v.name for v in datamap.variables],
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


@router.get("/studies/{study_id}/questionnaire/map")
def questionnaire_map(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """Every question's bucket and links, read from the definition itself.

    What kind of variable each question is (screener, profile, core measure,
    loop, open end, hidden) and how it connects: asked if, options from, text
    uses, computed from, and the reverse — what its answer decides. No model is
    asked; every link is something the questionnaire already states.
    """
    from src.engines.questionnaire.buckets import bucket_of, links

    questionnaire = _latest_questionnaire(db, study_id)
    survey = _load_definition(db, questionnaire)
    connections = links(survey)
    return {
        "questions": {
            q.qid: {
                "bucket": bucket,
                "reason": reason,
                "suggested": suggested,
                "links": connections.get(q.qid, {}),
            }
            for q in survey.questions
            for bucket, reason, suggested in [bucket_of(q)]
        }
    }


_MASK_MODES = {"selected", "full_list", "list_except", "not_selected"}


@router.put("/studies/{study_id}/questionnaire/questions/{qid}/links")
def set_question_links(
    study_id: str,
    qid: str,
    request: QuestionLinksRequest,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Add or remove what a question takes from earlier ones. (Owner/Analyst)

    "Options from" is a mask: this question's list comes from an earlier one,
    narrowed per respondent. "Text uses" is a pipe: an earlier answer is written
    into the wording. Both may only point BACKWARDS — a question cannot show
    options from one the respondent has not reached yet — and a question with no
    list of its own gets the source's list, as a document's "[SHOW Q26 LIST]"
    would.
    """
    from src.engines.questionnaire.list_sources import fill_lists

    questionnaire = _latest_questionnaire(db, study_id)
    survey = _load_definition(db, questionnaire)
    if qid not in survey:
        raise HTTPException(status_code=404, detail=f"Question {qid} not found")
    order = survey.question_order()
    earlier = set(order[: order.index(qid)]) if qid in order else set()

    def check_source(source: str) -> None:
        if source not in survey:
            raise HTTPException(
                status_code=422, detail=f"{source} is not a question in this survey"
            )
        if source not in earlier:
            raise HTTPException(
                status_code=422,
                detail=f"{source} comes after {qid}: a respondent has not answered it yet",
            )

    question = survey[qid]
    validation = dict(question.validation or {})
    techniques = dict(validation.get("techniques") or {})
    provided = request.model_fields_set

    if "options_from" in provided:
        masks = []
        for mask in request.options_from or []:
            if mask.mode not in _MASK_MODES:
                raise HTTPException(status_code=422, detail=f"Unknown mode: {mask.mode}")
            check_source(mask.source_qid)
            if mask.exclude_qid:
                check_source(mask.exclude_qid)
            masks.append({
                "source_qid": mask.source_qid, "mode": mask.mode,
                "exclude_qid": mask.exclude_qid, "verbatim": f"set in review by {user.name}",
            })
        techniques["masks"] = masks
    if "text_from" in provided:
        pipes = []
        for source in request.text_from or []:
            check_source(source)
            pipes.append({"source_qid": source, "name": None, "verbatim": f"[INSERT {source}]"})
            if f"[INSERT {source}]" not in (question.text or ""):
                # Where the answer goes: at the end unless the reviewer moves it.
                question.text = f"{(question.text or '').rstrip()} [INSERT {source}]"
        # A pipe removed takes its placeholder with it — left in, it prints.
        kept = {p["source_qid"] for p in pipes}
        for old in techniques.get("pipes") or []:
            source = old.get("source_qid")
            if source and source not in kept:
                question.text = (question.text or "").replace(old.get("verbatim") or "", "").strip()
        techniques["pipes"] = pipes

    validation["techniques"] = techniques
    question.validation = validation
    fill_lists(survey)  # a masked question with no list of its own takes the source's

    survey.metadata["validation_errors"] = survey.validate()
    _save_definition(db, questionnaire, survey)
    update_questionnaire_status(db, questionnaire.id, "in_review")
    return _question_payload(question, set(questionnaire.approved_qids or []))


# --- Re-read (current parser, no model call) --------------------------------


def _opt_dicts(options: list[Option] | None) -> list[dict] | None:
    return [o.to_dict() for o in options] if options else None


def _reread_diff(old: Question, new: Question) -> list[str]:
    """Field names where a re-read would change what is stored.

    `show_if` is compared only in the direction "found something new" — the
    caller applies it the same way (never clears an existing base just because
    this pass didn't happen to re-find it)."""
    changed = []
    if old.text != new.text:
        changed.append("text")
    if old.type != new.type:
        changed.append("type")
    for field_name, old_val, new_val in (
        ("options", old.options, new.options),
        ("rows", old.rows, new.rows),
        ("cols", old.cols, new.cols),
    ):
        if _opt_dicts(old_val) != _opt_dicts(new_val):
            changed.append(field_name)
    old_term = old.terminate_if.to_dict() if old.terminate_if else None
    new_term = new.terminate_if.to_dict() if new.terminate_if else None
    if old_term != new_term:
        changed.append("terminate_if")
    if new.show_if is not None:
        old_show = old.show_if.to_dict() if old.show_if else None
        if old_show != new.show_if.to_dict():
            changed.append("show_if")
    old_techniques = (old.validation or {}).get("techniques")
    new_techniques = (new.validation or {}).get("techniques")
    if old_techniques != new_techniques:
        changed.append("validation")
    if (old.extraction_evidence or {}) != (new.extraction_evidence or {}):
        changed.append("extraction_evidence")
    if sorted(old.flags) != sorted(new.flags):
        changed.append("flags")
    return changed


#: Flags that say "this is not an ordinary question". Losing one silently is
#: how a derived variable or an unfieldable exercise gets turned back into
#: something a respondent is asked.
def _answer_surface(question: Question) -> int:
    """How many answers this question offers, however they are arranged."""
    return len(question.options or []) + len(question.rows or []) + len(question.cols or [])


#: Codes from src/engines/questionnaire/schema.py, as strings for the same
#: reason _BLOCKING_FLAGS above is.
_PROTECTED_FLAGS = frozenset({
    "DERIVED_VARIABLE",
    "TRADE_OFF_UNSUPPORTED",
})


def _reread_is_regression(old: Question, new: Question) -> bool:
    """True when the re-read is worse than what is already stored.

    A parser fix should only ever ADD structure; losing an option list, grid
    shape, or the wording itself means this re-read parsed the source WORSE
    than whatever produced the stored question (a different chunking, a table
    the rebuilt rows didn't fully capture) — never something to save over.

    This used to catch only a drop to ZERO, which let three real regressions
    through on the Google Ads study: DA_TOP_GOAL losing one option of nine,
    HIDDEN_TOPBENEFIT turning from a declared hidden variable back into a
    9-option question, and MAXDIFF_OE losing the flag that says the exercise
    cannot be fielded. Shrinking, un-hiding and un-flagging are all "worse".
    """
    if old.text and not new.text:
        return True
    # Shrinkage, not only total loss. Nine options becoming eight is a
    # respondent losing an answer and a data file losing a column.
    #
    # Only WITHIN the same type, though: a flat list becoming a grid moves its
    # answers from `options` into `rows`/`cols`, which reads as options going
    # to zero and is exactly the per-item repair working. Across a type change
    # the honest comparison is the total answer surface.
    if old.type is new.type:
        for was, now in (
            (old.options, new.options),
            (old.rows, new.rows),
            (old.cols, new.cols),
        ):
            if len(now or []) < len(was or []):
                return True
    elif _answer_surface(new) < _answer_surface(old):
        return True
    # A row nobody is asked becoming a question somebody is asked.
    if old.type is QuestionType.INFO and new.type is not QuestionType.INFO:
        return True
    # `_apply_reread` replaces flags wholesale, so a dropped flag is silent.
    if (set(old.flags or ()) - set(new.flags or ())) & _PROTECTED_FLAGS:
        return True
    return False


def _apply_reread(question: Question, new: Question) -> None:
    """Overwrite the parser-produced fields on `question` in place.

    qid and approval state are the caller's to keep — neither is touched here.
    """
    question.text = new.text
    question.type = new.type
    question.options = new.options
    question.rows = new.rows
    question.cols = new.cols
    question.terminate_if = new.terminate_if
    if new.show_if is not None:
        question.show_if = new.show_if
    validation = dict(question.validation or {})
    validation.update(new.validation or {})
    question.validation = validation
    question.extraction_evidence = dict(new.extraction_evidence or {})
    question.flags = list(new.flags or [])


def _profile_slug_for(survey: SurveyDefinition) -> str | None:
    """The detected format profile slug already stashed on this survey.

    Mirrors corrections.py's `_profile_slug`, reading the already-loaded
    SurveyDefinition instead of re-parsing `definition_json`. None when no
    profile was detected — the caller's job to turn that into `patterns=[]`.
    """
    profile = (survey.metadata or {}).get("profile")
    if isinstance(profile, dict):
        return profile.get("slug")
    return profile if isinstance(profile, str) else None


def _patterns_for_survey(db: Session, survey: SurveyDefinition) -> list:
    """The taught patterns that apply to this survey's detected profile.

    `[]` when no profile was detected — the T32/T33 guarantee: an
    undetected/untaught document behaves byte-identically to before the
    correction loop existed."""
    from api.db import active_patterns_for_profile

    slug = _profile_slug_for(survey)
    return list(active_patterns_for_profile(db, slug)) if slug else []


def _unread_fragments(question: Question) -> list[str]:
    """Fragments of `question` the parsers saw but could not interpret.

    Mirrors TeachFormat.tsx's `unrecognisedFragments` so a founder told "no
    improvement" also sees exactly what is worth teaching, without opening the
    question and reading validation by hand. Anything already cited in
    `extraction_evidence` is excluded: it WAS understood.
    """
    validation = question.validation or {}
    directives = [str(d) for d in (validation.get("scripter_directives") or [])]
    rows = validation.get("unrecognised_rows")
    row_labels = list(rows.keys()) if isinstance(rows, dict) else []

    def _norm(text: str) -> str:
        return text.replace("[", "").replace("]", "").strip().lower()

    cited = [_norm(str(v)) for v in (question.extraction_evidence or {}).values()]
    seen: set[str] = set()
    out: list[str] = []
    for frag in [*directives, *row_labels]:
        needle = _norm(frag)
        if not needle or needle in seen or any(needle in c for c in cited):
            continue
        seen.add(needle)
        out.append(frag)
    return out


def _reread_all_with_patterns(
    survey: SurveyDefinition, patterns: list
) -> tuple[list[dict], int]:
    """Re-read every question in `survey`, applying `patterns`.

    Shared by reread-all and apply-teaching so the two can never drift apart.
    Returns (updated, n_unchanged); does not save — the caller decides that.
    """
    updated: list[dict] = []
    n_unchanged = 0
    for question in list(survey.questions):
        reread = reread_question(question, patterns)
        if reread is None or _reread_is_regression(question, reread):
            n_unchanged += 1
            continue
        changed = _reread_diff(question, reread)
        if not changed:
            n_unchanged += 1
            continue
        _apply_reread(question, reread)
        updated.append({"qid": question.qid, "changed": changed})
    return updated, n_unchanged


def _persist_reread(
    db: Session, questionnaire, survey: SurveyDefinition, updated: list[dict]
) -> None:
    """Common tail of a successful re-read pass: rebuild, save, unapprove touched qids."""
    survey._rebuild_index()
    _rerun_reread_safe_passes(survey)
    survey.metadata["validation_errors"] = survey.validate()
    _save_definition(db, questionnaire, survey)
    update_questionnaire_status(db, questionnaire.id, "in_review")
    approved = set(questionnaire.approved_qids or []) - {u["qid"] for u in updated}
    set_questionnaire_approved_qids(db, questionnaire.id, sorted(approved))


def _rerun_reread_safe_passes(survey: SurveyDefinition) -> None:
    """The survey-level passes safe to replay after a re-read.

    Only the ones that read the whole survey and are idempotent on questions
    they've already processed (a masked list, a named carousel, a constant-sum
    group). Section paths / intro screens / loop and grid expansion restructure
    the question list itself and must not run again on an already-expanded
    survey."""
    from src.engines.questionnaire.carousel import apply as apply_carousels
    from src.engines.questionnaire.constant_sum import apply as apply_constant_sums
    from src.engines.questionnaire.list_sources import fill_lists
    from src.engines.questionnaire.write_ins import mark_write_ins

    fill_lists(survey)
    apply_carousels(survey)
    apply_constant_sums(survey)
    mark_write_ins(survey)


@router.post("/studies/{study_id}/questionnaire/questions/{qid}/reread")
def reread_one_question(
    study_id: str,
    qid: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Re-run the CURRENT deterministic parsers on one question. (Owner/Analyst)

    No model call: `reread_question` replays `parse_prose_question`/
    `_apply_table_shape` against the SAME `source_text` already stored, so a
    parser fix landed after this question was first extracted reaches it for
    free. Also passes the profile's founder-taught patterns (T33 correction
    loop) — without this, teaching the parser a convention never reached the
    document a founder was actually looking at, only the next upload. 422,
    unchanged, if the re-read finds nothing better than what is already stored
    (see `_reread_is_regression`) or nothing changed at all."""
    questionnaire = _latest_questionnaire(db, study_id)
    survey = _load_definition(db, questionnaire)
    if qid not in survey:
        raise HTTPException(status_code=404, detail=f"Question {qid} not found")

    question = survey[qid]
    patterns = _patterns_for_survey(db, survey)
    reread = reread_question(question, patterns)
    if reread is None:
        # Say WHICH refusal this is. "No source text" is untrue of a spec-table
        # question and sends a reviewer looking for a missing excerpt.
        if (question.extraction_method or "") == "spec_table":
            raise HTTPException(
                status_code=422,
                detail=(
                    f"{qid} was read from a spec table, and re-read only replays the "
                    "prose parser — it would read the question differently rather than "
                    "better. Re-upload the document to pick up table-parser fixes."
                ),
            )
        raise HTTPException(
            status_code=422, detail=f"{qid} has no source text the current parser can read"
        )
    if _reread_is_regression(question, reread):
        raise HTTPException(
            status_code=422,
            detail=f"Re-read of {qid} found less than what is already stored — left unchanged",
        )
    changed = _reread_diff(question, reread)
    if not changed:
        raise HTTPException(
            status_code=422,
            detail={
                "error": f"Re-read of {qid} found no improvement",
                "unread_fragments": _unread_fragments(question),
            },
        )

    _apply_reread(question, reread)
    _persist_reread(db, questionnaire, survey, [{"qid": qid}])
    approved = set(questionnaire.approved_qids or []) - {qid}
    payload = _question_payload(survey[qid], approved)
    payload["changed"] = changed
    return payload


@router.post("/studies/{study_id}/questionnaire/questions/reread-all")
def reread_all_questions(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Re-read every question with the current parsers, in one pass. (Owner/Analyst)

    Same no-model-call re-read as the single-question endpoint, also passing
    the profile's taught patterns; the survey-level passes run once at the end
    rather than after each question. A question with no improvement is simply
    left alone — this never errors on a clean survey, it just reports 0
    updated."""
    questionnaire = _latest_questionnaire(db, study_id)
    survey = _load_definition(db, questionnaire)
    patterns = _patterns_for_survey(db, survey)

    updated, _n_unchanged = _reread_all_with_patterns(survey, patterns)
    if updated:
        _persist_reread(db, questionnaire, survey, updated)

    return {"n_questions": len(survey.questions), "n_updated": len(updated), "updated": updated}


def _archived_source(db: Session, questionnaire, folder: Path) -> Path:
    """Write a study's archived document into `folder` and return the path."""
    if not questionnaire.source_file_b64:
        raise HTTPException(
            status_code=404,
            detail=(
                "The document itself was not archived for this questionnaire, so it "
                "cannot be re-read here. Upload it again to make this available."
            ),
        )
    name = questionnaire.source_filename or f"{questionnaire.id}.docx"
    path = folder / f"{questionnaire.id}{Path(name).suffix.lower() or '.docx'}"
    path.write_bytes(decompress_archived(questionnaire.source_file_b64))
    return path


@router.get("/studies/{study_id}/questionnaire/diagnosis")
def diagnose_extraction(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """Why does this study hold fewer questions than its document has? (Authenticated)

    Re-segments the ARCHIVED document with today's parser and compares. That
    comparison is the only thing that can tell a reviewer their study is broken
    in a way that has already been fixed: an extraction is a snapshot taken by
    the parser of the day it ran, and every fix since reaches the study only on
    re-extraction. Reports; changes nothing.
    """
    import tempfile

    questionnaire = _latest_questionnaire(db, study_id)
    survey = _load_definition(db, questionnaire)
    if not questionnaire.source_file_b64:
        return {
            "questions_stored": len(survey.questions),
            "can_reextract": False,
            "findings": [],
            "note": (
                "The document itself was not archived for this study, so what the "
                "parser would make of it today cannot be checked here. Upload it "
                "again to compare."
            ),
        }

    with tempfile.TemporaryDirectory() as folder:
        path = _archived_source(db, questionnaire, Path(folder))
        try:
            blocks = load_pdf(path) if path.suffix == ".pdf" else load_docx(path)
        except Exception as exc:
            logger.warning("could not re-read %s: %s", questionnaire.id, exc)
            raise HTTPException(
                status_code=422,
                detail="This document could not be opened to check it.",
            ) from exc
        chunks = segment_into_questions(blocks)

    return diagnose(survey.questions, chunks, blocks, can_reextract=True)


@router.get("/studies/{study_id}/questionnaire/workbook")
def questionnaire_workbook(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """The questionnaire as an .xlsx somebody can read away from the screen.

    Everything in it is already known — preflight runs these checks before a
    link can be made, and the review page shows every question. What it could
    not do is leave the screen, and a scripter working in another tool, a
    client signing off, or a PM checking this wave against the last one all
    want the instrument as a file.

    Built from the stored definition and the same audit that gates fielding,
    never by re-reading the document: two readings of one questionnaire that
    disagree is worse than one reading.
    """
    import tempfile

    from src.engines.fielding.preflight import preflight
    from src.renderers.questionnaire_workbook import build_workbook

    questionnaire = _latest_questionnaire(db, study_id)
    survey = _load_definition(db, questionnaire)
    report = preflight(
        survey,
        approved=questionnaire.status == "approved",
        blocking_flags=_BLOCKING_FLAGS,
        approved_qids=set(questionnaire.approved_qids or []),
    )
    brief = (survey.metadata or {}).get("study_overview") or {}

    with tempfile.TemporaryDirectory() as folder:
        path = build_workbook(
            survey, Path(folder) / "questionnaire.xlsx",
            checks=report.get("checks") or [], brief=brief,
        )
        content = path.read_bytes()

    name = (questionnaire.source_filename or study_id).rsplit(".", 1)[0]
    return Response(
        content=content,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition": f'attachment; filename="{name}_questionnaire.xlsx"'
        },
    )


@router.post("/studies/{study_id}/questionnaire/reextract")
def reextract_questionnaire(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Extract the archived document again with today's parsers. (Owner/Analyst)

    The only way a parser fix reaches a study already in the database. Unlike a
    re-read — which replays each stored question's own text, and so can never
    recover a question the segmenter never separated — this starts from the
    document.

    It creates a NEW questionnaire version rather than overwriting. The older
    one keeps its definition and its per-question approvals, so re-extracting
    cannot destroy a review somebody spent hours on; it supersedes it, and the
    previous version is still there to compare against.
    """
    questionnaire = _latest_questionnaire(db, study_id)
    fresh = create_questionnaire(
        db,
        study_id,
        source_filename=questionnaire.source_filename or f"{questionnaire.id}.docx",
        version=(questionnaire.version or 1) + 1,
    )
    # The archive is re-pointed at the new row so the next re-extract can start
    # from the document too; the old row keeps its own copy.
    set_questionnaire_source_archive(db, fresh.id, questionnaire.source_file_b64)

    # The study's own upload directory, exactly where upload puts it — not a
    # temp dir, which the extraction thread outlives and so would never clean.
    path = _archived_source(db, questionnaire, _questionnaire_dir(study_id))
    threading.Thread(
        target=_run_extraction,
        args=(
            fresh.id,
            study_id,
            path,
            _definition_path(study_id, fresh.id),
            questionnaire.source_filename or "questionnaire.docx",
        ),
        daemon=True,
    ).start()

    return {
        "questionnaire_id": fresh.id,
        "version": fresh.version,
        "status": "processing",
        "superseded": questionnaire.id,
        "message": (
            f"Re-extracting {questionnaire.source_filename or 'the document'} as "
            f"version {fresh.version}. The previous version and its approvals are kept."
        ),
    }


@router.post("/studies/{study_id}/questionnaire/apply-teaching")
def apply_teaching(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Re-read every question with the profile's CURRENT taught patterns. (Owner/Analyst/Founder)

    The point of this endpoint: a founder who just taught a convention should
    not have to separately remember to click "Re-read all" to see it take
    effect on the document in front of them. Reuses `_reread_all_with_patterns`
    — the same machinery `reread-all` uses — so the two can never disagree
    about what counts as an improvement."""
    updated, n_unchanged = _apply_teaching_for_study(db, study_id)
    return {"n_updated": len(updated), "updated": updated, "n_unchanged": n_unchanged}


def _apply_teaching_for_study(db: Session, study_id: str) -> tuple[list[dict], int]:
    """Core of the apply-teaching pass. Shared by the endpoint above and by
    `apply_teaching_after_lesson` below, which corrections.py calls right after
    a pattern is taught or a preview approved. Raises the same 409/500 as
    `_load_definition` when there IS a questionnaire but it is not readable;
    a study with no questionnaire at all is simply nothing to apply to."""
    questionnaire = _latest_questionnaire(db, study_id)
    if questionnaire is None:
        return [], 0
    survey = _load_definition(db, questionnaire)
    patterns = _patterns_for_survey(db, survey)

    updated, n_unchanged = _reread_all_with_patterns(survey, patterns)
    if updated:
        _persist_reread(db, questionnaire, survey, updated)
    return updated, n_unchanged


def apply_teaching_after_lesson(db: Session, study_id: str | None) -> dict | None:
    """Best-effort apply-teaching pass for the moment a lesson is saved (a
    pattern taught, a correction preview approved) when the caller already
    knows which study is in context.

    Never raises: teaching a pattern or approving a preview must still succeed
    even when this side-effect cannot run (no questionnaire extracted yet, a
    stale profile). Returns None — "leave the response unchanged", per the
    caller's contract — when `study_id` is unknown or there is nothing to
    apply to.
    """
    if not study_id:
        return None
    try:
        updated, _n_unchanged = _apply_teaching_for_study(db, study_id)
    except HTTPException:
        return None
    except Exception:  # noqa: BLE001 - a side-effect must not fail the lesson
        # A definition this pass cannot read (an old or hand-written one) must
        # not take down the thing the user actually asked for: the lesson is
        # saved either way, and the reviewer can re-read by hand.
        logger.exception(f"[{study_id}] apply-teaching after a lesson failed")
        return None
    return {"n_updated": len(updated)}


# --- Split ("for each X, ask Y" -> one question per X) ----------------------


@router.post("/studies/{study_id}/questionnaire/questions/{qid}/split")
def split_question(
    study_id: str,
    qid: str,
    request: QuestionSplitRequest,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Turn one question into one-per-item copies: {qid}_1..{qid}_n. (Owner/Analyst)

    "For each category, which brands have you used" is really N questions asked
    once each — the same shape `loops.expand_loops`/`carousel.apply` already
    produce from a document's own wording, just chosen by a reviewer instead of
    read from prose. Refused (422) if fewer than two items are given, or if any
    {qid}_{i} already exists (a second split of the same question)."""
    from src.engines.questionnaire.loops import _replace

    questionnaire = _latest_questionnaire(db, study_id)
    survey = _load_definition(db, questionnaire)
    if len(request.items) < 2:
        raise HTTPException(status_code=422, detail="Split needs at least two items")

    new_qids = [f"{qid}_{i}" for i in range(1, len(request.items) + 1)]
    clash = sorted({q.qid for q in survey.questions if q.qid in new_qids})
    if qid not in survey:
        # A qid already split has no question left under its own name (`_replace`
        # took it out) — that is a stale request, not a 404: the client is
        # pointing at the qid it already split, so say so rather than a bare
        # "not found" that looks like a typo.
        if clash:
            raise HTTPException(
                status_code=422,
                detail=f"{qid} was already split — {', '.join(clash)} already exist",
            )
        raise HTTPException(status_code=404, detail=f"Question {qid} not found")
    if clash:
        raise HTTPException(
            status_code=422,
            detail=f"{qid} was already split — {', '.join(clash)} already exist",
        )

    try:
        copy_type = QuestionType(request.type) if request.type else None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid type: {exc}") from exc

    template = survey[qid]
    parts: list[Question] = []
    for index, item in enumerate(request.items, start=1):
        validation = dict(template.validation or {})
        validation["loop"] = {"of": qid, "index": index, "label": item.label}
        parts.append(Question(
            qid=new_qids[index - 1],
            text=f"{template.text} ({item.label})",
            type=copy_type or template.type,
            options=_build_options(item.options) if item.options is not None
            else list(template.options),
            rows=list(template.rows) if template.rows else None,
            cols=list(template.cols) if template.cols else None,
            required=template.required,
            randomize_options=template.randomize_options,
            show_if=Expression.from_dict(item.show_if) if item.show_if else None,
            terminate_if=template.terminate_if,
            piping_refs=list(template.piping_refs or []),
            validation=validation,
            source_text=template.source_text,
            source_location=template.source_location,
            extraction_method="manual_split",
            extraction_evidence=dict(template.extraction_evidence),
        ))

    _replace(survey, template, parts)
    survey._rebuild_index()

    survey.metadata["validation_errors"] = survey.validate()
    _save_definition(db, questionnaire, survey)
    update_questionnaire_status(db, questionnaire.id, "in_review")

    approved = set(questionnaire.approved_qids or [])
    return {
        "replaced": qid,
        "into": [q.qid for q in parts],
        "questions": [_question_payload(q, approved) for q in parts],
    }

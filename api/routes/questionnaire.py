"""Questionnaire ingestion + review endpoints — scoped to ONE study.

Wires the deterministic loader (src/intake/questionnaire_loader.py) and the LLM
extractor (src/engines/questionnaire/extractor.py) behind an upload endpoint,
then exposes the resulting `SurveyDefinition` for human review and correction.

Storage convention (matches api/routes/chat.py's datamap.json / crosstabs.json
handling): the full nested SurveyDefinition is persisted as JSON on disk under
the study's directory; the QuestionnaireDB row tracks only metadata + review
status + the on-disk path. The dataclass in src/core/survey_definition.py is the
source of truth for the instrument's structure — it is never re-modelled as SQL.

Roles (api/auth.py): owner/analyst may upload and edit; reviewer is read-only
(GET allowed; upload/PATCH/approve rejected with 403).
"""

import logging
import sys
import threading
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from api.auth import UserRole, get_current_user, require_role
from api.config import STUDIES_DIR
from api.db import (
    UserDB,
    create_questionnaire,
    get_db,
    get_study,
    list_questionnaires_for_study,
    set_questionnaire_approved_qids,
    update_questionnaire_status,
)
from api.models import (
    QuestionnaireStatusResponse,
    QuestionnaireUploadResponse,
    QuestionUpdateRequest,
)
from src.core.survey_definition import Expression, Option, QuestionType, SurveyDefinition
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
_BLOCKING_FLAGS = frozenset(
    {"AMBIGUOUS_ROUTING", "UNRESOLVED_ROUTING_QID", "EXTRACTION_UNAVAILABLE"}
)


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


def _load_definition(questionnaire) -> SurveyDefinition:
    """Load the on-disk SurveyDefinition for a questionnaire row, or 409/404.

    404 while still processing (nothing on disk yet); a corrupt/missing file on
    a supposedly-ready questionnaire is a 500-worthy server fault.
    """
    if not questionnaire.definition_path:
        raise HTTPException(
            status_code=409,
            detail=f"Questionnaire is still {questionnaire.status}; no definition yet",
        )
    path = Path(questionnaire.definition_path)
    if not path.exists():
        raise HTTPException(status_code=500, detail="Questionnaire definition file missing")
    return SurveyDefinition.from_json(path.read_text())


def _save_definition(questionnaire, survey: SurveyDefinition) -> None:
    Path(questionnaire.definition_path).write_text(survey.to_json())


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
        n_approved=len(questionnaire.approved_qids or []),
        validation_errors=metadata.get("validation_errors", []),
        extraction_summary=metadata.get("extraction_summary", {}),
    )


def _question_payload(question, approved_qids: set[str]) -> dict:
    """Full per-question payload for the review UI — every field a reviewer
    needs (source_text, confidence, flags) is included, nothing trimmed."""
    data = question.to_dict()
    data["approved"] = question.qid in approved_qids
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
    from api.db import SessionLocal

    job_db = SessionLocal()
    try:
        suffix = file_path.suffix.lower()
        if suffix == ".docx":
            blocks = load_docx(file_path)
        else:  # .pdf — the only other accepted type (validated at upload)
            blocks = load_pdf(file_path)

        chunks = segment_into_questions(blocks)
        survey = extract_survey_definition(chunks, study_id=study_id)
        # Title from the original upload name, not the on-disk path (which is
        # renamed to the questionnaire id to avoid collisions).
        survey.title = Path(source_filename).stem

        definition_path.write_text(survey.to_json())
        update_questionnaire_status(
            job_db, questionnaire_id, "draft", definition_path=str(definition_path)
        )
        logger.info(
            f"[{study_id}] Questionnaire {questionnaire_id} extracted: "
            f"{len(survey.questions)} questions"
        )
    except Exception as exc:
        logger.error(
            f"[{study_id}] Questionnaire {questionnaire_id} extraction failed: {exc}",
            exc_info=True,
        )
        update_questionnaire_status(job_db, questionnaire_id, "error")
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
    """Current questionnaire status + extraction summary. (Authenticated)"""
    questionnaire = _latest_questionnaire(db, study_id)
    survey = None
    if questionnaire.definition_path and Path(questionnaire.definition_path).exists():
        survey = SurveyDefinition.from_json(Path(questionnaire.definition_path).read_text())
    return _status_response(questionnaire, survey)


@router.get("/studies/{study_id}/questionnaire/questions")
async def list_questions(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """Full list of extracted questions with provenance/confidence/flags."""
    questionnaire = _latest_questionnaire(db, study_id)
    survey = _load_definition(questionnaire)
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
    survey = _load_definition(questionnaire)
    if qid not in survey:
        raise HTTPException(status_code=404, detail=f"Question {qid} not found")
    approved = set(questionnaire.approved_qids or [])
    return _question_payload(survey[qid], approved)


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
        question.options = [
            Option(code=int(o["code"]), label=str(o["label"]), **{
                k: o[k] for k in ("exclusive", "anchored", "open_other") if k in o
            })
            for o in request.options
        ]
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
    """Correct one extracted question. Re-validates the whole SurveyDefinition
    after the edit; rejects (422) if the edit would break `.validate()`.
    (Owner/Analyst)"""
    questionnaire = _latest_questionnaire(db, study_id)
    survey = _load_definition(questionnaire)
    if qid not in survey:
        raise HTTPException(status_code=404, detail=f"Question {qid} not found")

    try:
        _apply_update(survey[qid], request)
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid update: {exc}") from exc

    problems = survey.validate()
    if problems:
        raise HTTPException(
            status_code=422,
            detail={"error": "Edit would make the questionnaire invalid", "problems": problems},
        )

    survey.metadata["validation_errors"] = problems  # now empty
    _save_definition(questionnaire, survey)
    update_questionnaire_status(db, questionnaire.id, "in_review")
    approved = set(questionnaire.approved_qids or [])
    return _question_payload(survey[qid], approved)


@router.post("/studies/{study_id}/questionnaire/questions/{qid}/approve")
async def approve_question(
    study_id: str,
    qid: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Mark one question as reviewer-approved. (Owner/Analyst)"""
    questionnaire = _latest_questionnaire(db, study_id)
    survey = _load_definition(questionnaire)
    if qid not in survey:
        raise HTTPException(status_code=404, detail=f"Question {qid} not found")

    approved = set(questionnaire.approved_qids or [])
    approved.add(qid)
    set_questionnaire_approved_qids(db, questionnaire.id, sorted(approved))
    if questionnaire.status == "draft":
        update_questionnaire_status(db, questionnaire.id, "in_review")
    return {"qid": qid, "approved": True, "n_approved": len(approved)}


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
    survey = _load_definition(questionnaire)
    approved = set(questionnaire.approved_qids or [])

    unresolved: list[dict] = []
    for q in survey.questions:
        reasons = []
        if q.qid not in approved:
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
    _save_definition(questionnaire, survey)
    update_questionnaire_status(db, questionnaire.id, "approved")
    logger.info(f"[{study_id}] Questionnaire {questionnaire.id} approved by {user.name}")
    return {"questionnaire_id": questionnaire.id, "status": "approved"}

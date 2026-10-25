"""Study endpoints."""

import json
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.auth import UserRole, get_current_user, require_role
from api.config import (
    AUTO_APPROVE_CLASSIFY,
    AUTO_APPROVE_QC,
    REQUIRE_DELIVERY_APPROVAL,
    STUDIES_DIR,
)
from api.db import (
    UserDB,
    approve_gate,
    create_share_link,
    create_study,
    get_active_share_link_for_study,
    get_db,
    get_study,
    list_studies,
    revoke_share_link,
    update_study_paths,
    update_study_status,
)
from api.models import (
    ApprovalRequest,
    CreateStudyRequest,
    ShareLinkResponse,
    StudyListResponse,
    StudyMetadata,
    StudyResponse,
    UploadResponse,
)
from src.engines.analysis.quality import data_quality
from src.orchestration.orchestrator import StudyOrchestrator
from src.platform.events import emit
from src.platform.jobs import JobError, enqueue, handler

logger = logging.getLogger(__name__)
router = APIRouter()

# Original uploads are archived gzip+base64 into Postgres so a study can be
# re-processed after Render's ephemeral disk is wiped. Cap what we archive:
# base64 inflates ~4/3, and the free Postgres tier is 1GB total. 25MB of
# compressed source covers every realistic CSV/Excel datafeed; a file over
# the cap simply isn't archived (re-process then needs a fresh upload).
MAX_ARCHIVED_SOURCE_BYTES = 25 * 1024 * 1024


def _archive_source_file(job_db, study_id: str, file_path: Path) -> None:
    """Best-effort: gzip+base64 the upload into the study row (size-capped)."""
    from api.db import compress_for_archive, set_study_artifacts

    try:
        archived = compress_for_archive(file_path)
        if archived is None:
            logger.info(
                f"[{study_id}] source not archived: over {MAX_ARCHIVED_SOURCE_BYTES} "
                "bytes compressed"
            )
            return
        set_study_artifacts(
            job_db,
            study_id,
            source_filename=file_path.name,
            source_file_b64=archived,
        )
        logger.info(f"[{study_id}] source archived to DB ({len(archived)} b64 chars)")
    except Exception as exc:
        logger.warning(f"[{study_id}] source archive failed (non-fatal): {exc}")


def _restore_source_file(study, upload_dir: Path) -> Path | None:
    """Rebuild the original upload from the DB archive; None if not archived."""
    from api.db import decompress_archived

    if not study.source_file_b64 or not study.source_filename:
        return None
    upload_dir.mkdir(parents=True, exist_ok=True)
    path = upload_dir / study.source_filename
    path.write_bytes(decompress_archived(study.source_file_b64))
    return path


# Stages the pipeline passes through while a worker should be alive. A study
# sitting in one of these with no heartbeat has lost its thread.
_RUNNING_STAGES = frozenset({"classifying", "processing", "intake", "classified"})
_STUDY_STALE_AFTER = timedelta(minutes=20)


def _is_stale_study(study) -> bool:
    """True when a study claims to be processing but nothing has moved.

    The pipeline writes a stage heartbeat as it goes, so "slow" and "dead" are
    distinguishable. Twenty minutes is deliberately generous: coding and deck
    rendering on a large file legitimately take a while, and calling a healthy
    run dead is worse than showing a spinner slightly too long.

    A malformed or missing timestamp counts as NOT stale — a parse error must
    never mask a run that is still going.
    """
    if study.status not in _RUNNING_STAGES:
        return False
    stamp = study.updated_at or study.created_at
    if not stamp:
        return False
    try:
        started = datetime.fromisoformat(stamp)
    except (ValueError, TypeError):
        return False
    return datetime.now() - started > _STUDY_STALE_AFTER


# Database errors that mean "the connection died", not "your file is wrong".
_CONNECTION_ERROR_MARKERS = (
    "ssl connection has been closed",
    "server closed the connection",
    "connection already closed",
    "terminating connection",
    "could not connect to server",
    "operationalerror",
)


def _upload_error_message(exc: Exception) -> str:
    """What to tell the person who just uploaded a file.

    The raw exception was being handed straight to the browser. On a dropped
    database connection that meant a wall of SQLAlchemy internals — the full
    SELECT, the table columns, and the user's own email address — presented as
    though their spreadsheet were at fault. It is neither their fault nor their
    business: the detail belongs in the server log, which already has it with a
    traceback.
    """
    text = str(exc).lower()
    if any(marker in text for marker in _CONNECTION_ERROR_MARKERS):
        return (
            "The database connection dropped while saving your upload. "
            "Nothing was lost — please try again."
        )
    return "The upload could not be processed. The error has been logged."


def _run_orchestration_job(study_id: str, client_name: str, file_path: Path) -> None:
    """The full pipeline for one study, on a fresh DB session (daemon thread).

    Shared by upload and re-process. On success, persists deck paths plus the
    durable artifact copies (datamap/crosstabs/coding JSON) so the study
    remains fully servable after the ephemeral disk is wiped.
    """
    from api.db import SessionLocal, set_study_artifacts, touch_study_stage

    job_db = SessionLocal()
    try:
        orchestrator = StudyOrchestrator(work_dir=STUDIES_DIR)

        def _stage(name: str) -> None:
            logger.info(f"[{study_id}] stage: {name}")
            touch_study_stage(job_db, study_id, name)

        result = orchestrator.run_study(
            client_name=client_name,
            source_file=file_path,
            on_stage=_stage,
            auto_approve_classify=AUTO_APPROVE_CLASSIFY,
            auto_approve_qc=AUTO_APPROVE_QC,
            require_delivery_approval=REQUIRE_DELIVERY_APPROVAL,
        )
        logger.info(f"[{study_id}] Orchestration complete: {result.stage}")

        metadata = {
            "classification_confidence": result.source_file.classified_confidence,
        }
        if result.qc_report:
            metadata["qc_passed"] = result.qc_report.passed
            metadata["qc_issues"] = result.qc_report.issues
            metadata["qc_warnings"] = result.qc_report.warnings
        update_study_status(job_db, study_id, result.stage.value, metadata)

        def _read(path) -> str | None:
            return Path(path).read_text() if path and Path(path).exists() else None

        # The data map is persisted on its OWN, not alongside the decks. It is
        # produced at intake, minutes before any deck exists, and a run that
        # pauses at the QC gate — or errors during coding, or is killed — never
        # reaches deck rendering. Gating it on excel_deck_path threw away a data
        # map that had parsed perfectly, which then silently removed the
        # acceleration from every questionnaire uploaded to the same project:
        # the extractor found no data map and paid full price for questions the
        # script had already described.
        datamap_json = _read(result.datamap_path)
        if datamap_json:
            update_study_paths(job_db, study_id, datamap_json=datamap_json)
            logger.info(f"[{study_id}] Data map stored ({len(datamap_json)} bytes)")

        if result.excel_deck_path:
            update_study_paths(
                job_db,
                study_id,
                excel_path=str(result.excel_deck_path),
                pptx_path=str(result.pptx_deck_path) if result.pptx_deck_path else None,
            )
            set_study_artifacts(
                job_db,
                study_id,
                crosstabs_json=_read(result.crosstabs_path),
                coding_json=_read(result.coding_path),
            )
            logger.info(f"[{study_id}] Updated paths + durable artifacts")

        for gate, (approver, _timestamp) in result.approval_chain.items():
            approve_gate(job_db, study_id, gate, approver)
            logger.info(f"[{study_id}] Approved {gate} by {approver}")

        _archive_source_file(job_db, study_id, file_path)

    except Exception as e:
        logger.error(f"[{study_id}] Orchestration failed: {e}", exc_info=True)
        update_study_status(job_db, study_id, "error", {"error": str(e)})
    finally:
        job_db.close()


@router.post("/studies/upload", response_model=UploadResponse)
async def upload_study(
    file: UploadFile = File(...),
    client_name: str = Form(None),
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Upload SPSS/CSV file and start orchestration. (Owner/Analyst-only)"""
    try:
        # Validate file type
        if not file.filename.endswith((".sav", ".csv", ".xlsx")):
            raise HTTPException(
                status_code=400, detail="Only SPSS (.sav), CSV, and Excel files supported"
            )

        # Create study record
        study_id = f"study_{uuid4().hex[:8]}"
        project_name = (client_name or "").strip() or file.filename.rsplit(".", 1)[0]
        study = create_study(db, study_id, client_name=project_name)
        update_study_status(db, study_id, study.status, {"uploaded_by": user.name})

        # Stream to disk in chunks — never hold the whole upload in memory.
        # A large SPSS/CSV file loaded via file.read() would sit in RAM twice
        # over (once as the upload buffer, again when pyreadstat/pandas loads
        # it), which is how an 8GB-RAM box gets pushed into swap on a
        # multi-GB file.
        upload_dir = STUDIES_DIR / study_id
        upload_dir.mkdir(parents=True, exist_ok=True)
        file_path = upload_dir / file.filename
        chunk_size = 1024 * 1024  # 1 MB
        total_bytes = 0
        with open(file_path, "wb") as f:
            while chunk := await file.read(chunk_size):
                f.write(chunk)
                total_bytes += len(chunk)
        logger.info(f"[{study_id}] File uploaded: {file_path} ({total_bytes} bytes)")

        # Start orchestration in background (fresh DB session — request session
        # closes when this handler returns)
        client_name = study.client_name
        thread = threading.Thread(
            target=_run_orchestration_job, args=(study_id, client_name, file_path), daemon=True
        )
        thread.start()

        return UploadResponse(
            study_id=study_id,
            status="classifying",
            message=f"File {file.filename} uploaded. Processing started.",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Upload failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=_upload_error_message(e)) from e


@router.post("/studies", response_model=StudyResponse)
def create_empty_study(
    request: CreateStudyRequest,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Create a data-less study for the survey-first path. (Owner/Analyst-only)

    No data file, no orchestration — the study starts in "authoring" so the
    questionnaire-upload or survey-builder flows can take over. It has no
    excel_path; the analysis tabs light up later via a data upload or a T11
    fielding materialize (which sets excel_path + status "rendered"). Returns the
    same shape as GET /studies/{id} so the frontend can navigate straight to it.
    """
    study_id = f"study_{uuid4().hex[:8]}"
    client_name = (request.client_name or "").strip() or "Untitled project"
    study = create_study(db, study_id, client_name=client_name, status="authoring")
    if request.kind:
        study = update_study_status(db, study_id, "authoring", {"kind": request.kind})

    return StudyResponse(
        id=study.id,
        client_name=study.client_name,
        status=study.status,
        created_at=study.created_at,
        updated_at=study.updated_at,
        metadata=StudyMetadata(**study.study_metadata),
        approval_chain=[],
        excel_path=study.excel_path,
        pptx_path=study.pptx_path,
        source_filename=study.source_filename,
    )


_DATA_SUFFIXES = (".sav", ".csv", ".xlsx")


@router.post("/studies/{study_id}/data")
async def attach_data_file(
    study_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Attach a fielded data file to an existing project. (Owner/Analyst)

    A project started from its questionnaire had no way to receive the client's
    data export afterwards: the data upload only existed as "start a new
    project", so the questionnaire and its data ended up in two projects and
    the data-vs-questionnaire check had nothing to run on. This puts the file
    in THIS project and runs the same processing a data upload runs. The
    questionnaire is untouched; a previous data file is replaced.
    """
    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")
    name = Path(file.filename or "").name
    if not name.lower().endswith(_DATA_SUFFIXES):
        raise HTTPException(
            status_code=400,
            detail="Only SPSS (.sav), CSV and Excel (.xlsx) data files are supported",
        )
    upload_dir = STUDIES_DIR / study_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    # One data file per project: re-processing picks the file it finds here.
    for old in upload_dir.iterdir():
        if old.is_file() and old.suffix.lower() in _DATA_SUFFIXES:
            old.unlink()
    file_path = upload_dir / name
    with open(file_path, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            f.write(chunk)
    update_study_status(db, study_id, "classifying", {"data_attached_by": user.name})
    threading.Thread(
        target=_run_orchestration_job, args=(study_id, study.client_name, file_path), daemon=True
    ).start()
    logger.info(f"[{study_id}] Data file attached by {user.name}: {name}")
    return {"study_id": study_id, "status": "classifying", "source": name}


@router.get("/studies/{study_id}/fielded-data")
def fielded_data_summary(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """The project's fielded data at a glance: file, respondents, variables.

    Data fielded on another platform arrives as an export, and the field
    monitor showed nothing about it — only links run here — so a project whose
    fieldwork was finished looked as if it had none. How each interview ended
    is in `/data-field-report`, shown right below this on the field monitor.
    """
    import polars as pl

    from api.routes.chat import _study_dir
    from src.core.models import DataMap
    from src.engines.qc.discrepancy import _disposition_labels

    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")
    parquet = _study_dir(study) / "data.parquet"
    if not study.datamap_json or not parquet.exists():
        return {"available": False, "source_filename": study.source_filename,
                "processing": study.status == "classifying"}

    status_column, _meaning = _disposition_labels(DataMap.from_json(study.datamap_json))
    columns = pl.read_parquet_schema(parquet)
    return {
        "available": True,
        "processing": study.status == "classifying",
        "source_filename": study.source_filename,
        "updated_at": study.updated_at,
        "n_variables": len(columns),
        "n_respondents": pl.scan_parquet(parquet).select(pl.len()).collect().item(),
        "status_variable": status_column if status_column in columns else None,
    }


@router.get("/studies/{study_id}/data-field-report")
def data_field_report_route(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """The field report for data fielded elsewhere, read from its export.

    The same numbers the field monitor shows for our own link — dispositions,
    incidence, which screen-out removed people, quota cells, interview length,
    drop-off, devices, soft-launch quality — so an uploaded export is treated
    as the fieldwork it is, not as a spreadsheet to profile.
    """
    import polars as pl

    from api.db import list_questionnaires_for_study
    from api.routes.chat import _study_dir
    from src.core.models import DataMap
    from src.core.survey_definition import SurveyDefinition
    from src.engines.fielding.data_report import data_field_report
    from src.engines.qc.cleaning import collect_flags
    from src.engines.qc.discrepancy import completes_only
    from src.engines.questionnaire.label_merge import (
        questionnaire_to_data_codes,
        translate_to_data_codes,
    )
    from src.engines.questionnaire.linker import link_questions_to_datamap

    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")
    parquet = _study_dir(study) / "data.parquet"
    if not study.datamap_json or not parquet.exists():
        return {"available": False, "processing": study.status == "classifying"}

    datamap = DataMap.from_json(study.datamap_json)
    df = pl.read_parquet(parquet)
    survey, links = None, None
    questionnaires = list_questionnaires_for_study(db, study_id)
    definition = questionnaires[0].definition_json if questionnaires else None
    if definition:
        # Answers are counted in the script's codes, labelled by the document.
        survey = SurveyDefinition.from_json(definition)
        report = link_questions_to_datamap(survey, datamap)
        survey = translate_to_data_codes(
            survey, questionnaire_to_data_codes(survey, datamap, report)
        )
        links = report.links

    # Soft-launch quality is judged on the completes: a screen-out's speed or
    # blank grid says nothing about the data that will be delivered.
    completes, _fielded = completes_only(df, datamap)
    flags, _ = collect_flags(completes, datamap)
    # ponytail: computed per request (~0.3s on 250 rows); cache on the run dir
    # if exports in the tens of thousands make this slow.
    return {
        "available": True,
        "source_filename": study.source_filename,
        "has_questionnaire": survey is not None,
        **data_field_report(df, datamap, survey, links, flags),
    }


@router.post("/studies/{study_id}/reprocess")
def reprocess_study(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Re-run the full pipeline for an existing study. (Owner/Analyst)

    The free-tier recovery path: Render restarts wipe the disk, but the
    original upload is archived in Postgres — restore it and run the same
    orchestration as a fresh upload (new run dir, new decks, new durable
    artifacts; the study keeps its id, share links and approval history).
    Also how a study picks up pipeline improvements (new QC checks, coding)
    without re-uploading.
    """
    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")

    upload_dir = STUDIES_DIR / study_id
    # Prefer the on-disk original when it survived; else restore from the DB.
    file_path = None
    if upload_dir.exists():
        candidates = [p for p in upload_dir.iterdir() if p.suffix in (".sav", ".csv", ".xlsx")]
        if candidates:
            file_path = candidates[0]
    if file_path is None:
        file_path = _restore_source_file(study, upload_dir)
    if file_path is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "Original file is gone (disk wiped) and no archived copy exists "
                "for this study — upload the file again as a new project"
            ),
        )

    update_study_status(db, study_id, "classifying", {"reprocessed_by": user.name})
    threading.Thread(
        target=_run_orchestration_job,
        args=(study_id, study.client_name, file_path),
        daemon=True,
    ).start()
    logger.info(f"[{study_id}] Re-process started by {user.name} from {file_path.name}")
    return {"study_id": study_id, "status": "classifying", "source": file_path.name}


@router.get("/studies", response_model=StudyListResponse)
def list_all_studies(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    status: str = Query(None),
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """List all studies. (Authenticated users only)"""
    studies, total = list_studies(db, skip=skip, limit=limit)

    # Filter by status if provided
    if status:
        studies = [s for s in studies if s.status == status]

    study_responses = []
    for s in studies:
        approval_records = [
            {"gate": gate, "approved_by": data["approved_by"], "approved_at": data["approved_at"]}
            for gate, data in (s.approval_chain or {}).items()
        ]

        metadata = StudyMetadata(**s.study_metadata)
        study_response = StudyResponse(
            id=s.id,
            client_name=s.client_name,
            status=s.status,
            created_at=s.created_at,
            updated_at=s.updated_at,
            metadata=metadata,
            approval_chain=approval_records,
            excel_path=s.excel_path,
            pptx_path=s.pptx_path,
            source_filename=s.source_filename,
        )
        study_responses.append(study_response)

    return StudyListResponse(
        studies=study_responses,
        total=total,
        page=skip // limit + 1,
        limit=limit,
    )


@router.get("/studies/{study_id}", response_model=StudyResponse)
def get_study_detail(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """Get study detail. (Authenticated users only)"""
    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")

    # A study whose worker died would otherwise poll for ever behind a spinner,
    # showing "QC pending…" with nothing behind it. Flip it to a state the UI
    # can act on, and say what to do about it.
    if _is_stale_study(study):
        stage = (study.study_metadata or {}).get("stage")
        study = update_study_status(
            db,
            study_id,
            "error",
            {
                "error": (
                    f"Processing stopped{f' during {stage}' if stage else ''} and did not "
                    "resume — the server most likely restarted. Use Re-process to run it again."
                )
            },
        )

    approval_records = [
        {"gate": gate, "approved_by": data["approved_by"], "approved_at": data["approved_at"]}
        for gate, data in (study.approval_chain or {}).items()
    ]

    metadata = StudyMetadata(**study.study_metadata)
    return StudyResponse(
        id=study.id,
        client_name=study.client_name,
        status=study.status,
        created_at=study.created_at,
        updated_at=study.updated_at,
        metadata=metadata,
        approval_chain=approval_records,
        excel_path=study.excel_path,
        pptx_path=study.pptx_path,
        source_filename=study.source_filename,
    )


@router.post("/studies/{study_id}/approve/{gate}")
def approve_study_gate(
    study_id: str,
    gate: str,
    request: ApprovalRequest,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """Approve a study gate. (Authenticated users only; delivery gate Owner-only)"""
    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")

    if gate not in ("classify", "qc", "delivery"):
        raise HTTPException(status_code=400, detail="Invalid gate")

    # Delivery sign-off requires Owner role
    if gate == "delivery" and UserRole(user.role) != UserRole.OWNER:
        raise HTTPException(
            status_code=403,
            detail="Only Owners can sign off on delivery",
        )

    # Use the authenticated user's name for approval, not the request body
    approve_gate(db, study_id, gate, user.name)
    logger.info(f"[{study_id}] Gate '{gate}' approved by {user.name} ({user.email})")

    return {"status": "approved", "gate": gate}


@router.get("/studies/{study_id}/crosstabs")
def get_crosstabs(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """Return the computed tables (JSON) for the web viewer. (Authenticated users only)

    Reads the run dir's crosstabs.json when the disk still has it, else falls
    back to the durable DB copy — tables stay viewable after a restart even
    though the parquet/decks are gone."""
    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")
    if not study.excel_path:
        raise HTTPException(status_code=404, detail="Tables not yet computed")

    import json

    crosstabs_file = Path(study.excel_path).parent / "crosstabs.json"
    if crosstabs_file.exists():
        return json.loads(crosstabs_file.read_text())
    if getattr(study, "crosstabs_json", None):
        return json.loads(study.crosstabs_json)
    raise HTTPException(status_code=404, detail="No table data for this study")


@router.get("/studies/{study_id}/tables/{table_id}/provenance")
def get_cell_provenance(
    study_id: str,
    table_id: str,
    row: int = Query(...),
    col: int = Query(...),
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """The thread behind one table cell: base, question, cleaning, weight, data file."""
    from api.db import list_questionnaires_for_study
    from api.routes.cleaning import _decisions
    from src.core.survey_definition import SurveyDefinition
    from src.engines.tabs.provenance import provenance

    payload = get_crosstabs(study_id, db, user)
    study = get_study(db, study_id)
    table = next((t for t in payload["tables"] if t.get("id") == table_id), None)
    if table is None:
        raise HTTPException(status_code=404, detail="No such table in this study")
    if not 0 <= row < len(table["rows"]) or not 0 <= col < len(table["columns"]):
        raise HTTPException(status_code=404, detail="No such cell in this table")

    removed = {k: d["reason_code"] or "other" for k, d in _decisions(db, study_id).items()
               if d["decision"] == "remove"}
    meta = study.study_metadata or {}
    data_file = {"name": study.source_filename, "uploaded_at": study.created_at,
                 "uploaded_by": meta.get("uploaded_by")}
    run_dir = Path(study.excel_path).parent
    for name in ("data_raw.parquet", "data.parquet"):  # raw = the file as uploaded
        if (run_dir / name).exists():
            import polars as pl

            data_file["n_rows"] = pl.scan_parquet(run_dir / name).select(pl.len()).collect().item()
            break
    q = next(iter(list_questionnaires_for_study(db, study_id)), None)
    survey = SurveyDefinition.from_json(q.definition_json) if q and q.definition_json else None
    return provenance(db, study_id, payload, table, row, col,
                      removed=removed, data_file=data_file, survey=survey)


@router.get("/studies/{study_id}/findings")
def get_findings(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """Findings read off the computed tables, strongest first, each number cited
    to its table, column and base. (Authenticated, like the tables themselves.)"""
    tables = get_crosstabs(study_id, db, user)["tables"]
    return {"n_tables": len(tables), "findings": _findings(db, study_id, tables)}


def _findings(db: Session, study_id: str, tables: list[dict]) -> list[dict]:
    from api.db import list_questionnaires_for_study
    from src.core.survey_definition import SurveyDefinition
    from src.engines.analysis.findings import findings

    questions: dict[str, str] = {}
    screeners: set[str] = set()
    q = next(iter(list_questionnaires_for_study(db, study_id)), None)
    if q is not None and q.definition_json:
        survey = SurveyDefinition.from_json(q.definition_json)
        questions = {x.qid: x.text for x in survey.questions}
        screeners = {x.qid for x in survey.questions if x.terminate_if}
    return findings(tables, questions, screeners)


# --- Cited report: every sentence points at cells; code decides whether it stands ---

_REWRITE_PROMPT = (
    "Rewrite each survey finding as one clear sentence for a client. Keep every number "
    "exactly as written and add none. Keep the meaning. Reply as JSON "
    '{{"sentences": [{{"id": "...", "text": "..."}}]}}.\n\n{items}')
_REWRITE_SCHEMA = {"type": "object", "properties": {"sentences": {"type": "array", "items": {
    "type": "object", "properties": {"id": {"type": "string"}, "text": {"type": "string"}},
    "required": ["id", "text"]}}}, "required": ["sentences"]}


def _set_meta(db: Session, study_id: str, **fields) -> None:
    study = get_study(db, study_id)
    study.study_metadata = {**(study.study_metadata or {}), **fields}
    db.commit()


def _report_of(study) -> dict:
    return (study.study_metadata or {}).get("report") or {"sentences": [], "drafted_at": None}


def _sentence(sid: str, text: str, cites: list[dict], tables: list[dict], source: str) -> dict:
    from src.engines.analysis.citations import check

    return {"id": sid, "text": text, "cites": cites, **check(text, cites, tables), "source": source}


@handler("report.draft")
def _draft_report(payload: dict, db: Session) -> dict:
    """Queue handler: the top findings as cited sentences, a private model's rewrite
    kept only where the checker passes it against that finding's own cells."""
    from src.engines.analysis.citations import check, to_cites

    study_id = payload["study_id"]
    job_id = payload.get("job_id")
    try:
        tables = get_crosstabs(study_id, db, None)["tables"]
        top = [f for f in _findings(db, study_id, tables) if f["kind"] != "sample"][:12]
        if not top:
            raise ValueError("no findings")
        base = [{"id": f"s{n}", "cites": to_cites(f["cites"], tables),
                 "text": f["text"] + (f" {f['caution']}" if f.get("caution") else "")}
                for n, f in enumerate(top, 1)]
        rewrites: dict[str, str] = {}
        try:  # no private model, or a bad answer: the findings' own texts stand
            from src.ai import router

            items = json.dumps([{"id": b["id"], "text": b["text"]} for b in base],
                               ensure_ascii=False)
            reply = router.run_private(_REWRITE_PROMPT.format(items=items), _REWRITE_SCHEMA)
            rewrites = {str(i.get("id")): str(i.get("text", "")).strip()
                        for i in reply.content.get("sentences") or []}
        except Exception as exc:
            logger.info(f"[{study_id}] report rewrite skipped: {exc}")
        sentences = []
        for b in base:
            new = rewrites.get(b["id"])
            if new and check(new, b["cites"], tables)["ok"]:
                sentences.append(_sentence(b["id"], new, b["cites"], tables, "model"))
            else:
                sentences.append(_sentence(b["id"], b["text"], b["cites"], tables, "finding"))
        _set_meta(db, study_id, report={"sentences": sentences,
                                        "drafted_at": datetime.now().isoformat()},
                  report_job={"state": "done", "job_id": job_id})
    except Exception:  # recorded for the panel; never left "running"
        logger.exception(f"[{study_id}] report draft failed")
        db.rollback()
        message = "The report could not be drafted. Check that the tables are computed."
        _set_meta(db, study_id, report_job={"state": "failed", "job_id": job_id,
                                            "error": message})
        emit(db, study_id, "job.failed", {"kind": "report.draft", "error": message})
        raise JobError(message) from None
    emit(db, study_id, "job.done", {"kind": "report.draft"})
    return {"sentences": len(sentences)}


class SentenceBody(BaseModel):
    text: str
    cites: list[dict] | None = None


@router.post("/studies/{study_id}/report/draft", status_code=202)
def draft_report(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Draft the cited report in the background; poll GET /report. (Owner/Analyst)"""
    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")
    if ((study.study_metadata or {}).get("report_job") or {}).get("state") == "running":
        raise HTTPException(status_code=409, detail="A report is already being drafted")
    get_crosstabs(study_id, db, user)  # 404 with a reason when there are no tables yet
    _set_meta(db, study_id, report_job={"state": "running"})
    job = enqueue(db, "report.draft", {"study_id": study_id},
                  study_id=study_id, max_attempts=1, created_by=user.id)
    db.refresh(study)
    running = (study.study_metadata or {}).get("report_job") or {}
    if running.get("state") == "running":  # the worker may already have finished it
        _set_meta(db, study_id, report_job={**running, "job_id": job.id})
    return {"state": "running"}


@router.get("/studies/{study_id}/report")
def get_report(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """The cited report and any drafting in progress. (Authenticated)"""
    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")
    return {**_report_of(study), "job": (study.study_metadata or {}).get("report_job")}


def _save_sentence(db: Session, study_id: str, sid: str | None, body: SentenceBody) -> dict:
    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")
    report = _report_of(study)
    sentences = list(report["sentences"])
    old = next((x for x in sentences if x["id"] == sid), None)
    if sid and old is None:
        raise HTTPException(status_code=404, detail="No such sentence in this report")
    tables = get_crosstabs(study_id, db, None)["tables"]
    cites = body.cites if body.cites is not None else (old["cites"] if old else [])
    new = _sentence(sid or f"s_{uuid4().hex[:6]}", body.text.strip(), cites, tables, "person")
    sentences = [new if x["id"] == new["id"] else x for x in sentences]
    if old is None:
        sentences.append(new)
    _set_meta(db, study_id, report={**report, "sentences": sentences})
    return new


@router.put("/studies/{study_id}/report/sentences/{sid}")
def edit_sentence(
    study_id: str, sid: str, body: SentenceBody,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Replace a sentence and re-check it; a failed check is stored with its problems."""
    return _save_sentence(db, study_id, sid, body)


@router.post("/studies/{study_id}/report/sentences")
def add_sentence(
    study_id: str, body: SentenceBody,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    return _save_sentence(db, study_id, None, body)


@router.delete("/studies/{study_id}/report/sentences/{sid}")
def delete_sentence(
    study_id: str, sid: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")
    report = _report_of(study)
    if not any(x["id"] == sid for x in report["sentences"]):
        raise HTTPException(status_code=404, detail="No such sentence in this report")
    _set_meta(db, study_id, report={**report, "sentences": [
        x for x in report["sentences"] if x["id"] != sid]})
    return {"deleted": sid}


def _run_artifacts(study, what: str) -> tuple[Path, Path]:
    """(data.parquet, datamap.json) for a processed study, or 404 with a reason."""
    if not study.excel_path:
        raise HTTPException(status_code=404, detail=f"{what} not yet processed")
    run_dir = Path(study.excel_path).parent
    parquet, datamap = run_dir / "data.parquet", run_dir / "datamap.json"
    if not parquet.exists() or not datamap.exists():
        raise HTTPException(
            status_code=404,
            detail=f"{what} run predates artifact persistence — re-run the study",
        )
    return parquet, datamap


@router.get("/studies/{study_id}/tracking/{baseline_id}")
def get_tracking(
    study_id: str,
    baseline_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """Wave-over-wave comparison: this study vs a baseline wave. (Authenticated)

    Deterministic Total-level deltas per shared variable, sig-tested with the
    same pooled z-test as the banner columns (src/engines/tabs/tracking.py).
    Variables the two waves can't honestly compare (code drift, small bases)
    come back under `skipped` with reasons rather than silently vanishing.
    """
    from src.core.models import DataMap
    from src.engines.tabs.tracking import compare_waves

    current = get_study(db, study_id)
    baseline = get_study(db, baseline_id)
    if not current or not baseline:
        raise HTTPException(status_code=404, detail="Study not found")
    cur_parquet, cur_dm = _run_artifacts(current, f"Study {study_id}")
    base_parquet, base_dm = _run_artifacts(baseline, f"Baseline {baseline_id}")

    report = compare_waves(
        base_parquet,
        DataMap.from_json(base_dm.read_text()),
        cur_parquet,
        DataMap.from_json(cur_dm.read_text()),
        baseline_id=baseline_id,
        current_id=study_id,
    )
    return report.to_dict()


def _stack_sources(db: Session, study_id: str, others: list[str]):
    """(name, parquet, datamap path) for this study then each other one, or 404/422."""
    ids = [study_id, *dict.fromkeys(o for o in others if o != study_id)]
    if len(ids) < 2:
        raise HTTPException(status_code=422, detail="Name at least one other study to stack with")
    out = []
    for sid in ids:
        study = get_study(db, sid)
        if not study:
            raise HTTPException(status_code=404, detail=f"Study {sid} not found")
        parquet, dm = _run_artifacts(study, f"Study {sid}")
        out.append((study.client_name or sid, sid, parquet, dm))
    return out


@router.get("/studies/{study_id}/stack")
def stack_check(
    study_id: str,
    with_: list[str] = Query(default=[], alias="with"),
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """What differs between this study and other waves or countries of it: variables
    not in every file, codes added or missing, codes that changed meaning. Data maps
    only, no answers. (Authenticated)"""
    from src.core.models import DataMap
    from src.engines.tabs.stacking import drift

    sources = _stack_sources(db, study_id, with_)
    return drift([(name, DataMap.from_json(dm.read_text())) for name, _sid, _p, dm in sources])


@router.get("/studies/{study_id}/stack.sav")
def stack_download(
    study_id: str,
    with_: list[str] = Query(default=[], alias="with"),
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """One SPSS file of every respondent from these studies, SOURCE first. Refused while
    a question's codes mean different things in different files. (Gated like a download
    for every study in it.)"""
    import shutil
    import tempfile

    import polars as pl
    from starlette.background import BackgroundTask

    from api.routes.materialize import data_access
    from src.adapters.spss import write_sav
    from src.core.models import DataMap
    from src.engines.tabs.stacking import drift, stack

    sources = _stack_sources(db, study_id, with_)
    for _name, sid, _p, _dm in sources:
        decision = data_access(db, sid, user)
        if not decision.allowed:
            raise HTTPException(status_code=403, detail=f"{sid}: {decision.reason}")
    maps = [(name, DataMap.from_json(dm.read_text())) for name, _sid, _p, dm in sources]
    report = drift(maps)
    if report["n_breaking"]:
        raise HTTPException(status_code=409, detail=(
            f"{report['n_breaking']} questions mean different things in different files; "
            "fix their codes before stacking"))
    data, datamap = stack([(name, pl.read_parquet(p), dm)
                           for (name, _sid, p, _d), (_n, dm) in zip(sources, maps, strict=True)])
    folder = Path(tempfile.mkdtemp())
    path = folder / f"{study_id}_stacked.sav"
    write_sav(data.to_pandas(), datamap, path)
    # Respondent data: gone from disk once sent.
    return FileResponse(path, filename=path.name, media_type="application/octet-stream",
                        background=BackgroundTask(shutil.rmtree, folder, ignore_errors=True))


def _coding_file(study) -> Path:
    """coding.json lives in the orchestrator run dir (beside the decks) — the
    same location convention as crosstabs.json/datamap.json, NOT STUDIES_DIR."""
    if not study.excel_path:
        raise HTTPException(status_code=404, detail="Study not yet processed")
    path = Path(study.excel_path).parent / "coding.json"
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=(
                "No verbatim coding for this study "
                "(no open-ends, or run predates coding persistence)"
            ),
        )
    return path


def _uncoded_open_ends_payload(study) -> dict:
    """Coding payload listing a study's open-ends even when coding never ran.

    A reviewer opening the Coding page should always see what the study's
    open-end questions ARE and that they aren't coded (with why to the extent
    we know), not a bare 404 — an empty page teaches nothing. Built from the
    datamap (durable DB copy first, run-dir file as fallback)."""
    datamap_json = getattr(study, "datamap_json", None)
    if not datamap_json and study.excel_path:
        path = Path(study.excel_path).parent / "datamap.json"
        if path.exists():
            datamap_json = path.read_text()
    if not datamap_json:
        return {}

    from src.core.models import DataMap, VarType

    try:
        datamap = DataMap.from_json(datamap_json)
    except Exception:
        return {}
    # The questions people wrote answers to, read from the data when it is on disk;
    # every TEXT column listed uuid, dates and user agents as "not coded".
    from src.engines.qc.variable_roles import classify_variable, open_end_columns

    parquet = Path(study.excel_path).parent / "data.parquet" if study.excel_path else None
    if parquet and parquet.exists():
        import polars as pl

        names = set(open_end_columns(pl.read_parquet(parquet), datamap))
    else:
        names = {v.name for v in datamap.variables if v.type == VarType.TEXT
                 and classify_variable(v.name, v.label or "", v.question_ref)
                 in ("question", "other_specify")}
    return {
        v.name: {
            "question_var": v.name,
            "label": v.label,
            "note": (
                "Not coded yet. Use “Code open ends” to build a codeframe and code "
                "every answer."
            ),
            "codeframe": {"allow_multi": True, "version": 0, "codes": []},
            "summary": {"n_auto_coded": 0, "n_needs_review": 0, "confidence_mean": 0.0},
            "review_queue": [],
        }
        for v in datamap.variables
        if v.name in names
    }


@router.get("/studies/{study_id}/coding")
def get_coding(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """Codeframes + review queues for every coded open-end. (Authenticated users)

    Serves the coding.json the orchestrator persisted at run time — no LLM call
    happens here. When no coding.json exists (older run, or coding failed), the
    study's open-end variables are still listed with a not-coded note so the
    page is informative rather than empty; only a study with genuinely zero
    open-ends 404s.
    """
    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")
    import json

    try:
        return json.loads(_coding_file(study).read_text())
    except HTTPException as exc:
        # Disk copy gone (restart) -> durable DB copy; else list the study's
        # open-ends as not-coded; else the original 404.
        if getattr(study, "coding_json", None):
            return json.loads(study.coding_json)
        fallback = _uncoded_open_ends_payload(study)
        if fallback:
            return fallback
        raise exc


def _set_coding_job(db: Session, study_id: str, job: dict) -> None:
    study = get_study(db, study_id)
    study.study_metadata = {**(study.study_metadata or {}), "coding_job": job}
    db.commit()


@handler("coding.run")
def _run_coding(payload: dict, db: Session) -> dict:
    """Queue handler: code the chosen open questions and merge them into coding.json."""
    import polars as pl

    from api.db import set_study_artifacts
    from src.core.models import DataMap
    from src.engines.coding.runner import code_open_ends
    from src.engines.qc.discrepancy import completes_only

    study_id, variables = payload["study_id"], payload["variables"]
    parquet, datamap = Path(payload["parquet"]), Path(payload["datamap"])
    try:
        dm = DataMap.from_json(datamap.read_text())
        df, _fielded = completes_only(pl.read_parquet(parquet), dm)
        _coded, blocks, failed = code_open_ends(df, dm, variables)
        study = get_study(db, study_id)
        existing = json.loads(study.coding_json) if getattr(study, "coding_json", None) else {}
        merged = {k: v for k, v in existing.items() if k not in blocks} | blocks
        text = json.dumps(merged, indent=1)
        (parquet.parent / "coding.json").write_text(text)
        set_study_artifacts(db, study_id, coding_json=text)
        _set_coding_job(db, study_id, {"state": "done", "variables": variables,
                                       "coded": sorted(blocks), "failed": failed,
                                       "job_id": payload.get("job_id"),
                                       "finished_at": datetime.now().isoformat()})
    except Exception:  # recorded for the page; never left "running"
        logger.exception(f"[{study_id}] on-demand coding failed")
        db.rollback()
        message = "Coding stopped before it finished. It has been logged."
        _set_coding_job(db, study_id, {"state": "failed", "variables": variables,
                                       "job_id": payload.get("job_id"), "error": message})
        emit(db, study_id, "job.failed", {"kind": "coding.run", "error": message})
        raise JobError(message) from None
    emit(db, study_id, "job.done", {"kind": "coding.run", "coded": sorted(blocks)})
    return {"coded": sorted(blocks), "failed": failed}


class CodingRunRequest(BaseModel):
    variables: list[str] | None = None


@router.get("/studies/{study_id}/coding/status")
def coding_status(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """The study's open questions, which are coded, and any coding in progress."""
    import polars as pl

    from src.core.models import DataMap
    from src.engines.qc.variable_roles import open_end_columns

    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")
    parquet, datamap = _run_artifacts(study, f"Study {study_id}")
    dm = DataMap.from_json(datamap.read_text())
    df = pl.read_parquet(parquet)
    coded = set(json.loads(study.coding_json)) if getattr(study, "coding_json", None) else set()
    return {
        "open_ends": [{"variable": n, "question": (dm[n].question_text or dm[n].label or n)[:200],
                       "n_answers": int(df[n].drop_nulls().len()), "coded": n in coded}
                      for n in open_end_columns(df, dm)],
        "job": (study.study_metadata or {}).get("coding_job"),
    }


@router.post("/studies/{study_id}/coding/run", status_code=202)
def run_coding(
    study_id: str,
    body: CodingRunRequest,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Code open questions now, in the background; poll /coding/status. (Owner/Analyst)

    Builds a codeframe from a sample and codes every answer with an open model.
    """
    import polars as pl

    from src.core.models import DataMap
    from src.engines.qc.variable_roles import open_end_columns

    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")
    job = (study.study_metadata or {}).get("coding_job") or {}
    if job.get("state") == "running":
        raise HTTPException(status_code=409, detail="Coding is already running for this study")
    parquet, datamap = _run_artifacts(study, f"Study {study_id}")
    allowed = open_end_columns(pl.read_parquet(parquet), DataMap.from_json(datamap.read_text()))
    variables = body.variables or allowed
    unknown = sorted(set(variables) - set(allowed))
    if unknown:
        raise HTTPException(status_code=422, detail=f"Not open questions: {', '.join(unknown)}")
    if not variables:
        raise HTTPException(status_code=422, detail="This study has no open questions to code")
    _set_coding_job(db, study_id, {"state": "running", "variables": variables,
                                   "started_at": datetime.now().isoformat(),
                                   "started_by": user.name})
    job = enqueue(db, "coding.run",
                  {"study_id": study_id, "parquet": str(parquet), "datamap": str(datamap),
                   "variables": variables},
                  study_id=study_id, max_attempts=1, created_by=user.id)
    db.refresh(study)
    running = (study.study_metadata or {}).get("coding_job") or {}
    if running.get("state") == "running":  # the worker may already have finished it
        _set_coding_job(db, study_id, {**running, "job_id": job.id})
    return {"state": "running", "variables": variables}


@router.patch("/studies/{study_id}/coding/{variable}/{respondent_id}")
def resolve_coding_item(
    study_id: str,
    variable: str,
    respondent_id: int,
    payload: dict,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Resolve one review-queue item: accept the suggestion or correct the codes.

    Body: {"codes": [...], "status": "accepted" | "corrected"}. Persists the
    human decision into coding.json (status, final codes, who resolved it).
    NOTE (honest limitation): this updates the review record, not the study's
    data.parquet — re-tabbing with corrected codes is a follow-up; the record
    here is the source of truth a re-run would apply. (Owner/Analyst)
    """
    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")
    status = payload.get("status", "accepted")
    if status not in ("accepted", "corrected"):
        raise HTTPException(status_code=422, detail="status must be accepted|corrected")
    codes = payload.get("codes")
    if codes is not None and not isinstance(codes, list):
        raise HTTPException(status_code=422, detail="codes must be a list of code ids")

    import json

    from api.db import set_study_artifacts

    # Load from disk when it survived, else the durable DB copy.
    path = None
    try:
        path = _coding_file(study)
        coding = json.loads(path.read_text())
    except HTTPException:
        if not getattr(study, "coding_json", None):
            raise
        coding = json.loads(study.coding_json)
    variable_block = coding.get(variable)
    if variable_block is None:
        raise HTTPException(status_code=404, detail=f"No coding for variable {variable}")
    # Validate corrected codes against the variable's own codeframe so a typo'd
    # code id can't silently enter the record.
    if codes:
        known = {c["id"] for c in variable_block["codeframe"]["codes"]}
        unknown = [c for c in codes if c not in known]
        if unknown:
            raise HTTPException(
                status_code=422, detail=f"unknown code ids: {', '.join(unknown)}"
            )

    for item in variable_block["review_queue"]:
        if item["respondent_id"] == respondent_id:
            item["status"] = status
            if codes is not None:
                item["final_codes"] = codes
            item["resolved_by"] = user.name
            payload = json.dumps(coding, indent=1)
            # DB copy is authoritative; the file echo is best-effort (the run
            # dir may be gone after a restart).
            set_study_artifacts(db, study_id, coding_json=payload)
            if path is not None:
                try:
                    path.write_text(payload)
                except OSError:
                    pass
            return {"variable": variable, "respondent_id": respondent_id, "status": status}
    raise HTTPException(
        status_code=404, detail=f"respondent {respondent_id} not in {variable}'s review queue"
    )


@router.get("/studies/{study_id}/charts/{filename}")
def get_chart(
    study_id: str,
    filename: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """Serve a chart PNG generated by the navigator's generate_chart tool.

    Charts live under the study's run directory (excel_path.parent, matching
    _study_dir in api/routes/chat.py — the upload dir under STUDIES_DIR/{id}
    only holds the source file once orchestration has run). filename must
    resolve strictly inside that study's charts/ dir — reject any path
    component that could escape it (traversal via "..", absolute paths, etc).
    """
    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")

    if Path(filename).name != filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    study_dir = Path(study.excel_path).parent if study.excel_path else STUDIES_DIR / study_id
    charts_dir = (study_dir / "charts").resolve()
    chart_path = (charts_dir / filename).resolve()

    if charts_dir not in chart_path.parents or not chart_path.exists():
        raise HTTPException(status_code=404, detail="Chart not found")

    return FileResponse(chart_path, media_type="image/png", filename=filename)


@router.get("/studies/{study_id}/profile")
def get_study_profile(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """Per-variable data quality profile: missing %, type, distinct count,
    top values / range. Powers the Overview tab's Data Profile card — this
    replaces auto-displayed crosstabs as the default view; crosstabs remain
    available on demand via the chat navigator's show_table tool.
    """
    from api.routes.chat import _load_datamap, _study_dir

    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")

    study_dir = _study_dir(study)
    parquet_path = study_dir / "data.parquet"
    if not parquet_path.exists():
        return {
            "ready": False,
            "variables": [],
            "summary": {
                "n_variables": 0,
                "n_respondents": 0,
                "n_high_missing": 0,
                "n_constant": 0,
                "n_likely_duplicate_id": 0,
            },
        }

    report = data_quality(parquet_path)
    if not report.get("success"):
        raise HTTPException(status_code=500, detail=report.get("error", "Profile failed"))

    datamap = _load_datamap(study_dir, study)
    labels = {v.name: v.label for v in datamap.variables} if datamap else {}

    flags = report["flags"]
    variables = [
        {
            "name": col,
            "label": labels.get(col, ""),
            **entry,
        }
        for col, entry in report["columns"].items()
    ]

    return {
        "ready": True,
        "variables": variables,
        "summary": {
            "n_variables": len(variables),
            "n_respondents": report["row_count"],
            "n_high_missing": len(flags["high_missing_columns"]),
            "n_constant": len(flags["constant_columns"]),
            "n_likely_duplicate_id": len(flags["likely_duplicate_id_columns"]),
        },
    }


@router.get("/studies/{study_id}/download/{fmt}")
def download_deck(
    study_id: str,
    fmt: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """Download a study artifact. (Authenticated users only)

    `excel`/`pptx` are the rendered decks, `source` is the data file as it was
    uploaded, and `responses`/`spss` are the respondent-level delivery files
    from a fielded link — every disposition, with the data map alongside.
    """
    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")

    if fmt == "source":
        # The data file exactly as it was uploaded. Served from the database
        # copy so it still works after the restart that wiped the disk.
        if not study.source_file_b64 or not study.source_filename:
            raise HTTPException(
                status_code=404,
                detail=(
                    "This project's data file was uploaded before files were "
                    "archived, or was too large to keep. Nothing to download."
                ),
            )
        from api.db import decompress_archived

        return Response(
            content=decompress_archived(study.source_file_b64),
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="{study.source_filename}"'
            },
        )

    if fmt in ("responses", "spss"):
        # Respondent-level delivery files written by materialize: every
        # disposition, with the data map travelling alongside the data.
        # Respondent answers leave only under the study's data rule — this
        # door was open to any signed-in user.
        from api.routes.materialize import data_access

        decision = data_access(db, study_id, user)
        if not decision.allowed:
            raise HTTPException(status_code=403, detail=decision.reason)
        suffix = "xlsx" if fmt == "responses" else "sav"
        path = str(STUDIES_DIR / study_id / f"{study_id}_responses.{suffix}")
        media = (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            if fmt == "responses"
            else "application/x-spss-sav"
        )
    elif fmt == "excel":
        path = study.excel_path
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif fmt == "pptx":
        path = study.pptx_path
        media = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    else:
        raise HTTPException(
            status_code=400,
            detail=(
                "Format must be 'excel', 'pptx', 'source', 'responses' or 'spss'"
            ),
        )

    if not path or not Path(path).exists():
        raise HTTPException(status_code=404, detail=f"No {fmt} deck rendered for this study")

    return FileResponse(path, media_type=media, filename=Path(path).name)


@router.delete("/studies/{study_id}")
def delete_study(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER)),
):
    """Archive (soft delete) a study. (Owner-only)"""
    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")

    update_study_status(db, study_id, "archived")
    logger.info(f"[{study_id}] Archived by {user.name}")

    return {"status": "archived", "study_id": study_id}


@router.post("/studies/{study_id}/share", response_model=ShareLinkResponse)
def create_study_share_link(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Create or return existing active share link. (Owner/Analyst-only)"""
    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")

    # Check for existing active link
    existing_link = get_active_share_link_for_study(db, study_id)
    if existing_link:
        return ShareLinkResponse(
            token=existing_link.token,
            study_id=existing_link.study_id,
            created_by=existing_link.created_by,
            created_at=existing_link.created_at,
            revoked=existing_link.revoked,
        )

    # Create new link
    link = create_share_link(db, study_id, user.email)
    logger.info(f"[{study_id}] Share link created by {user.name}")

    return ShareLinkResponse(
        token=link.token,
        study_id=link.study_id,
        created_by=link.created_by,
        created_at=link.created_at,
        revoked=link.revoked,
    )


@router.post("/studies/{study_id}/share/revoke")
def revoke_study_share_link(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Revoke share link for a study. (Owner/Analyst-only)"""
    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")

    link = get_active_share_link_for_study(db, study_id)
    if not link:
        raise HTTPException(status_code=404, detail="No active share link found")

    revoked_link = revoke_share_link(db, link.token)
    logger.info(f"[{study_id}] Share link revoked by {user.name}")

    return {"status": "revoked", "token": revoked_link.token}


@router.get("/studies/{study_id}/crosscheck")
def get_crosscheck(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """The last cross-check result for this study, or null if never run.

    The result was already being stored and never read back, so leaving the
    project and returning showed an empty panel as though the check had never
    happened — the work was done, paid for, and then hidden. It is a durable
    column, so a result stays readable a week later without re-running
    anything.
    """
    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")
    if not study.crosscheck_json:
        return {"report": None}
    try:
        return {"report": json.loads(study.crosscheck_json)}
    except (TypeError, ValueError) as exc:
        # A corrupt stored result must not break the page; re-running fixes it.
        logger.warning(f"[{study_id}] stored cross-check unreadable: {exc}")
        return {"report": None}


@router.post("/studies/{study_id}/crosscheck")
def crosscheck_study(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Check the collected data against the questionnaire it was collected under.

    This is the step that joins the two halves of the tool. The standard
    pipeline validates a data file *on its own terms* — speeders, duplicates,
    straightliners — which says nothing about whether the data matches the
    instrument the client signed off. That comparison needs both, so it is an
    explicit action rather than something that silently happens on upload:
    a study may have data long before its questionnaire is approved, and
    running this against an unreviewed extraction would report the parser's
    mistakes as the client's.

    Deterministic throughout — no model is called. Every finding names the
    question, the count and example row numbers so a researcher can open the
    file and see it for themselves.
    """
    from api.db import (
        list_questionnaires_for_study,
        record_audit,
        set_study_artifacts,
        update_study_paths,
    )
    from src.core.models import DataMap
    from src.core.survey_definition import SurveyDefinition
    from src.engines.qc.base_gaps import find_audiences
    from src.engines.qc.base_gaps import summarise as summarise_base_gaps
    from src.engines.qc.condition_audit import audit_conditions
    from src.engines.qc.discrepancy import check_against_questionnaire
    from src.engines.qc.variable_roles import ROLE_LABELS, summarize, unexplained
    from src.engines.questionnaire.datamap_recovery import reconcile, recover_missing
    from src.engines.questionnaire.label_merge import (
        align_codes_to_questionnaire,
        questionnaire_to_data_codes,
        remap_data_codes,
        translate_to_data_codes,
    )
    from src.engines.questionnaire.linker import (
        cross_validate_routing,
        link_questions_to_datamap,
    )

    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")

    questionnaires = list_questionnaires_for_study(db, study_id)
    questionnaire = questionnaires[0] if questionnaires else None
    definition_json = getattr(questionnaire, "definition_json", None) if questionnaire else None
    if not definition_json:
        raise HTTPException(
            status_code=409,
            detail="This project has no extracted questionnaire yet. Upload and "
            "review one first — the cross-check compares the data against it.",
        )
    if not study.datamap_json:
        raise HTTPException(
            status_code=409,
            detail="This project has no data file yet. Upload the data and let "
            "processing finish, then run the cross-check.",
        )

    survey = SurveyDefinition.from_json(definition_json)
    datamap = DataMap.from_json(study.datamap_json)

    # Two-way reconciliation, BEFORE linking. The data map is the script's own
    # record of what was fielded — every variable, its question text, and every
    # option label with its punch. Extraction only guesses. So a question the
    # data map describes and extraction missed is rebuilt from the data map
    # rather than guessed at a second time, and then takes part in every check
    # below. Recovered questions are flagged, never silently merged: their
    # wording comes from the script, not the client's document, and the two can
    # legitimately differ.
    agreement = reconcile(survey, datamap)

    # Link the EXTRACTED questions first, then rebuild from the data map only
    # what none of them claimed: a column an extracted question owns (Q2r5c1
    # is Q2a's) rebuilt as a second question was checked twice, wrongly.
    link_report = link_questions_to_datamap(survey, datamap)
    linked = {name for link in link_report.links for name in link.variable_names}
    recovered, recovered_links = recover_missing(survey, datamap, linked=linked)
    for question in recovered:
        survey.questions.append(question)
        if survey.blocks:
            survey.blocks[0].question_ids.append(question.qid)
    already = {link.qid for link in link_report.links}
    link_report.links.extend(link for link in recovered_links if link.qid not in already)

    # The respondent rows live in the processing run's folder (beside the deck),
    # not the upload folder. Reading only the upload folder reported "the server
    # restarted" and skipped every row-level check on any project whose data
    # came from an upload.
    from api.routes.chat import _study_dir

    parquet_path = next(
        (p for p in (_study_dir(study) / "data.parquet", STUDIES_DIR / study_id / "data.parquet")
         if p.exists()),
        STUDIES_DIR / study_id / "data.parquet",
    )

    # A CSV/Excel export that ships answer TEXT has no codes of its own, so the
    # importer numbers the answers alphabetically. The questionnaire's codes are
    # the real ones — screen-outs, routing and the tab plan are all written
    # against them — so before anything is compared, the invented numbering is
    # replaced by the authored one wherever the option labels join the two
    # unambiguously. Labels never move; only the arbitrary integer does. Codes
    # that came from SPSS or a data map sheet are the script's own and are left
    # exactly as they are.
    alignment = align_codes_to_questionnaire(survey, datamap, link_report)
    n_columns_remapped = 0
    if alignment.code_remaps:
        n_columns_remapped = remap_data_codes(parquet_path, alignment.code_remaps)
        update_study_paths(db, study_id, datamap_json=datamap.to_json())

    # Audit the conditions against what was actually scripted before reading a
    # single respondent row. A screen-out testing codes that were never punched
    # fires for nobody, and the data check would report zero violations — which
    # reads exactly like a clean result.
    # The script's own codes are never renumbered; the questionnaire is
    # translated to them instead, by answer label, for every check below. A
    # document listing a scale top-down checked against a script that coded
    # it bottom-up read half a study as answering outside its base.
    translation = questionnaire_to_data_codes(survey, datamap, link_report)
    survey = translate_to_data_codes(survey, translation)

    condition_audit = audit_conditions(survey, link_report.links, datamap)

    # The run directory is ephemeral on the free tier, so the rows can be gone
    # while the durable copies (questionnaire, data map) are intact. That used
    # to 409 — throwing away the linking, the reconciliation and the condition
    # audit that had ALREADY been computed above and need no respondent rows at
    # all, and leaving the reviewer a yellow banner where a real report belongs.
    # Everything that can still be answered is answered; the row-level checks
    # say plainly that they were not run, and why.
    has_rows = parquet_path.exists()

    # The datamap is passed deliberately: it is the authority on what each
    # column actually holds. Without it, a question the extractor mis-read as
    # coded is compared against raw values and every respondent becomes a
    # finding — measured at 715 findings on a clean 750-respondent file, versus
    # 7 real ones with the datamap supplied.
    report = check_against_questionnaire(
        survey, link_report.links, parquet_path, datamap=datamap
    )
    if not has_rows:
        report.skipped.append(
            "Every check that needs respondent rows — answer codes, single-vs-multi, "
            "numeric ranges, screen-outs — was not run: the data file is no longer on "
            "disk (the server restarted). Use Re-process to rebuild it from the "
            "archived upload, then run this again."
        )
    # "412 variables no question claimed" is not a finding — most of those are
    # hidden variables, MaxDiff design columns and system fields that are
    # SUPPOSED to be absent from a questionnaire. Report what each column is
    # for, and list only the ones genuinely worth chasing.
    # The base — who each question was supposed to be asked of — checked
    # against who actually answered it, respondent by respondent. This has
    # existed since the linker was written and was never called from here, so
    # every "[IF S2 = 2]" in a document was read, compiled, and then never
    # compared to anybody.
    routing = cross_validate_routing(survey, link_report.links, parquet_path)
    base_gap_summary = summarise_base_gaps(
        find_audiences(survey, link_report.links, parquet_path)
    )

    matched = {name for link in link_report.links for name in link.variable_names}
    by_qid = {q.qid: q for q in survey.questions}
    variable_labels = {v.name: (v.label or "") for v in datamap.variables}
    roles = summarize(datamap)
    payload = {
        **report.to_dict(),
        "linked_questions": len(link_report.links),
        "data_file_available": has_rows,
        # Questions whose document codes differ from the script's, checked in
        # the script's codes (joined by answer label).
        "questionnaire_codes_translated": {
            qid: {str(k): v for k, v in table.items()} for qid, table in translation.items()
        },
        "codes_aligned_to_questionnaire": {
            "variables": sorted(alignment.code_remaps),
            "n_values": alignment.n_codes_aligned,
            "n_columns_rewritten": n_columns_remapped,
        },
        "unmatched_questions": link_report.unmatched_questions,
        "unmatched_variables": unexplained(datamap, matched),
        "conditions": condition_audit.to_dict(),
        # Bases the DATA proves exist and the questionnaire never stated. The
        # cross-check can only verify the logic extraction found, and on a real
        # 79-question study that was six conditions — while the file showed
        # eleven distinct answer populations. Reported per POPULATION rather
        # than per question: thirty "this looks gated" lines is a wall a
        # reviewer scrolls past, and "345 respondents answered B1 to B6 and
        # nobody else did" is something somebody can act on.
        "base_gaps": base_gap_summary,
        "routing": {
            "n_checked": sum(1 for r in routing if r.checked),
            "n_unchecked": sum(1 for r in routing if not r.checked),
            "issues": [
                {
                    "qid": r.qid,
                    "agreement_pct": round(r.agreement_pct, 1),
                    "detail": r.detail,
                    "checked": r.checked,
                    "n_outside_base": r.n_outside_base,
                }
                for r in routing
                if r.flagged or not r.checked or r.n_outside_base
            ],
        },
        # A variable name in the data need not equal the qid in the document.
        # The linker falls back to matching on question TEXT, which is right
        # far more often than not — but it is a judgement, and a wrong one
        # silently checks the wrong column. Every such link is surfaced for a
        # human to confirm rather than quietly accepted.
        "needs_confirmation": [
            {
                "qid": link.qid,
                "variables": link.variable_names,
                "confidence": round(link.confidence, 2),
                "question_text": (by_qid.get(link.qid).text or "")[:160]
                if by_qid.get(link.qid)
                else "",
                "variable_label": (
                    variable_labels.get(link.variable_names[0], "")[:160]
                    if link.variable_names
                    else ""
                ),
            }
            for link in link_report.links
            if link.match_method == "fuzzy"
        ],
        "reconciliation": {
            **agreement.summary(),
            "recovered_from_datamap": [q.qid for q in recovered],
        },
        "variable_roles": [
            {"role": role, "label": ROLE_LABELS[role], "n": n}
            for role, n in sorted(roles.counts().items(), key=lambda kv: -kv[1])
        ],
        "hidden_variables": roles.of("hidden"),
        "run_by": user.name,
        "run_at": datetime.now().isoformat(),
    }
    set_study_artifacts(db, study_id, crosscheck_json=json.dumps(payload))
    record_audit(
        db,
        entity="study",
        entity_id=study_id,
        action="crosschecked",
        actor_id=user.id,
        summary=(
            f"{len(report.findings)} discrepancies over {report.n_rows} rows; "
            f"{len(link_report.links)} questions linked; "
            f"{len(recovered)} recovered from the data map; "
            f"{len(condition_audit.issues)} condition issues"
        ),
    )
    logger.info(
        f"[{study_id}] cross-check: {len(report.findings)} findings, "
        f"{len(link_report.links)} linked, {len(link_report.unmatched_questions)} unmatched"
    )
    return payload

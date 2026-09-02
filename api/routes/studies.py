"""Study endpoints."""

import json
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
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
    import base64
    import gzip

    from api.db import set_study_artifacts

    try:
        compressed = gzip.compress(file_path.read_bytes(), compresslevel=6)
        if len(compressed) > MAX_ARCHIVED_SOURCE_BYTES:
            logger.info(
                f"[{study_id}] source not archived: {len(compressed)} bytes compressed "
                f"exceeds cap ({MAX_ARCHIVED_SOURCE_BYTES})"
            )
            return
        set_study_artifacts(
            job_db,
            study_id,
            source_filename=file_path.name,
            source_file_b64=base64.b64encode(compressed).decode("ascii"),
        )
        logger.info(f"[{study_id}] source archived to DB ({len(compressed)} bytes gz)")
    except Exception as exc:
        logger.warning(f"[{study_id}] source archive failed (non-fatal): {exc}")


def _restore_source_file(study, upload_dir: Path) -> Path | None:
    """Rebuild the original upload from the DB archive; None if not archived."""
    import base64
    import gzip

    if not study.source_file_b64 or not study.source_filename:
        return None
    upload_dir.mkdir(parents=True, exist_ok=True)
    path = upload_dir / study.source_filename
    path.write_bytes(gzip.decompress(base64.b64decode(study.source_file_b64)))
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
async def create_empty_study(
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
    )


@router.post("/studies/{study_id}/reprocess")
async def reprocess_study(
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
async def list_all_studies(
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
        )
        study_responses.append(study_response)

    return StudyListResponse(
        studies=study_responses,
        total=total,
        page=skip // limit + 1,
        limit=limit,
    )


@router.get("/studies/{study_id}", response_model=StudyResponse)
async def get_study_detail(
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
    )


@router.post("/studies/{study_id}/approve/{gate}")
async def approve_study_gate(
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
async def get_crosstabs(
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
async def get_tracking(
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
    return {
        v.name: {
            "question_var": v.name,
            "label": v.label,
            "note": (
                "Not coded — this run predates coding persistence or the coding "
                "backends were unavailable. Re-run the study to code it."
            ),
            "codeframe": {"allow_multi": True, "version": 0, "codes": []},
            "summary": {"n_auto_coded": 0, "n_needs_review": 0, "confidence_mean": 0.0},
            "review_queue": [],
        }
        for v in datamap.variables
        if v.type == VarType.TEXT
    }


@router.get("/studies/{study_id}/coding")
async def get_coding(
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


@router.patch("/studies/{study_id}/coding/{variable}/{respondent_id}")
async def resolve_coding_item(
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
async def get_chart(
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
async def get_study_profile(
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
async def download_deck(
    study_id: str,
    fmt: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """Download the rendered Excel or PowerPoint deck. (Authenticated users only)"""
    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")

    if fmt == "excel":
        path = study.excel_path
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif fmt == "pptx":
        path = study.pptx_path
        media = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    else:
        raise HTTPException(status_code=400, detail="Format must be 'excel' or 'pptx'")

    if not path or not Path(path).exists():
        raise HTTPException(status_code=404, detail=f"No {fmt} deck rendered for this study")

    return FileResponse(path, media_type=media, filename=Path(path).name)


@router.delete("/studies/{study_id}")
async def delete_study(
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
async def create_study_share_link(
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
async def revoke_study_share_link(
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
async def get_crosscheck(
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
async def crosscheck_study(
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
    from api.db import list_questionnaires_for_study, record_audit, set_study_artifacts
    from src.core.models import DataMap
    from src.core.survey_definition import SurveyDefinition
    from src.engines.qc.condition_audit import audit_conditions
    from src.engines.qc.discrepancy import check_against_questionnaire
    from src.engines.qc.variable_roles import ROLE_LABELS, summarize, unexplained
    from src.engines.questionnaire.datamap_recovery import reconcile, recover_missing
    from src.engines.questionnaire.linker import link_questions_to_datamap

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
    recovered, recovered_links = recover_missing(survey, datamap)
    for question in recovered:
        survey.questions.append(question)
        if survey.blocks:
            survey.blocks[0].question_ids.append(question.qid)

    # Link the EXTRACTED questions by matching, then add the recovered ones
    # whose links are already known exactly.
    link_report = link_questions_to_datamap(survey, datamap)
    already = {link.qid for link in link_report.links}
    link_report.links.extend(link for link in recovered_links if link.qid not in already)

    # Audit the conditions against what was actually scripted before reading a
    # single respondent row. A screen-out testing codes that were never punched
    # fires for nobody, and the data check would report zero violations — which
    # reads exactly like a clean result.
    condition_audit = audit_conditions(survey, link_report.links, datamap)

    parquet_path = STUDIES_DIR / study_id / "data.parquet"
    if not parquet_path.exists():
        # The run directory is ephemeral on the free tier; the durable copies
        # are enough to link, but the row-level checks need the actual rows.
        raise HTTPException(
            status_code=409,
            detail="The data file is no longer on disk (the server restarted). "
            "Use Re-process to rebuild it, then run the cross-check.",
        )

    # The datamap is passed deliberately: it is the authority on what each
    # column actually holds. Without it, a question the extractor mis-read as
    # coded is compared against raw values and every respondent becomes a
    # finding — measured at 715 findings on a clean 750-respondent file, versus
    # 7 real ones with the datamap supplied.
    report = check_against_questionnaire(
        survey, link_report.links, parquet_path, datamap=datamap
    )
    # "412 variables no question claimed" is not a finding — most of those are
    # hidden variables, MaxDiff design columns and system fields that are
    # SUPPOSED to be absent from a questionnaire. Report what each column is
    # for, and list only the ones genuinely worth chasing.
    matched = {name for link in link_report.links for name in link.variable_names}
    by_qid = {q.qid: q for q in survey.questions}
    variable_labels = {v.name: (v.label or "") for v in datamap.variables}
    roles = summarize(datamap)
    payload = {
        **report.to_dict(),
        "linked_questions": len(link_report.links),
        "unmatched_questions": link_report.unmatched_questions,
        "unmatched_variables": unexplained(datamap, matched),
        "conditions": condition_audit.to_dict(),
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

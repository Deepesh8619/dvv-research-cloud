"""Study endpoints."""

import logging
import sys
import threading
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

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

        def run_orchestration():
            from api.db import SessionLocal

            job_db = SessionLocal()
            try:
                orchestrator = StudyOrchestrator(work_dir=STUDIES_DIR)
                result = orchestrator.run_study(
                    client_name=client_name,
                    source_file=file_path,
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

                if result.excel_deck_path:
                    # Durable DataMap copy: the run dir's datamap.json is on
                    # ephemeral disk (wiped every deploy) — the DB column keeps
                    # the study's variable metadata alive across restarts.
                    datamap_json = None
                    if result.datamap_path and Path(result.datamap_path).exists():
                        datamap_json = Path(result.datamap_path).read_text()
                    update_study_paths(
                        job_db,
                        study_id,
                        excel_path=str(result.excel_deck_path),
                        pptx_path=str(result.pptx_deck_path) if result.pptx_deck_path else None,
                        datamap_json=datamap_json,
                    )
                    logger.info(f"[{study_id}] Updated paths: Excel + PPT + datamap")

                for gate, (approver, _timestamp) in result.approval_chain.items():
                    approve_gate(job_db, study_id, gate, approver)
                    logger.info(f"[{study_id}] Approved {gate} by {approver}")

            except Exception as e:
                logger.error(f"[{study_id}] Orchestration failed: {e}", exc_info=True)
                update_study_status(job_db, study_id, "error", {"error": str(e)})
            finally:
                job_db.close()

        # Run async
        thread = threading.Thread(target=run_orchestration, daemon=True)
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
        raise HTTPException(status_code=500, detail=str(e)) from e


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
    """Return the computed tables (JSON) for the web viewer. (Authenticated users only)"""
    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")
    if not study.excel_path:
        raise HTTPException(status_code=404, detail="Tables not yet computed")

    crosstabs_file = Path(study.excel_path).parent / "crosstabs.json"
    if not crosstabs_file.exists():
        raise HTTPException(status_code=404, detail="No table data for this study")

    import json

    return json.loads(crosstabs_file.read_text())


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


@router.get("/studies/{study_id}/coding")
async def get_coding(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """Codeframes + review queues for every coded open-end. (Authenticated users)

    Serves the coding.json the orchestrator persisted at run time — no LLM call
    happens here; this is a pure read of already-computed results.
    """
    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")
    import json

    return json.loads(_coding_file(study).read_text())


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

    path = _coding_file(study)
    coding = json.loads(path.read_text())
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
            path.write_text(json.dumps(coding, indent=1))
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

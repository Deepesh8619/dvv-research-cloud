"""Job status and the per-study live event stream."""

import asyncio
import json
import time

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from api.auth import UserRole, get_current_user, require_role
from api.db import JobDB, UserDB, WorkflowRunDB, get_db, get_study
from src.platform import ledger, workflows
from src.platform.events import since
from src.workflows import field_close, qa_certificate  # noqa: F401 - registers them

router = APIRouter()

SSE_POLL_S = 1.0
SSE_KEEPALIVE_S = 15.0
SSE_LIFETIME_S = 300.0  # then the client reconnects with `after`

_STUDY_ROLES = (UserRole.OWNER, UserRole.ANALYST, UserRole.FOUNDER)


def _study_access(user: UserDB) -> None:
    """Same rule as require_role(OWNER, ANALYST) on the study routes (founders count
    as analysts); reviewers get no job or event feed."""
    if UserRole(user.role) not in _STUDY_ROLES:
        raise HTTPException(status_code=403, detail="You do not have access to this study")


@router.get("/jobs/{job_id}")
def get_job(
    job_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    job = db.get(JobDB, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.study_id:
        _study_access(user)
    return {
        "id": job.id,
        "kind": job.kind,
        "state": job.state,
        "attempts": job.attempts,
        "result": json.loads(job.result) if job.result else None,
        "error": job.error,
    }


@router.get("/studies/{study_id}/events")
def study_events(
    study_id: str,
    after: int = 0,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """Server-sent events for one study, after seq `after`. Read it with fetch
    streaming (it needs the Authorization header); on close, reconnect with the last id."""
    _study_access(user)
    if not get_study(db, study_id):
        raise HTTPException(status_code=404, detail="Study not found")
    factory = sessionmaker(bind=db.get_bind())

    def read(last: int) -> list[dict]:
        with factory() as s:
            return since(s, study_id, last)

    # Async, so an open stream waits on the event loop: a sync generator would hold
    # one of the server's worker threads for the stream's whole lifetime.
    async def stream():
        last, start = after, time.monotonic()
        last_sent = start
        while time.monotonic() - start < SSE_LIFETIME_S:
            events = await run_in_threadpool(read, last)
            for ev in events:
                last = ev["seq"]
                yield f"id: {ev['seq']}\nevent: {ev['kind']}\ndata: {json.dumps(ev['data'])}\n\n"
            if events:
                last_sent = time.monotonic()
            elif time.monotonic() - last_sent >= SSE_KEEPALIVE_S:
                yield ": keepalive\n\n"
                last_sent = time.monotonic()
            await asyncio.sleep(SSE_POLL_S)

    return StreamingResponse(stream(), media_type="text/event-stream")


def _start(db: Session, name: str, study_id: str, user: UserDB) -> dict:
    if not get_study(db, study_id):
        raise HTTPException(status_code=404, detail="Study not found")
    run = workflows.start(db, name, study_id, {"study_id": study_id}, created_by=user.id)
    return {"run_id": run.id}


@router.post("/studies/{study_id}/certificate", status_code=202)
def issue_certificate(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Start the logic-certificate workflow. Poll /workflow-runs/{run_id}."""
    return _start(db, "qa_certificate", study_id, user)


@router.post("/studies/{study_id}/workflows/field-close", status_code=202)
def close_fieldwork(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Build the data file, tables, report draft and decks from the fielded data."""
    return _start(db, "field_close", study_id, user)


@router.get("/workflow-runs/{run_id}")
def get_workflow_run(
    run_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    run = db.get(WorkflowRunDB, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.study_id:
        _study_access(user)
    jobs = {j.step: j for j in db.query(JobDB).filter(JobDB.workflow_run_id == run_id)}
    steps = []
    for s in workflows.WORKFLOWS[run.workflow].steps:
        j = jobs.get(s.name)
        steps.append({"name": s.name, "state": j.state if j else "waiting",
                      "error": j.error if j and j.state == "failed" else None,
                      "result": json.loads(j.result) if j and j.result else None})
    return {"id": run.id, "workflow": run.workflow, "state": run.state,
            "error": run.error, "steps": steps}


@router.get("/studies/{study_id}/certificate.pdf")
def download_certificate(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    path = qa_certificate.certificate_path(study_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="No certificate has been issued yet")
    return FileResponse(path, media_type="application/pdf", filename="logic_certificate.pdf")


@router.get("/studies/{study_id}/ledger/verify")
def verify_ledger(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """Recompute the study's audit chain. `first_bad_seq` is where history was changed."""
    _study_access(user)
    if not get_study(db, study_id):
        raise HTTPException(status_code=404, detail="Study not found")
    return ledger.verify(db, study_id)


@router.post("/studies/{study_id}/ledger/check-file")
def check_file(
    study_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """Was this exact file issued by this study? Compares its sha256 to the ledger."""
    import hashlib

    _study_access(user)
    if not get_study(db, study_id):
        raise HTTPException(status_code=404, detail="Study not found")
    h = hashlib.sha256()
    for block in iter(lambda: file.file.read(1 << 20), b""):
        h.update(block)
    found = ledger.was_issued(db, study_id, h.hexdigest())
    return {"issued": found is not None, **(found or {"at": None, "name": None})}


@router.get("/admin/ai-usage")
def ai_usage(
    days: int = 30,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.FOUNDER)),
):
    """AI model usage and its estimated cost, per day and for the month. (Owner/Founder)"""
    from src.platform.usage import summary

    return summary(db, days=max(1, min(days, 366)))

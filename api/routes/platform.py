"""Job status and the per-study live event stream."""

import asyncio
import json
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from api.auth import UserRole, get_current_user
from api.db import JobDB, UserDB, get_db, get_study
from src.platform.events import since

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

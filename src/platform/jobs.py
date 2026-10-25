"""Database-backed job queue. A restart loses nothing; failures retry with backoff."""

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import update
from sqlalchemy.orm import Session

from api.db import JobDB

logger = logging.getLogger(__name__)

GENERIC_ERROR = "Something went wrong running this job. It has been logged."


class JobError(Exception):
    """A failure whose message is written for the person reading it."""


HANDLERS: dict[str, Callable[[dict, Session], dict]] = {}


def handler(kind: str):
    def deco(fn):
        HANDLERS[kind] = fn
        return fn

    return deco


def _now() -> datetime:
    return datetime.now(UTC)


def enqueue(
    db: Session,
    kind: str,
    payload: dict,
    *,
    study_id: str | None = None,
    max_attempts: int = 3,
    delay_s: float = 0,
    workflow_run_id: str | None = None,
    step: str | None = None,
    created_by: str | None = None,
) -> JobDB:
    now = _now()
    job = JobDB(
        id=f"job_{uuid4().hex[:12]}",
        kind=kind,
        study_id=study_id,
        payload=json.dumps(payload),
        state="queued",
        attempts=0,
        max_attempts=max_attempts,
        run_after=(now + timedelta(seconds=delay_s)).isoformat(),
        workflow_run_id=workflow_run_id,
        step=step,
        created_by=created_by,
        created_at=now.isoformat(),
    )
    db.add(job)
    db.commit()
    return job


def claim(db: Session, worker_id: str) -> JobDB | None:
    """Take the oldest due queued job, or None. Two workers never get the same one."""
    now = _now().isoformat()
    due = (JobDB.state == "queued") & (JobDB.run_after <= now)
    if db.get_bind().dialect.name == "postgresql":
        job = (
            db.query(JobDB)
            .filter(due)
            .order_by(JobDB.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
            .first()
        )
        if job is None:
            db.rollback()
            return None
        job.state, job.locked_by, job.heartbeat_at = "running", worker_id, now
        job.attempts = (job.attempts or 0) + 1
        db.commit()
        return job
    while True:
        cand = db.query(JobDB.id).filter(due).order_by(JobDB.created_at).first()
        if cand is None:
            return None
        won = db.execute(
            update(JobDB)
            .where(JobDB.id == cand.id, JobDB.state == "queued")
            .values(
                state="running",
                locked_by=worker_id,
                heartbeat_at=now,
                attempts=JobDB.attempts + 1,
            )
        ).rowcount
        db.commit()
        if won == 1:
            job = db.get(JobDB, cand.id)
            db.refresh(job)
            return job
        # lost the race to another worker; try the next candidate


def heartbeat(db: Session, job_id: str) -> None:
    db.execute(update(JobDB).where(JobDB.id == job_id).values(heartbeat_at=_now().isoformat()))
    db.commit()


def complete(db: Session, job_id: str, result: dict) -> None:
    job = db.get(JobDB, job_id)
    job.state, job.result, job.error = "done", json.dumps(result), None
    job.finished_at = _now().isoformat()
    db.commit()


def fail(db: Session, job_id: str, error: str) -> None:
    job = db.get(JobDB, job_id)
    job.error = error
    if (job.attempts or 0) < job.max_attempts:
        job.state, job.locked_by = "queued", None
        job.run_after = (_now() + timedelta(seconds=30 * 2**job.attempts)).isoformat()
    else:
        job.state, job.finished_at = "failed", _now().isoformat()
    db.commit()


def reap(db: Session, stale_s: float = 300) -> int:
    """Running jobs whose worker stopped heartbeating count as a failed attempt:
    retried while attempts remain, failed after, so a job that kills its worker
    cannot loop forever and its workflow still finishes."""
    cutoff = (_now() - timedelta(seconds=stale_s)).isoformat()
    stale = db.query(JobDB).filter(JobDB.state == "running", JobDB.heartbeat_at < cutoff).all()
    for job in stale:
        fail(db, job.id, "The worker stopped while running this job")
        if job.workflow_run_id:
            from src.platform.workflows import advance

            advance(db, job.workflow_run_id)
    return len(stale)


def run_one(db: Session, job: JobDB) -> None:
    fn = HANDLERS.get(job.kind)
    if fn is None:
        fail(db, job.id, f"No handler for {job.kind}")
    else:
        try:
            result = fn({"job_id": job.id, **json.loads(job.payload or "{}")}, db)
        except Exception as e:  # noqa: BLE001 - a handler failure is recorded, never raised
            db.rollback()
            if isinstance(e, JobError):
                fail(db, job.id, str(e)[:500])
            else:
                logger.exception("job %s (%s) failed", job.id, job.kind)
                fail(db, job.id, GENERIC_ERROR)
        else:
            complete(db, job.id, result)
    if job.workflow_run_id:
        from src.platform.workflows import advance

        advance(db, job.workflow_run_id)

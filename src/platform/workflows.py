"""Multi-step workflows: each step is a job; a step runs once its `after` steps are done."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from api.db import JobDB, WorkflowRunDB
from src.platform.jobs import enqueue


@dataclass(frozen=True)
class Step:
    name: str
    kind: str
    after: tuple[str, ...] = ()


@dataclass(frozen=True)
class Workflow:
    name: str
    steps: tuple[Step, ...]


WORKFLOWS: dict[str, Workflow] = {}


def register_workflow(wf: Workflow) -> None:
    names = [s.name for s in wf.steps]
    if len(set(names)) != len(names):
        raise ValueError(f"Workflow {wf.name} has duplicate step names")
    for s in wf.steps:
        for d in s.after:
            if d not in names:
                raise ValueError(f"Step {s.name} comes after unknown step {d}")
    # Kahn: if some steps can never be released, there is a cycle.
    done: set[str] = set()
    while len(done) < len(names):
        ready = [s.name for s in wf.steps if s.name not in done and set(s.after) <= done]
        if not ready:
            raise ValueError(f"Workflow {wf.name} has a cycle")
        done.update(ready)
    WORKFLOWS[wf.name] = wf


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _enqueue_step(db: Session, run: WorkflowRunDB, step: Step, results: dict, created_by=None):
    payload = {"inputs": json.loads(run.inputs), "results": results}
    enqueue(
        db,
        step.kind,
        payload,
        study_id=run.study_id,
        workflow_run_id=run.id,
        step=step.name,
        created_by=created_by,
    )


def start(
    db: Session, workflow_name: str, study_id: str | None, inputs: dict, created_by=None
) -> WorkflowRunDB:
    wf = WORKFLOWS[workflow_name]
    run = WorkflowRunDB(
        id=f"wfr_{uuid4().hex[:12]}",
        workflow=wf.name,
        study_id=study_id,
        state="running",
        inputs=json.dumps(inputs),
        started_at=_now(),
    )
    db.add(run)
    db.commit()
    for s in wf.steps:
        if not s.after:
            _enqueue_step(db, run, s, {}, created_by)
    return run


def advance(db: Session, run_id: str) -> None:
    # Locked: two parallel steps finishing on two workers must not both queue the next.
    run = db.query(WorkflowRunDB).filter(WorkflowRunDB.id == run_id).with_for_update().first()
    if run is None or run.state != "running":
        return
    wf = WORKFLOWS[run.workflow]
    jobs = {j.step: j for j in db.query(JobDB).filter(JobDB.workflow_run_id == run_id)}
    for s in wf.steps:
        j = jobs.get(s.name)
        if j is not None and j.state == "failed":
            run.state, run.error, run.finished_at = "failed", j.error, _now()
            db.commit()
            return
    done = {n: j for n, j in jobs.items() if j.state == "done"}
    for s in wf.steps:
        if s.name not in jobs and all(d in done for d in s.after):
            results = {d: json.loads(done[d].result or "{}") for d in s.after}
            _enqueue_step(db, run, s, results)
    if all(s.name in done for s in wf.steps):
        run.state, run.finished_at = "done", _now()
    db.commit()


def cancel(db: Session, run_id: str) -> None:
    now = _now()
    for j in db.query(JobDB).filter(JobDB.workflow_run_id == run_id, JobDB.state == "queued"):
        j.state, j.finished_at = "cancelled", now
    run = db.get(WorkflowRunDB, run_id)
    run.state, run.finished_at = "cancelled", now
    db.commit()

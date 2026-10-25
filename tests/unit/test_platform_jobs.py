import json
import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.db import Base, JobDB
from src.platform import jobs, worker


@pytest.fixture
def factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path}/t.db", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture
def db(factory):
    with factory() as s:
        yield s


def test_enqueue_claim_complete(db):
    job = jobs.enqueue(db, "t", {"a": 1}, study_id="s1")
    assert job.id.startswith("job_") and job.state == "queued"
    got = jobs.claim(db, "w1")
    assert got.id == job.id and got.state == "running" and got.attempts == 1
    assert got.locked_by == "w1"
    jobs.complete(db, job.id, {"ok": True})
    db.refresh(got)
    assert got.state == "done" and json.loads(got.result) == {"ok": True}
    assert got.finished_at


def test_claim_never_returns_same_job_twice(factory):
    with factory() as a, factory() as b:
        jobs.enqueue(a, "t", {})
        first = jobs.claim(a, "w1")
        assert first is not None
        assert jobs.claim(b, "w2") is None
        jobs.enqueue(a, "t", {})
        second = jobs.claim(b, "w2")
        assert second.id != first.id


def test_claim_skips_future_and_takes_oldest(db):
    jobs.enqueue(db, "t", {}, delay_s=3600)
    old = jobs.enqueue(db, "t", {})
    jobs.enqueue(db, "t", {})
    assert jobs.claim(db, "w").id == old.id


def test_retry_backoff_then_final_failure(db):
    job = jobs.enqueue(db, "t", {}, max_attempts=2)
    jobs.claim(db, "w")
    jobs.fail(db, job.id, "boom")
    db.refresh(job)
    assert job.state == "queued" and job.error == "boom"
    wait = datetime.fromisoformat(job.run_after) - datetime.now(UTC)
    assert timedelta(seconds=50) < wait <= timedelta(seconds=60)  # 30 * 2**1
    assert jobs.claim(db, "w") is None  # still backing off
    job.run_after = datetime.now(UTC).isoformat()
    db.commit()
    jobs.claim(db, "w")
    jobs.fail(db, job.id, "boom2")
    db.refresh(job)
    assert job.state == "failed" and job.finished_at


def test_reap_returns_dead_job(db):
    job = jobs.enqueue(db, "t", {})
    jobs.claim(db, "w")
    assert jobs.reap(db) == 0
    job.heartbeat_at = (datetime.now(UTC) - timedelta(seconds=600)).isoformat()
    db.commit()
    assert jobs.reap(db) == 1
    db.refresh(job)
    assert job.state == "queued" and job.locked_by is None


def test_a_job_that_keeps_killing_its_worker_stops_retrying(db):
    job = jobs.enqueue(db, "t", {}, max_attempts=1)
    jobs.claim(db, "w")
    job.heartbeat_at = (datetime.now(UTC) - timedelta(seconds=600)).isoformat()
    db.commit()
    assert jobs.reap(db) == 1
    db.refresh(job)
    assert job.state == "failed" and "worker stopped" in job.error


def test_unknown_kind_fails_cleanly(factory):
    with factory() as db:
        job = jobs.enqueue(db, "nope", {}, max_attempts=1)
        assert worker.run_once(factory, "w") is True
        db.refresh(job)
        assert job.state == "failed" and job.error == "No handler for nope"
        assert worker.run_once(factory, "w") is False


def test_handler_exception_stored_truncated(factory):
    @jobs.handler("explode")
    def _(payload, db):
        raise ValueError("x" * 800)

    try:
        with factory() as db:
            job = jobs.enqueue(db, "explode", {}, max_attempts=1)
            worker.run_once(factory, "w")
            db.refresh(job)
            assert job.state == "failed"
            assert job.error.startswith("ValueError: xxx") and len(job.error) == 500
    finally:
        jobs.HANDLERS.pop("explode")


def test_handler_result_stored(factory):
    @jobs.handler("echo")
    def _(payload, db):
        return {"got": payload["v"]}

    try:
        with factory() as db:
            job = jobs.enqueue(db, "echo", {"v": 7})
            worker.run_once(factory, "w")
            db.refresh(job)
            assert job.state == "done" and json.loads(job.result) == {"got": 7}
    finally:
        jobs.HANDLERS.pop("echo")


@pytest.mark.skipif(not os.environ.get("DVV_TEST_PG"), reason="needs DVV_TEST_PG")
def test_claim_skip_locked_postgres():
    engine = create_engine(os.environ["DVV_TEST_PG"])
    Base.metadata.create_all(bind=engine)
    S = sessionmaker(bind=engine)
    with S() as a, S() as b:
        j = jobs.enqueue(a, "t", {})
        try:
            got = jobs.claim(a, "w1")
            assert got.id == j.id
            assert jobs.claim(b, "w2") is None
        finally:
            a.query(JobDB).delete()
            a.commit()

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.db import Base, JobDB
from src.platform import jobs, worker, workflows
from src.platform.workflows import Step, Workflow

DIAMOND = Workflow(
    "diamond",
    (
        Step("A", "wf_a"),
        Step("B", "wf_b", ("A",)),
        Step("C", "wf_c", ("A",)),
        Step("D", "wf_d", ("B", "C")),
    ),
)


@pytest.fixture
def factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path}/t.db", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(autouse=True)
def handlers():
    for k in "abcd":
        jobs.HANDLERS[f"wf_{k}"] = lambda p, db, k=k: {"from": k}
    workflows.register_workflow(DIAMOND)
    yield
    for k in "abcd":
        jobs.HANDLERS.pop(f"wf_{k}", None)
    workflows.WORKFLOWS.pop("diamond", None)


def drain(factory):
    n = 0
    while worker.run_once(factory, "w"):
        n += 1
    return n


def test_diamond_runs_to_done(factory):
    with factory() as db:
        run = workflows.start(db, "diamond", "s1", {"x": 1})
        assert db.query(JobDB).count() == 1  # only A
        assert drain(factory) == 4
        db.refresh(run)
        assert run.state == "done" and run.finished_at
        d = db.query(JobDB).filter_by(step="D").one()
        payload = json.loads(d.payload)
        assert payload["inputs"] == {"x": 1}
        assert payload["results"] == {"B": {"from": "b"}, "C": {"from": "c"}}


def test_failing_step_fails_run_and_leaves_d_unqueued(factory):
    jobs.HANDLERS["wf_c"] = lambda p, db: 1 / 0
    with factory() as db:
        run = workflows.start(db, "diamond", None, {})
        # max_attempts is 3 with backoff; exhaust C directly
        drain(factory)
        c = db.query(JobDB).filter_by(step="C").one()
        c.max_attempts = 1
        c.run_after = "2000-01-01T00:00:00+00:00"
        db.commit()
        drain(factory)
        db.refresh(run)
        assert run.state == "failed" and run.error == jobs.GENERIC_ERROR
        assert db.query(JobDB).filter_by(step="D").count() == 0


def test_cancel(factory):
    with factory() as db:
        run = workflows.start(db, "diamond", None, {})
        workflows.cancel(db, run.id)
        db.refresh(run)
        assert run.state == "cancelled"
        assert [j.state for j in db.query(JobDB)] == ["cancelled"]
        assert worker.run_once(factory, "w") is False


def test_validation():
    with pytest.raises(ValueError, match="cycle"):
        workflows.register_workflow(
            Workflow("cyc", (Step("a", "k", ("b",)), Step("b", "k", ("a",))))
        )
    with pytest.raises(ValueError, match="unknown"):
        workflows.register_workflow(Workflow("u", (Step("a", "k", ("zz",)),)))
    with pytest.raises(ValueError, match="duplicate"):
        workflows.register_workflow(Workflow("d", (Step("a", "k"), Step("a", "k"))))
    assert "cyc" not in workflows.WORKFLOWS

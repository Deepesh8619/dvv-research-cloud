from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.auth import get_current_user
from api.db import Base, StudyDB, UserDB, get_db
from api.main import app
from api.routes import platform as platform_routes
from src.platform import events, jobs


@pytest.fixture
def env():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    factory = sessionmaker(bind=eng)
    state = {"role": "analyst"}
    app.dependency_overrides[get_db] = lambda: factory()
    app.dependency_overrides[get_current_user] = lambda: UserDB(
        id="u1", email="t@x.com", name="T", role=state["role"], is_active=True)
    now = datetime.now().isoformat()
    with factory() as db:
        db.add(StudyDB(id="s1", client_name="C", status="qc", created_at=now,
                       updated_at=now, study_metadata={}, approval_chain={}))
        db.commit()
    yield TestClient(app), factory, state
    app.dependency_overrides.clear()


def test_since_returns_later_events_in_order(env):
    _, factory, _ = env
    with factory() as db:
        a = events.emit(db, "s1", "a", {"n": 1})
        b = events.emit(db, "s1", "b")
        events.emit(db, "other", "c")
        assert [e["kind"] for e in events.since(db, "s1", 0)] == ["a", "b"]
        assert [e["kind"] for e in events.since(db, "s1", a)] == ["b"]
        assert events.since(db, "s1", b) == []
        assert events.since(db, "s1", 0, limit=1)[0]["data"] == {"n": 1}


def test_sse_streams_an_emitted_event(env, monkeypatch):
    c, factory, _ = env
    monkeypatch.setattr(platform_routes, "SSE_LIFETIME_S", 0.3)
    monkeypatch.setattr(platform_routes, "SSE_POLL_S", 0.05)
    with factory() as db:
        seq = events.emit(db, "s1", "job.done", {"kind": "x"})
    r = c.get("/api/studies/s1/events")
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.text.startswith(f'id: {seq}\nevent: job.done\ndata: {{"kind": "x"}}\n\n')
    assert c.get(f"/api/studies/s1/events?after={seq}").text == ""
    assert c.get("/api/studies/nope/events").status_code == 404


def test_job_route_404_403_and_fields(env):
    c, factory, state = env
    assert c.get("/api/jobs/job_nope").status_code == 404
    with factory() as db:
        job_id = jobs.enqueue(db, "t", {}, study_id="s1").id
        free_id = jobs.enqueue(db, "t", {}).id
    got = c.get(f"/api/jobs/{job_id}").json()
    assert got["state"] == "queued" and got["kind"] == "t" and got["error"] is None
    state["role"] = "reviewer"
    assert c.get(f"/api/jobs/{job_id}").status_code == 403
    assert c.get(f"/api/jobs/{free_id}").status_code == 200
    assert c.get("/api/studies/s1/events").status_code == 403

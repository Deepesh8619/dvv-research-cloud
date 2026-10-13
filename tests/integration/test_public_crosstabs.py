"""Public share-link crosstabs: gated like the downloads, thin payload."""

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.db import Base, create_share_link, create_study, get_db, revoke_share_link
from api.main import app

PAYLOAD = {
    "title": "T", "weight": None, "confidence": 0.95, "min_base_for_test": 30,
    "tables": [{"id": "Q1"}], "qc_flags": ["x"], "approval_history": ["y"],
}


@pytest.fixture
def env():
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    app.dependency_overrides[get_db] = lambda: db
    study = create_study(db, "s1", "Acme")
    study.crosstabs_json = json.dumps(PAYLOAD)
    db.commit()
    link = create_share_link(db, "s1", "u1")
    yield TestClient(app), db, study, link
    app.dependency_overrides.clear()


def test_gate(env):
    c, db, study, link = env
    url = f"/api/public/studies/{link.token}/crosstabs"
    assert c.get(url).status_code == 403
    study.status = "approved_for_delivery"
    db.commit()
    r = c.get(url)
    assert r.status_code == 200
    assert r.json()["tables"] == [{"id": "Q1"}]
    assert "qc_flags" not in r.json() and "approval_history" not in r.json()
    revoke_share_link(db, link.token)
    assert c.get(url).status_code == 404

"""Cited report: draft job, GET, edit with re-check, add, delete."""

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.auth import get_current_user
from api.db import Base, UserDB, create_study, get_db
from api.main import app
from src.ai import router as ai_router
from src.platform.worker import drain


def _t(tid, stub, title, rows):
    return {"id": tid, "stub": stub, "title": title, "means": None,
            "columns": [{"label": "Total", "letter": None, "base": 80.0, "unweighted_base": 80}],
            "rows": [{"label": lbl, "pct": [p], "sig": [""], "kind": "category"}
                     for lbl, p in rows]}


@pytest.fixture
def client(tmp_path):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: UserDB(
        id="u1", email="t@x.com", name="T", role="analyst", is_active=True)
    study = create_study(db, "s1", "Acme")
    study.excel_path = str(tmp_path / "gone" / "deck.xlsx")
    study.crosstabs_json = json.dumps({"tables": [
        _t("t1", "Q1", "Heard of us", [("Yes", 70.0), ("No", 30.0)])]})
    db.commit()
    yield TestClient(app), sessionmaker(bind=eng)
    app.dependency_overrides.clear()


def _down(monkeypatch):
    def down(prompt, schema, timeout=120):
        raise ai_router.BackendUnavailable("none", config_error=True)
    monkeypatch.setattr(ai_router, "run_private", down)


def test_with_no_model_the_report_is_the_finding_texts(client, monkeypatch):
    c, sf = client
    _down(monkeypatch)
    assert c.post("/api/studies/s1/report/draft").status_code == 202
    drain(sf)
    body = c.get("/api/studies/s1/report").json()
    assert body["job"]["state"] == "done"
    (s,) = body["sentences"]
    assert s["text"] == "Heard of us: Yes leads at 70%, ahead of No at 30%."
    assert s["source"] == "finding" and s["ok"], s
    assert s["cites"] == [{"table": "t1", "row": 0, "col": 0}, {"table": "t1", "row": 1, "col": 0}]


def test_a_good_rewrite_is_kept_and_a_bad_one_replaced(client, monkeypatch):
    c, sf = client
    reply = {"sentences": [{"id": "s1", "text": "Seven in ten (70%) have heard of us, ahead of "
                                                "the 30% who have not."}]}
    monkeypatch.setattr(ai_router, "run_private",
                        lambda p, s, timeout=120: SimpleNamespace(content=reply))
    c.post("/api/studies/s1/report/draft")
    drain(sf)
    (s,) = c.get("/api/studies/s1/report").json()["sentences"]
    assert s["source"] == "model" and s["ok"]

    reply["sentences"][0]["text"] = "Awareness is 85%, ahead of 30%."
    c.post("/api/studies/s1/report/draft")
    drain(sf)
    (s,) = c.get("/api/studies/s1/report").json()["sentences"]
    assert s["source"] == "finding" and s["text"].startswith("Heard of us: Yes leads")


def test_put_rechecks_and_keeps_a_failed_sentence_with_its_problems(client, monkeypatch):
    c, sf = client
    _down(monkeypatch)
    c.post("/api/studies/s1/report/draft")
    drain(sf)
    r = c.put("/api/studies/s1/report/sentences/s1", json={"text": "Yes is at 90%."})
    assert r.status_code == 200
    assert r.json()["source"] == "person" and not r.json()["ok"] and r.json()["problems"]
    (s,) = c.get("/api/studies/s1/report").json()["sentences"]
    assert not s["ok"] and s["cites"]  # stored with its problems, cites kept
    fixed = c.put("/api/studies/s1/report/sentences/s1", json={"text": "Yes is at 70%."}).json()
    assert fixed["ok"]
    assert c.put("/api/studies/s1/report/sentences/zz", json={"text": "x"}).status_code == 404


def test_add_and_delete_a_sentence(client, monkeypatch):
    c, sf = client
    added = c.post("/api/studies/s1/report/sentences", json={
        "text": "No is 30%.", "cites": [{"table": "t1", "row": 1, "col": 0}]}).json()
    assert added["ok"] and added["source"] == "person"
    assert len(c.get("/api/studies/s1/report").json()["sentences"]) == 1
    assert c.delete(f"/api/studies/s1/report/sentences/{added['id']}").status_code == 200
    assert c.get("/api/studies/s1/report").json()["sentences"] == []
    assert c.delete("/api/studies/s1/report/sentences/nope").status_code == 404
    assert c.get("/api/studies/nope/report").status_code == 404

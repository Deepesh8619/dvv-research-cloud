"""Norms from past studies: range of the Total column across matched past tables."""

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.auth import get_current_user
from api.db import Base, ProjectChunkDB, UserDB, create_study, get_db
from api.main import app

SAME, OTHER = [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]


def _table(tid, stub, pcts, labels=("Brand A", "Brand B")):
    return {"id": tid, "stub": stub, "title": "T",
            "columns": [{"label": "Men", "base": 1, "unweighted_base": 1},
                        {"label": "Total", "base": 1, "unweighted_base": 1}],
            "rows": [{"label": lab, "pct": [0.0, p], "sig": ["", ""], "kind": "category"}
                     for lab, p in zip(labels, pcts, strict=True)]}


@pytest.fixture
def env(tmp_path):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: UserDB(
        id="u1", email="t@x.com", name="T", role="analyst", is_active=True)

    def add(sid, vec, table, qid="Q1"):
        s = create_study(db, sid, f"Client {sid}")
        s.excel_path = str(tmp_path / sid / "deck.xlsx")
        tables = table if isinstance(table, list) else [table]
        s.crosstabs_json = json.dumps({"title": f"Study {sid}", "tables": tables})
        db.add(ProjectChunkDB(id=f"{sid}:question:{qid}", study_id=sid, kind="question",
                              ref=qid, text="x", model="m", vector_json=json.dumps(vec)))
        db.commit()

    yield TestClient(app), db, add
    app.dependency_overrides.clear()


def test_range_from_three_past_studies(env):
    client, _, add = env
    add("p1", SAME, _table("t", "Q1", [20, 70]))
    add("p2", [0.99, 0.1, 0.0], _table("t", "Q1r1", [30, 60]))   # near-identical question
    add("p3", SAME, _table("t", "Q1", [40, 50], labels=("brand a", "BRAND B")))
    add("p4", OTHER, _table("t", "Q1", [99, 1]))                 # different question
    add("new", SAME, _table("t", "Q1", [34, 66]))
    body = client.get("/api/studies/new/tables/t/norms").json()
    assert sorted(m["study_id"] for m in body["matched"]) == ["p1", "p2", "p3"]
    a = body["rows"][0]
    got = (a["label"], a["n_studies"], a["min"], a["median"], a["max"])
    assert got == ("Brand A", 3, 20, 30, 40)


def test_one_past_study_is_not_enough(env):
    client, _, add = env
    add("p1", SAME, _table("t", "Q1", [20, 70]))
    add("new", SAME, _table("t", "Q1", [34, 66]))
    body = client.get("/api/studies/new/tables/t/norms").json()
    assert body == {"rows": [], "matched": [], "label": "Not enough past studies yet"}


def test_different_row_labels_do_not_match(env):
    client, _, add = env
    add("p1", SAME, _table("t", "Q1", [20, 70], labels=("Cola", "Tea")))
    add("p2", SAME, _table("t", "Q1", [30, 60], labels=("Cola", "Tea")))
    add("new", SAME, _table("t", "Q1", [34, 66]))
    assert client.get("/api/studies/new/tables/t/norms").json()["rows"] == []


def test_an_option_table_is_compared_with_the_same_option_only(env):
    client, _, add = env

    def opt(tid, stub, brand, pct):
        t = _table(tid, stub, [pct, 100 - pct], labels=("Checked", "Not checked"))
        t["title"] = f"Which have you heard of? — {brand}"
        return t
    add("p1", SAME, [opt("a", "Q1r1", "Nescafé", 90), opt("b", "Q1r2", "Bru", 40)])
    add("p2", SAME, [opt("a", "Q1r1", "Nescafé", 80), opt("b", "Q1r2", "Bru", 50)])
    add("new", SAME, opt("t", "Q1r2", "Bru", 45))
    checked = client.get("/api/studies/new/tables/t/norms").json()["rows"][0]
    assert (checked["label"], checked["min"], checked["max"]) == ("Checked", 40, 50)

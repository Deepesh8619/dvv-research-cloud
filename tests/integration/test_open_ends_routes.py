"""Open ends: only real open questions, coded on demand in the background."""

import json
from datetime import datetime

import polars as pl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.auth import get_current_user
from api.db import Base, StudyDB, UserDB, get_db
from api.main import app
from src.core.models import DataMap, Variable, VarType


@pytest.fixture
def client(tmp_path):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: UserDB(
        id="u1", email="t@x.com", name="T", role="analyst", is_active=True)
    run = tmp_path / "run"
    run.mkdir()
    answers = ["Lower the cost of clicks", "Better targeting for local shops",
               "More support please", "Simpler reporting for small teams",
               "Cheaper video ads would help"]
    pl.DataFrame({"uuid": [f"u{i}" for i in range(5)], "date": ["2026-05-01 10:00"] * 5,
                  "WHY_OE": answers}).write_parquet(run / "data.parquet")
    (run / "datamap.json").write_text(DataMap([
        Variable(name="uuid", label="Participant identifier", type=VarType.TEXT),
        Variable(name="date", label="Completion time and date", type=VarType.TEXT),
        Variable(name="WHY_OE", label="What could improve?", type=VarType.TEXT,
                 question_ref="WHY_OE", question_text="What could improve?")]).to_json())
    now = datetime.now().isoformat()
    db.add(StudyDB(id="s1", client_name="C", status="qc", created_at=now, updated_at=now,
                   excel_path=str(run / "d.xlsx"), study_metadata={}, approval_chain={}))
    db.commit()
    yield TestClient(app), db, run
    app.dependency_overrides.clear()


def test_only_real_open_questions_are_listed_and_coded_on_demand(client, monkeypatch):
    c, db, run = client
    status = c.get("/api/studies/s1/coding/status").json()
    assert [o["variable"] for o in status["open_ends"]] == ["WHY_OE"]  # not uuid, not date
    assert list(c.get("/api/studies/s1/coding").json()) == ["WHY_OE"]

    from types import SimpleNamespace

    def fake_job(df, name, id_col, question, min_confidence=0.8):
        assert id_col in df.columns  # a Decipher export has no respondent_id; one is added
        return SimpleNamespace(df_coded=df.with_columns(
            codes=pl.lit(None), coding_confidence=pl.lit(0.9), coding_reasoning=pl.lit(""),
            needs_coding_review=pl.lit(False)))
    monkeypatch.setattr("src.engines.coding.runner.run_coding_job", fake_job)
    monkeypatch.setattr("src.engines.coding.runner.coding_run_to_artifact",
                        lambda run: {"question_var": "WHY_OE", "codeframe": {"codes": []},
                                     "review_queue": [], "summary": {}})
    assert c.post("/api/studies/s1/coding/run", json={"variables": ["uuid"]}).status_code == 422
    r = c.post("/api/studies/s1/coding/run", json={})
    assert r.status_code == 202 and r.json()["variables"] == ["WHY_OE"]
    after = c.get("/api/studies/s1/coding/status").json()
    assert after["job"]["state"] == "done" and after["job"]["coded"] == ["WHY_OE"]
    assert after["open_ends"][0]["coded"] is True
    assert "WHY_OE" in json.loads((run / "coding.json").read_text())

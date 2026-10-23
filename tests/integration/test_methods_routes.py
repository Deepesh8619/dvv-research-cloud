"""Methods routes: what can run, and a run on completes only."""

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
    pl.DataFrame({"Q5r1": [1, 1, 0, 0], "Q5r2": [0, 1, 1, 0], "Q5r3": [0, 0, 0, 1],
                  "SAT": [5, 4, 2, 1]}).write_parquet(run / "data.parquet")
    (run / "datamap.json").write_text(DataMap(
        [Variable(name=f"Q5r{i}", label=f"Where? — {s}", type=VarType.SINGLE,
                  values={0: "Unchecked", 1: "Checked"}, question_ref="Q5", question_text="Where?")
         for i, s in ((1, "Web"), (2, "Store"), (3, "Phone"))]
        + [Variable(name="SAT", label="Satisfaction", type=VarType.SINGLE,
                    values={i: str(i) for i in range(1, 6)}, question_ref="SAT")]).to_json())
    now = datetime.now().isoformat()
    db.add(StudyDB(id="s1", client_name="C", status="qc", created_at=now, updated_at=now,
                   excel_path=str(run / "d.xlsx"), study_metadata={}, approval_chain={}))
    db.commit()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_methods_suggest_inputs_and_run_turf(client):
    inputs = client.get("/api/studies/s1/methods").json()
    assert inputs["multi_select"] == [{"question": "Q5", "text": "Where?"}]
    assert [r["variable"] for r in inputs["ratings"]] == ["SAT"]
    r = client.post("/api/studies/s1/methods", json={"method": "turf", "question": "Q5",
                                                     "max_size": 2})
    assert r.status_code == 200, r.text
    assert r.json()["sets"][1]["reach_pct"] == 75.0  # every pair misses one person
    assert r.json()["sets"][0]["options"] == ["Web"]
    bad = client.post("/api/studies/s1/methods", json={"method": "drivers", "outcome": "SAT",
                                                       "predictors": ["Q5r1", "Q5r2"]})
    assert bad.status_code == 422 and "needed" in bad.json()["detail"]
    assert client.post("/api/studies/s1/methods", json={
        "method": "van_westendorp", "prices": ["NOPE", "A", "B", "C"]}).status_code == 422

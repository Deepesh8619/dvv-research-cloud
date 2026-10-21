"""Stack routes: the drift report, and one .sav refused while codes disagree."""

from datetime import datetime

import polars as pl
import pyreadstat
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.auth import get_current_user
from api.db import Base, QuestionnaireDB, StudyDB, UserDB, get_db
from api.main import app
from src.core.models import DataMap, Variable, VarType


def _study(db, tmp_path, sid, name, brands, rows):
    run = tmp_path / sid
    run.mkdir()
    pl.DataFrame({"uuid": [f"{sid}{i}" for i in range(len(rows))], "BRAND": rows}).write_parquet(
        run / "data.parquet")
    (run / "datamap.json").write_text(DataMap([Variable(
        name="BRAND", label="Brand used", type=VarType.SINGLE, values=brands,
        question_ref="BRAND")]).to_json())
    now = datetime.now().isoformat()
    db.add(StudyDB(id=sid, client_name=name, status="qc", created_at=now, updated_at=now,
                   excel_path=str(run / "deck.xlsx"), study_metadata={}, approval_chain={}))
    db.add(QuestionnaireDB(id=f"q{sid}", study_id=sid, status="approved", source_filename="q.docx",
                           version=1, definition_json="{}", approved_qids=[],
                           created_at=now, updated_at=now))


@pytest.fixture
def client(tmp_path):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: UserDB(
        id="u1", email="t@x.com", name="T", role="owner", is_active=True)
    _study(db, tmp_path, "uk", "UK", {1: "Alpha", 2: "Beta"}, [1, 2, 2])
    _study(db, tmp_path, "fr", "France", {1: "Alpha", 2: "Beta", 3: "Gamma"}, [3, 1])
    _study(db, tmp_path, "de", "Germany", {1: "Alpha", 2: "Delta"}, [2])
    db.commit()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_stack_reports_drift_and_writes_one_labelled_file(client, tmp_path):
    ok = client.get("/api/studies/uk/stack?with=fr")
    assert ok.status_code == 200 and ok.json()["n_breaking"] == 0
    assert ok.json()["changed"][0]["problems"] == ["codes added: [3]"]

    r = client.get("/api/studies/uk/stack.sav?with=fr")
    assert r.status_code == 200, r.text
    (tmp_path / "out.sav").write_bytes(r.content)
    df, meta = pyreadstat.read_sav(str(tmp_path / "out.sav"))
    assert df["SOURCE"].tolist() == [1, 1, 1, 2, 2]
    assert meta.variable_value_labels["SOURCE"] == {1: "UK", 2: "France"}
    assert meta.variable_value_labels["BRAND"][3] == "Gamma"

    bad = client.get("/api/studies/uk/stack?with=de").json()
    assert bad["n_breaking"] == 1
    assert client.get("/api/studies/uk/stack.sav?with=de").status_code == 409
    assert client.get("/api/studies/uk/stack").status_code == 422
    assert client.get("/api/studies/uk/stack?with=nope").status_code == 404

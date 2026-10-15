"""A respondent picks "Other", types what they mean, completes; it reaches the data file."""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import api.routes.materialize as materialize_module
import api.routes.studies as studies_module
from api.auth import get_current_user
from api.db import Base, QuestionnaireDB, StudyDB, UserDB, get_db
from api.main import app
from src.core.models import DataMap
from src.core.survey_definition import Block, Option, Question, QuestionType, SurveyDefinition
from src.engines.questionnaire.write_ins import mark_write_ins


@pytest.fixture
def client(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setattr(materialize_module, "STUDIES_DIR", tmp_path)
    monkeypatch.setattr(studies_module, "STUDIES_DIR", tmp_path)
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: UserDB(
        id="u1", email="t@x.com", name="T", role="owner", is_active=True)
    yield TestClient(app), factory
    app.dependency_overrides.clear()


def _seed(factory):
    q = Question(qid="S3", text="How do you identify?", type=QuestionType.SINGLE,
                 options=[Option(1, "Female"), Option(2, "Male"),
                          Option(4, "Prefer to self-describe (Specify)")])
    survey = SurveyDefinition(
        study_id="w1", title="W", source="authored", status="approved", version=1,
        blocks=[Block(block_id="B1", question_ids=["S3"])], questions=[q])
    mark_write_ins(survey)
    now = datetime.now().isoformat()
    db = factory()
    db.add(StudyDB(id="w1", client_name="T", status="classifying", created_at=now,
                   updated_at=now, study_metadata={}, approval_chain={}))
    db.add(QuestionnaireDB(id="qw1", study_id="w1", status="approved", source_filename="s.docx",
                           version=1, definition_json=survey.to_json(), definition_path=None,
                           approved_qids=["S3"], created_at=now, updated_at=now))
    db.commit()
    db.close()


def test_other_text_is_served_stored_and_materialized(client, tmp_path):
    http, factory = client
    _seed(factory)
    link = http.post("/api/studies/w1/fielding/links").json()
    token = link["token"]

    for answers in ({"S3": 4, "S3r4oe": "Agender"}, {"S3": 1}):
        entry = http.get(f"/api/r/{token}").json()
        names = [e["name"] for p in entry["surveyjs_json"]["pages"] for e in p["elements"]]
        assert names == ["S3", "S3r4oe"]  # the compiled link carries the box
        sid = entry["session_id"]
        assert http.post(f"/api/r/{token}/sessions/{sid}/answers",
                         json={"answers": answers, "current_page": 1}).status_code == 200
        assert http.post(f"/api/r/{token}/sessions/{sid}/complete").status_code == 200

    mat = http.post(f"/api/studies/w1/fielding/links/{link['id']}/materialize")
    assert mat.status_code == 200
    import pandas as pd

    df = pd.read_parquet(tmp_path / "w1" / "data.parquet")
    dm = DataMap.from_json((tmp_path / "w1" / "datamap.json").read_text())
    assert dm["S3r4oe"].type.value == "text"
    assert sorted(df["S3r4oe"].dropna().tolist()) == ["Agender"]
    assert df["S3"].tolist().count(4) == 1

    # ... and the delivery files a client opens.
    import pyreadstat

    sav, _meta = pyreadstat.read_sav(str(tmp_path / "w1" / "w1_responses.sav"))
    assert "Agender" in sav["S3r4oe"].tolist()
    xlsx = pd.read_excel(tmp_path / "w1" / "w1_responses.xlsx", sheet_name="Data")
    assert "Agender" in xlsx["S3r4oe"].tolist()

"""Half the respondents see a bipolar grid's statements flipped; which half is recorded."""

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
from src.core.survey_definition import Block, Option, Question, QuestionType, SurveyDefinition


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
    q = Question(qid="Q41", text="Pairs of opposite statements", type=QuestionType.GRID_SINGLE,
                 rows=[Option(1, "I stick with brands I know | I look for new skincare")],
                 cols=[Option(c, str(c)) for c in range(0, 11)],
                 validation={"bipolar": {"flip_half": True}})
    survey = SurveyDefinition(
        study_id="b1", title="B", source="authored", status="approved", version=1,
        blocks=[Block(block_id="B1", question_ids=["Q41"])], questions=[q])
    now = datetime.now().isoformat()
    db = factory()
    db.add(StudyDB(id="b1", client_name="T", status="classifying", created_at=now,
                   updated_at=now, study_metadata={}, approval_chain={}))
    db.add(QuestionnaireDB(id="qb1", study_id="b1", status="approved", source_filename="s.docx",
                           version=1, definition_json=survey.to_json(), definition_path=None,
                           approved_qids=["Q41"], created_at=now, updated_at=now))
    db.commit()
    db.close()


def test_arms_alternate_the_flipped_half_sees_sides_swapped_and_the_data_says_who(
        client, tmp_path):
    http, factory = client
    _seed(factory)
    link = http.post("/api/studies/b1/fielding/links").json()
    token = link["token"]
    lefts = []
    for _ in range(4):
        entry = http.get(f"/api/r/{token}").json()
        grid = entry["surveyjs_json"]["pages"][0]["elements"][0]
        lefts.append(grid["rows"][0]["leftText"])
        sid = entry["session_id"]
        assert http.post(f"/api/r/{token}/sessions/{sid}/answers",
                         json={"answers": {"Q41": {"1": 2}}, "current_page": 1}).status_code == 200
        assert http.post(f"/api/r/{token}/sessions/{sid}/complete").status_code == 200
    assert lefts == ["I stick with brands I know", "I look for new skincare"] * 2

    assert http.post(f"/api/studies/b1/fielding/links/{link['id']}/materialize").status_code == 200
    import pandas as pd

    df = pd.read_parquet(tmp_path / "b1" / "data.parquet")
    assert sorted(df["HID_BIPOLAR_FLIP"].tolist()) == [1, 1, 2, 2]

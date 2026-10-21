"""Findings route: computed from the stored tables, named in the questionnaire's words."""

import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.auth import get_current_user
from api.db import Base, QuestionnaireDB, UserDB, create_study, get_db
from api.main import app
from src.core.survey_definition import Block, Question, QuestionType, SurveyDefinition


def _t(tid, stub, title, checked):
    return {"id": tid, "stub": stub, "title": title,
            "columns": [{"label": "Total", "letter": None, "base": 80.0, "unweighted_base": 80}],
            "rows": [{"label": label, "pct": [pct], "sig": [""], "kind": "category"}
                     for label, pct in (("Unchecked", 100 - checked), ("Checked", checked))],
            "means": None}


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
    study.excel_path = str(tmp_path / "gone" / "deck.xlsx")  # disk copy gone: DB copy serves
    study.crosstabs_json = json.dumps({"tables": [_t("t1", "Q3r1", "Price", 64.0),
                                                  _t("t2", "Q3r2", "Taste", 41.0)]})
    q = Question(qid="Q3", text="Why did you buy it?", type=QuestionType.MULTI, options=[])
    survey = SurveyDefinition(study_id="s1", title="T", source="authored", status="approved",
                              version=1, blocks=[Block(block_id="B1", question_ids=["Q3"])],
                              questions=[q])
    now = datetime.now().isoformat()
    db.add(QuestionnaireDB(id="q1", study_id="s1", status="approved", source_filename="s.docx",
                           version=1, definition_json=survey.to_json(), approved_qids=["Q3"],
                           created_at=now, updated_at=now))
    db.commit()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_findings_are_named_by_the_questionnaire_and_cited(client):
    r = client.get("/api/studies/s1/findings")
    assert r.status_code == 200, r.text
    (f,) = r.json()["findings"]
    assert f["text"] == "Why did you buy it?: most chosen Price 64%, then Taste 41%."
    assert [(c["table"], c["base"]) for c in f["cites"]] == [("t1", 80), ("t2", 80)]
    assert client.get("/api/studies/nope/findings").status_code == 404

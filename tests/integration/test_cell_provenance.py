"""Cell provenance route: the ordered thread behind one table cell."""

import json
from datetime import datetime

import polars as pl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.auth import get_current_user
from api.db import Base, GraphEdgeDB, QuestionnaireDB, UserDB, create_study, get_db, record_audit
from api.main import app
from src.core.survey_definition import Block, Question, QuestionType, SurveyDefinition
from src.engines.questionnaire.graph_sync import sync_questionnaire

TABLE = {"id": "t1", "stub": "Q3", "title": "Why", "filter": "Q1 = 1",
         "columns": [{"label": "Total", "letter": None, "base": 80.0, "unweighted_base": 100,
                      "effective_base": 70.0}],
         "rows": [{"label": "Price", "pct": [64.0], "sig": [""], "kind": "category"}],
         "means": None}


@pytest.fixture
def env(tmp_path):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: UserDB(
        id="u1", email="t@x.com", name="T", role="analyst", is_active=True)
    study = create_study(db, "s1", "Acme")
    study.excel_path = str(tmp_path / "deck.xlsx")
    study.source_filename = "wave1.sav"
    study.study_metadata = {"uploaded_by": "Priya"}
    study.crosstabs_json = json.dumps({"weight": "wt", "tables": [TABLE]})
    pl.DataFrame({"record": range(100)}).write_parquet(tmp_path / "data.parquet")
    qs = [Question(qid="Q1", text="Do you buy it?", type=QuestionType.SINGLE, options=[]),
          Question(qid="Q3", text="Why did you buy it?", type=QuestionType.MULTI, options=[])]
    qs[1].show_if = None
    survey = SurveyDefinition(study_id="s1", title="T", source="authored", status="approved",
                              version=1, blocks=[Block(block_id="B1", question_ids=["Q1", "Q3"])],
                              questions=qs)
    now = datetime.now().isoformat()
    db.add(QuestionnaireDB(id="q1", study_id="s1", status="approved", source_filename="s.docx",
                           version=1, definition_json=survey.to_json(), approved_qids=["Q1", "Q3"],
                           created_at=now, updated_at=now))
    sync_questionnaire(db, "s1", survey)
    for k in ("a", "b"):
        record_audit(db, entity="cleaning.respondent", entity_id=k, action="remove",
                     actor_id="u1", study_id="s1", after={"reason_code": "speeder"})
    db.commit()
    yield TestClient(app), db
    app.dependency_overrides.clear()


def test_thread_steps_in_order_with_right_base(env):
    client, db = env
    r = client.get("/api/studies/s1/tables/t1/provenance?row=0&col=0")
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["value"], body["label"], body["column"]) == (64.0, "Price", "Total")
    titles = [s["title"] for s in body["steps"]]
    assert titles == ["The cell", "The base", "Question Q3: Why did you buy it?",
                      "Cleaning", "Weight", "The data file"]
    s = {x["title"]: x["detail"] for x in body["steps"]}
    assert "Weighted base 80" in s["The cell"] and "unweighted base 100" in s["The cell"]
    assert "Q1 = 1" in s["The base"]
    assert "2 respondents removed" in s["Cleaning"] and "2 speeder" in s["Cleaning"]
    assert "Efficiency 70%" in s["Weight"]
    assert s["The data file"].startswith("wave1.sav") and "by Priya" in s["The data file"]
    assert "100 rows" in s["The data file"]
    edges = db.query(GraphEdgeDB).filter_by(kind="computed_from", src="s1:cell:t1:0:0").all()
    assert [e.dst for e in edges] == ["s1:question:Q3"]


@pytest.mark.parametrize("url", ["/tables/nope/provenance?row=0&col=0",
                                 "/tables/t1/provenance?row=5&col=0",
                                 "/tables/t1/provenance?row=0&col=3",
                                 "/tables/t1/provenance?row=-1&col=0"])
def test_bad_table_row_or_column_is_404(env, url):
    assert env[0].get("/api/studies/s1" + url).status_code == 404

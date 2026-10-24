"""Templates and modules: a questionnaire built from blocks, with their routing."""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.auth import get_current_user
from api.db import Base, QuestionnaireDB, StudyDB, UserDB, get_db
from api.main import app
from src.core.survey_definition import SurveyDefinition

INPUTS = {"organisation": "DVV", "purpose": "coffee choices", "contact": "p@dvv.in",
          "category": "instant coffee", "brands": "Nescafé\nBru", "subject": "Nescafé",
          "attributes": "Taste\nPrice"}


@pytest.fixture
def client():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: UserDB(
        id="u1", email="t@x.com", name="T", role="analyst", is_active=True)
    now = datetime.now().isoformat()
    empty = SurveyDefinition(study_id="s1", title="Coffee", source="authored", status="draft",
                             version=1, blocks=[], questions=[])
    db.add(StudyDB(id="s1", client_name="C", status="authoring", created_at=now, updated_at=now,
                   study_metadata={}, approval_chain={}))
    db.add(QuestionnaireDB(id="q1", study_id="s1", status="draft", source_filename=None,
                           version=1, definition_json=empty.to_json(), approved_qids=[],
                           created_at=now, updated_at=now))
    db.commit()
    yield TestClient(app), db
    app.dependency_overrides.clear()


def _survey(db):
    db.expire_all()
    return SurveyDefinition.from_json(db.get(QuestionnaireDB, "q1").definition_json)


def test_a_template_then_a_module_build_a_valid_questionnaire(client):
    c, db = client
    lib = c.get("/api/questionnaire/library").json()
    assert {t["id"] for t in lib["templates"]} >= {"brand_tracker", "concept_test", "hcp"}
    bad = c.post("/api/studies/s1/questionnaire/template",
                 json={"template": "brand_tracker", "inputs": {"category": "coffee"}})
    assert bad.status_code == 422 and "Brands" in bad.json()["detail"]

    r = c.post("/api/studies/s1/questionnaire/template",
               json={"template": "brand_tracker", "inputs": INPUTS})
    assert r.status_code == 200, r.text
    assert r.json()["added"][:3] == ["DPDP_NOTICE", "CONSENT", "S_AGE"]
    assert c.post("/api/studies/s1/questionnaire/template",
                  json={"template": "brand_tracker", "inputs": INPUTS}).status_code == 409

    m = c.post("/api/studies/s1/questionnaire/modules",
               json={"module": "nps", "inputs": {"subject": "Bru"}, "after_qid": "S_GENDER"})
    assert m.json()["added"] == ["NPS_2", "NPS_WHY_2"]  # the template already has NPS
    survey = _survey(db)
    order = [q.qid for q in survey.questions]
    assert order[order.index("S_GENDER") + 1] == "NPS_2"
    assert survey.validate() == []
    assert survey["AWR_CONSIDER"].show_if.to_dict()["children"][0]["qid"] == "AWR_AIDED"


def test_expert_checks_read_the_questionnaire(client):
    c, _db = client
    c.post("/api/studies/s1/questionnaire/template",
           json={"template": "brand_tracker", "inputs": INPUTS})
    r = c.get("/api/studies/s1/questionnaire/expert-checks").json()
    assert r["n_fix"] == 0 and r["length"]["minutes"] > 0

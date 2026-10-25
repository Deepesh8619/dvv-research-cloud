import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.auth import get_current_user
from api.db import (
    Base,
    FieldingLinkDB,
    QuestionnaireDB,
    RespondentSessionDB,
    StudyDB,
    UserDB,
    get_db,
)
from api.main import app


@pytest.fixture
def env():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    factory = sessionmaker(bind=eng)
    app.dependency_overrides[get_db] = lambda: factory()
    app.dependency_overrides[get_current_user] = lambda: UserDB(
        id="u1", email="t@x.com", name="T", role="analyst", is_active=True)
    now = datetime.now().isoformat()
    with factory() as db:
        db.add(StudyDB(id="s1", client_name="C", status="classifying", created_at=now,
                       updated_at=now, study_metadata={}, approval_chain={}))
        db.commit()
    yield TestClient(app), factory
    app.dependency_overrides.clear()


def states(c, sid="s1"):
    r = c.get(f"/api/studies/{sid}/lifecycle")
    assert r.status_code == 200
    return r.json(), {s["key"]: s["state"] for s in r.json()["stages"]}


def test_new_study_is_all_todo(env):
    c, _ = env
    body, st = states(c)
    assert [s["label"] for s in body["stages"]] == [
        "Brief", "Questionnaire", "Proof", "Field", "Verify", "Data", "Tables", "Insight"]
    assert set(st.values()) == {"todo"}
    assert body["needs_you"] == []
    assert c.get("/api/studies/nope/lifecycle").status_code == 404


def test_flagged_draft_needs_you_then_approved_with_link(env):
    c, f = env
    with f() as db:
        defn = json.dumps({"questions": [{"qid": "Q1", "flags": ["AMBIGUOUS_ROUTING"]}]})
        db.add(QuestionnaireDB(id="q1", study_id="s1", status="draft", definition_json=defn,
                               approved_qids=[], created_at="1"))
        db.commit()
    body, st = states(c)
    assert st["questionnaire"] == "attention"
    assert [n["kind"] for n in body["needs_you"]] == ["questionnaire"]
    with f() as db:
        db.get(QuestionnaireDB, "q1").status = "approved"
        db.add(FieldingLinkDB(id="l1", study_id="s1", token="t", target_completes=2, is_test=False))
        db.add(RespondentSessionDB(id="r1", fielding_link_id="l1", status_code=10))
        db.commit()
    body, st = states(c)
    assert st["questionnaire"] == "done" and st["field"] == "todo" and st["proof"] == "todo"
    assert next(s for s in body["stages"] if s["key"] == "field")["detail"] == "1/2 completes"
    with f() as db:
        db.add(RespondentSessionDB(id="r2", fielding_link_id="l1", status_code=10))
        db.commit()
    assert states(c)[1]["field"] == "done"


def test_processed_data(env):
    c, f = env
    with f() as db:
        s = db.get(StudyDB, "s1")
        s.datamap_json, s.crosstabs_json, s.pptx_path = "{}", "{}", "/x/deck.pptx"
        s.study_metadata = {"description": "d", "qc_passed": False}
        db.commit()
    body, st = states(c)
    assert (st["brief"], st["data"], st["tables"], st["insight"]) == ("done",) * 4
    assert st["verify"] == "attention"
    assert body["needs_you"][0]["tab"] == "Cleaning"


def test_proof_reads_the_last_dry_run():
    from src.engines.lifecycle import _proof

    assert _proof(None)[0] == "todo"
    assert _proof({"n": 500, "impossible": 0, "never_fired": 0, "never_shown": 0}) == (
        "done", "Dry run of 500 found no logic problems", "/fielding")
    state, detail, _ = _proof({"n": 500, "impossible": 0, "never_fired": 1, "never_shown": 2})
    assert state == "attention"
    assert "1 rule never fired" in detail and "2 questions nobody" in detail

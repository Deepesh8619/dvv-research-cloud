"""Consent ledger export: one row per fielded session with the consent answer."""

import csv
import io
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.auth import get_current_user
from api.db import Base, QuestionnaireDB, StudyDB, UserDB, get_db
from api.main import app
from src.core.survey_definition import (
    Block,
    Expression,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
)

CONSENT_TEXT = "Do you agree to take part?"


def _survey(with_consent: bool) -> SurveyDefinition:
    qs = [Question(qid="Q2", text="Do you drink coffee?", type=QuestionType.SINGLE,
                   options=[Option(code=1, label="Yes"), Option(code=2, label="No")])]
    if with_consent:
        qs.insert(0, Question(
            qid="C1", text=CONSENT_TEXT, type=QuestionType.SINGLE,
            options=[Option(code=1, label="I agree"), Option(code=2, label="I do not agree")],
            terminate_if=Expression(op="eq", qid="C1", value=2),
            validation={"consent": True},
        ))
    return SurveyDefinition(
        study_id="s1", title="T", source="authored", status="approved", version=1,
        blocks=[Block(block_id="B1", question_ids=[q.qid for q in qs])], questions=qs,
    )


@pytest.fixture
def client(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    state = {"role": "owner"}
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: UserDB(
        id="u1", email="t@x.com", name="T", role=state["role"], is_active=True)
    yield TestClient(app), SessionLocal, state
    app.dependency_overrides.clear()


def _seed(sf, with_consent=True):
    survey, now = _survey(with_consent), datetime.now().isoformat()
    db = sf()
    db.add(StudyDB(id="s1", client_name="T", status="classifying", created_at=now,
                   updated_at=now, study_metadata={}, approval_chain={}))
    db.add(QuestionnaireDB(id="q1", study_id="s1", status="approved", source_filename="s.docx",
                           version=1, definition_json=survey.to_json(), definition_path=None,
                           approved_qids=[q.qid for q in survey.questions],
                           created_at=now, updated_at=now))
    db.commit()
    db.close()


def _ledger(c):
    r = c.get("/api/studies/s1/compliance/consent.csv")
    return r, list(csv.DictReader(io.StringIO(r.text)))


def test_consenting_and_refusing_sessions_both_logged(client):
    c, sf, _ = client
    _seed(sf)
    token = c.post("/api/studies/s1/fielding/links").json()["token"]
    for code in (1, 2):
        sid = c.get(f"/api/r/{token}").json()["session_id"]
        resp = c.post(f"/api/r/{token}/sessions/{sid}/answers",
                      json={"answers": {"C1": code}, "current_page": 1})
        assert resp.status_code == 200
    r, rows = _ledger(c)
    assert r.status_code == 200 and r.headers["X-Consent-Questions"] == "1"
    assert len(rows) == 2
    assert {x["answer_label"] for x in rows} == {"I agree", "I do not agree"}
    assert all(x["answered_at"] and x["consent_question_id"] == "C1" for x in rows)
    assert len({x["consent_text_sha256"] for x in rows}) == 1
    assert len(rows[0]["consent_text_sha256"]) == 64


def test_unanswered_session_still_listed(client):
    c, sf, _ = client
    _seed(sf)
    token = c.post("/api/studies/s1/fielding/links").json()["token"]
    c.get(f"/api/r/{token}")
    _, rows = _ledger(c)
    assert len(rows) == 1 and rows[0]["answer_label"] == "" and rows[0]["answered_at"] == ""


def test_reviewer_refused(client):
    c, sf, state = client
    _seed(sf)
    state["role"] = "reviewer"
    assert c.get("/api/studies/s1/compliance/consent.csv").status_code == 403


def test_no_consent_question_gives_headers_only(client):
    c, sf, _ = client
    _seed(sf, with_consent=False)
    token = c.post("/api/studies/s1/fielding/links").json()["token"]
    c.get(f"/api/r/{token}")
    r, rows = _ledger(c)
    assert r.status_code == 200 and r.headers["X-Consent-Questions"] == "0"
    assert rows == [] and r.text.startswith("session_id,link_id,")


def _fielded(c, sf, pid="P9"):
    _seed(sf)
    token = c.post("/api/studies/s1/fielding/links").json()["token"]
    sid = c.get(f"/api/r/{token}?pid={pid}").json()["session_id"]
    c.post(f"/api/r/{token}/sessions/{sid}/answers", json={"answers": {"C1": 1}, "current_page": 1})
    return sid


def test_erase_respondent_by_id_and_pid(client):
    from api.db import AuditLogDB, RespondentSessionDB, answers_by_session

    c, sf, _ = client
    sid = _fielded(c, sf)
    link = c.get("/api/studies/s1/fielding/links").json()
    link_id = (link["links"] if isinstance(link, dict) else link)[0]["id"]
    url = f"/api/studies/s1/fielding/links/{link_id}/field-report"
    before = c.get(url).json()["starts"]
    r = c.delete("/api/studies/s1/respondents/P9")  # by pid
    assert r.status_code == 200 and r.json()["needs_rematerialize"] and r.json()["limits"]
    db = sf()
    assert sid not in answers_by_session(db, link_id)
    row = db.get(RespondentSessionDB, sid)
    assert row.pid is None and row.entry_params == {"erased": True}
    aud = db.query(AuditLogDB).filter(AuditLogDB.entity == "compliance.erasure").all()
    assert len(aud) == 1 and aud[0].entity_id == sid and "P9" not in (aud[0].summary or "")
    db.close()
    assert c.get(url).json()["starts"] == before
    assert c.delete(f"/api/studies/s1/respondents/{sid}").status_code == 200  # by id


def test_erase_other_study_404_and_analyst_403(client):
    c, sf, state = client
    sid = _fielded(c, sf)
    db = sf()
    from api.db import StudyDB
    now = datetime.now().isoformat()
    db.add(StudyDB(id="s2", client_name="O", status="classifying", created_at=now,
                   updated_at=now, study_metadata={}, approval_chain={}))
    db.commit()
    db.close()
    assert c.delete(f"/api/studies/s2/respondents/{sid}").status_code == 404
    state["role"] = "analyst"
    assert c.delete(f"/api/studies/s1/respondents/{sid}").status_code == 403


# ---- retention + purge (P4) ----

def _purge_setup(c, sf, tmp_path, monkeypatch, retain):
    import api.config
    from api.db import ChatMessageDB, ShareLinkDB, StudyDB

    root = tmp_path / "studies"
    monkeypatch.setattr(api.config, "STUDIES_DIR", root)
    sid = _fielded(c, sf)
    (root / "s1" / "sub").mkdir(parents=True)
    (root / "s1" / "sub" / "data.parquet").write_text("x")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    (root / "s1" / "link").symlink_to(outside)  # must not be followed
    db = sf()
    st = db.get(StudyDB, "s1")
    st.study_metadata = {"retain_until": retain}
    st.source_file_b64 = "abc"
    db.add(ChatMessageDB(id="m1", study_id="s1", role="user", content="hi", created_at="x"))
    db.add(ShareLinkDB(token="tok", study_id="s1", revoked=False))
    db.commit()
    return db, root, outside, sid


def test_purge_dry_run_then_apply(client, tmp_path, monkeypatch):
    import sys

    from api.db import AuditLogDB, RespondentAnswerDB, RespondentSessionDB, ShareLinkDB, StudyDB
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import purge_expired

    c, sf, _ = client
    db, root, outside, _sid = _purge_setup(c, sf, tmp_path, monkeypatch, "2020-01-01")
    dry = purge_expired.run(db)
    assert [d[0] for d in dry] == ["s1"] and dry[0][1]["sessions"] == 1
    assert (root / "s1" / "sub" / "data.parquet").exists()
    assert db.query(RespondentSessionDB).count() == 1

    purge_expired.run(db, apply=True)
    assert not (root / "s1").exists() and (outside / "keep.txt").exists()
    assert db.query(RespondentSessionDB).count() == 0 and db.query(RespondentAnswerDB).count() == 0
    st = db.get(StudyDB, "s1")
    assert st.status == "purged" and st.source_file_b64 is None
    assert db.get(ShareLinkDB, "tok").revoked
    ents = {a.entity for a in db.query(AuditLogDB).all()}
    assert "compliance.purge" in ents
    assert purge_expired.run(db) == []  # already purged


def test_purge_future_refused_unless_force(client, tmp_path, monkeypatch):
    import sys

    from api.db import purge_study_data
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import purge_expired

    c, sf, _ = client
    db, root, _o, _s = _purge_setup(c, sf, tmp_path, monkeypatch, "2999-01-01")
    assert purge_expired.run(db) == []
    with pytest.raises(PermissionError):
        purge_study_data(db, "s1")
    assert (root / "s1").exists()
    assert purge_study_data(db, "s1", force=True)["files"] >= 1


def test_purge_never_deletes_outside_root(client, tmp_path, monkeypatch):
    from api.db import StudyDB, purge_study_data

    c, sf, _ = client
    db, root, outside, _s = _purge_setup(c, sf, tmp_path, monkeypatch, "2020-01-01")
    db.get(StudyDB, "s1").excel_path = str(outside / "x.xlsx")
    db.commit()
    assert purge_study_data(db, "s1")["paths_skipped"] == 1
    assert (outside / "keep.txt").exists()


def test_retention_patch(client):
    c, sf, state = client
    _seed(sf)
    url = "/api/studies/s1/retention"
    assert c.patch(url, json={"retain_until": "2020-01-01"}).status_code == 422
    assert c.patch(url, json={"retain_until": "2999-01-01"}).json()["retain_until"] == "2999-01-01"
    assert c.patch(url, json={"retain_until": None}).status_code == 200
    state["role"] = "analyst"
    assert c.patch(url, json={"retain_until": "2999-01-01"}).status_code == 403


def test_the_notice_is_checked_added_first_and_then_fills_the_ledger(client):
    c, sf, _state = client
    _seed(sf, with_consent=False)
    before = c.get("/api/studies/s1/compliance/notice").json()
    assert not before["complete"] and before["consent_questions"] == []
    body = {"organisation": "DVV Research", "purpose": "understanding coffee habits",
            "contact": "privacy@dvv.example"}
    after = c.post("/api/studies/s1/compliance/notice", json=body)
    assert after.status_code == 200 and after.json()["complete"], after.json()
    survey = SurveyDefinition.from_json(sf().get(QuestionnaireDB, "q1").definition_json)
    assert [q.qid for q in survey.questions][:2] == ["DPDP_NOTICE", "CONSENT"]
    assert survey.blocks[0].question_ids[:2] == ["DPDP_NOTICE", "CONSENT"]
    assert c.post("/api/studies/s1/compliance/notice", json=body).status_code == 409
    assert c.post("/api/studies/s1/compliance/notice",
                  json={**body, "contact": ""}).status_code == 422


def test_adverse_events_are_read_from_every_interview(client, tmp_path):
    import polars as pl

    from src.core.models import DataMap, Variable, VarType

    c, sf, _state = client
    _seed(sf)
    run = tmp_path / "run"
    run.mkdir()
    pl.DataFrame({"uuid": ["a", "b", "c"], "Q9": [
        "My patient developed a rash after the first dose.",
        "Efficacy and a clean side effect profile.", None]}).write_parquet(run / "data.parquet")
    (run / "datamap.json").write_text(DataMap([Variable(
        name="Q9", label="Anything else?", type=VarType.TEXT, question_ref="Q9")]).to_json())
    db = sf()
    db.get(StudyDB, "s1").excel_path = str(run / "out.xlsx")
    db.commit()
    db.close()
    r = c.get("/api/studies/s1/compliance/adverse-events")
    assert r.status_code == 200, r.text
    out = r.json()
    assert [(h["respondent"], h["terms"]) for h in out["possible"]] == [("a", ["rash"])]
    assert [h["respondent"] for h in out["mentions"]] == ["b"]

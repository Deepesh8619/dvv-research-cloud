"""POST /api/studies: create a data-less study (survey-first path).

Same TestClient + temp-sqlite override pattern as test_questionnaire_routes.py,
with a mutable role in a state dict so one fixture covers owner/analyst/reviewer.
Covers: the empty study is created in 'authoring' with no excel_path (owner and
analyst), a reviewer is 403, and the key end-to-end — a study created data-less
can have a survey authored + approved on it and reach a fielding link (the same
approve -> field path an extracted survey uses).
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import api.routes.questionnaire as qroutes
from api.auth import get_current_user
from api.db import Base, StudyDB, UserDB, get_db
from api.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path}/test.db", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    import api.db as api_db

    monkeypatch.setattr(api_db, "SessionLocal", TestSessionLocal)
    # Keep any authored-questionnaire on-disk artifacts inside tmp_path.
    monkeypatch.setattr(qroutes, "STUDIES_DIR", tmp_path)

    state = {"role": "owner"}

    def override_get_current_user():
        return UserDB(
            id="u1", email="test@x.com", name="Test", role=state["role"], is_active=True
        )

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    yield TestClient(app), TestSessionLocal, state
    app.dependency_overrides.clear()


def test_create_empty_study_owner(client):
    test_client, session_factory, _ = client
    resp = test_client.post("/api/studies", json={"client_name": "New Tracker", "kind": "builder"})
    assert resp.status_code == 200
    body = resp.json()

    assert body["id"].startswith("study_")
    assert body["client_name"] == "New Tracker"
    # Data-less: authoring status, no rendered deck.
    assert body["status"] == "authoring"
    assert body["excel_path"] is None
    assert body["pptx_path"] is None
    assert body["approval_chain"] == []

    # Persisted and readable via the normal detail endpoint.
    detail = test_client.get(f"/api/studies/{body['id']}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "authoring"

    # Row genuinely has no excel_path in the DB.
    db = session_factory()
    row = db.query(StudyDB).filter(StudyDB.id == body["id"]).first()
    assert row.excel_path is None
    assert row.study_metadata.get("kind") == "builder"
    db.close()


def test_create_empty_study_analyst(client):
    test_client, _, state = client
    state["role"] = "analyst"
    resp = test_client.post("/api/studies", json={"client_name": "Analyst Study"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "authoring"


def test_create_empty_study_defaults_name(client):
    test_client, _, _ = client
    resp = test_client.post("/api/studies", json={})
    assert resp.status_code == 200
    assert resp.json()["client_name"] == "Untitled project"


def test_create_empty_study_reviewer_403(client):
    test_client, _, state = client
    state["role"] = "reviewer"
    resp = test_client.post("/api/studies", json={"client_name": "Nope"})
    assert resp.status_code == 403


def test_survey_only_study_reaches_fielding(client):
    """End-to-end unification: a study created data-less (no upload, no pipeline)
    can have a survey AUTHORED on it, approved, and opened as a fielding link —
    proving the survey-only path reaches fielding without ever touching data."""
    test_client, _, _ = client

    # 1. Create the empty study via the new endpoint.
    created = test_client.post(
        "/api/studies", json={"client_name": "Survey Only", "kind": "builder"}
    )
    assert created.status_code == 200
    study_id = created.json()["id"]

    # 2. Author a blank questionnaire on it, then add two questions.
    authored = test_client.post(
        f"/api/studies/{study_id}/questionnaire/authored", json={"title": "Fieldable"}
    )
    assert authored.status_code == 200
    assert authored.json()["status"] == "draft"

    assert test_client.post(
        f"/api/studies/{study_id}/questionnaire/questions",
        json={"qid": "Q1", "text": "Age", "type": "numeric"},
    ).status_code == 200
    assert test_client.post(
        f"/api/studies/{study_id}/questionnaire/questions",
        json={
            "qid": "Q2",
            "text": "Interested?",
            "type": "single",
            "options": [{"code": 1, "label": "Yes"}, {"code": 2, "label": "No"}],
            "terminate_if": {"op": "lt", "qid": "Q1", "value": 18},
        },
    ).status_code == 200

    # 3. Approve each question, then the whole questionnaire.
    for qid in ("Q1", "Q2"):
        assert test_client.post(
            f"/api/studies/{study_id}/questionnaire/questions/{qid}/approve"
        ).status_code == 200
    approved = test_client.post(f"/api/studies/{study_id}/questionnaire/approve-all")
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"

    # 4. Open a fielding link — the same path an extracted survey uses.
    link = test_client.post(f"/api/studies/{study_id}/fielding/links")
    assert link.status_code == 200
    assert link.json()["token"]
    assert link.json()["status"] == "open"


def test_a_data_file_can_be_attached_to_a_project_started_from_its_questionnaire(
    client, tmp_path, monkeypatch
):
    """The questionnaire-first project had nowhere to put the client's export."""
    import api.routes.studies as sroutes

    test_client, _, _ = client
    monkeypatch.setattr(sroutes, "STUDIES_DIR", tmp_path)
    ran = []

    class _NoThread:
        def __init__(self, target, args, daemon):
            self.target, self.args = target, args

        def start(self):
            ran.append(self.args)

    monkeypatch.setattr(sroutes.threading, "Thread", _NoThread)

    study_id = test_client.post("/api/studies", json={"client_name": "Q first"}).json()["id"]
    (tmp_path / study_id).mkdir()
    (tmp_path / study_id / "old_wave.xlsx").write_bytes(b"old")

    resp = test_client.post(
        f"/api/studies/{study_id}/data",
        files={"file": ("fielded.xlsx", b"PK fake workbook", "application/octet-stream")},
    )
    assert resp.status_code == 200 and resp.json()["source"] == "fielded.xlsx"
    assert [p.name for p in (tmp_path / study_id).iterdir()] == ["fielded.xlsx"]
    assert ran and ran[0][0] == study_id and ran[0][2].name == "fielded.xlsx"
    assert test_client.get(f"/api/studies/{study_id}").json()["status"] == "classifying"

    bad = test_client.post(
        f"/api/studies/{study_id}/data",
        files={"file": ("notes.docx", b"x", "application/octet-stream")},
    )
    assert bad.status_code == 400

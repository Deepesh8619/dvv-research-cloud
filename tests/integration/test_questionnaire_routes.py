"""Questionnaire ingestion + review endpoints.

TestClient against the real app with get_db / get_current_user overridden to a
temp sqlite session and a fake user (same pattern as test_studies_profile.py).
The extraction pipeline (load_docx/load_pdf -> segment -> extract) is patched so
tests make no real LLM calls and need no real .docx/.pdf files: upload's
background thread calls a stubbed `extract_survey_definition` that returns a
hand-built SurveyDefinition, and the loader/segmenter are patched to no-ops so
the real file-parsing path is never entered.
"""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import api.routes.questionnaire as qroutes
from api.auth import get_current_user
from api.db import Base, StudyDB, UserDB, get_db
from api.main import app
from src.core.survey_definition import (
    Block,
    Expression,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
)


def _sample_survey(study_id: str, *, with_flags: bool = False) -> SurveyDefinition:
    """A small, valid two-question survey. With `with_flags`, Q2 carries an
    unresolved-routing flag so approve-all's gate has something to reject on."""
    q1 = Question(
        qid="Q1",
        text="What is your age?",
        type=QuestionType.NUMERIC,
        source_text="Q1. What is your age?",
        source_location="para 1",
        extraction_confidence={"text": 0.98},
    )
    q2 = Question(
        qid="Q2",
        text="Which brands do you know?",
        type=QuestionType.MULTI,
        options=[Option(code=1, label="Alpha"), Option(code=2, label="Beta")],
        source_text="Q2. Which brands do you know?",
        source_location="para 2",
        extraction_confidence={"text": 0.9, "options": 0.85},
        flags=["AMBIGUOUS_ROUTING"] if with_flags else [],
    )
    survey = SurveyDefinition(
        study_id=study_id,
        title="Sample",
        source="extracted",
        status="draft",
        blocks=[Block(block_id="B1", question_ids=["Q1", "Q2"])],
        questions=[q1, q2],
    )
    survey.metadata["validation_errors"] = survey.validate()
    survey.metadata["extraction_summary"] = {
        "total_chunks": 2,
        "flag_counts": {"AMBIGUOUS_ROUTING": 1} if with_flags else {},
        "average_confidence": 0.9,
        "verified_chunks": 2,
    }
    return survey


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

    # The background thread in _run_extraction uses SessionLocal directly, not
    # the request session — point it at the test engine too.
    monkeypatch.setattr(qroutes, "SessionLocal", TestSessionLocal, raising=False)
    import api.db as api_db

    monkeypatch.setattr(api_db, "SessionLocal", TestSessionLocal)
    # Keep questionnaire files inside tmp_path.
    monkeypatch.setattr(qroutes, "STUDIES_DIR", tmp_path)

    state = {"role": "owner", "survey": _sample_survey("study_q1")}

    def override_get_current_user():
        return UserDB(
            id="u1", email="test@x.com", name="Test", role=state["role"], is_active=True
        )

    # Patch the loader/segmenter to no-ops and extraction to return our fixture.
    monkeypatch.setattr(qroutes, "load_docx", lambda p: [])
    monkeypatch.setattr(qroutes, "load_pdf", lambda p: [])
    monkeypatch.setattr(qroutes, "segment_into_questions", lambda blocks: [])
    monkeypatch.setattr(
        qroutes,
        "extract_survey_definition",
        lambda chunks, study_id: state["survey"],
    )

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    yield TestClient(app), TestSessionLocal, state
    app.dependency_overrides.clear()


def _make_study(session_factory, study_id: str = "study_q1"):
    db = session_factory()
    db.add(
        StudyDB(
            id=study_id,
            client_name="Test",
            status="classifying",
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
            study_metadata={},
            approval_chain={},
        )
    )
    db.commit()
    db.close()


def _upload(test_client, study_id="study_q1", filename="survey.docx"):
    """Upload runs extraction synchronously here because the daemon thread joins
    fast against the stubbed pipeline; poll status until it leaves 'processing'."""
    resp = test_client.post(
        f"/api/studies/{study_id}/questionnaire/upload",
        files={"file": (filename, b"dummy-bytes", "application/octet-stream")},
    )
    return resp


def _wait_draft(test_client, study_id="study_q1"):
    for _ in range(50):
        body = test_client.get(f"/api/studies/{study_id}/questionnaire").json()
        if body["status"] != "processing":
            return body
        import time

        time.sleep(0.02)
    raise AssertionError("questionnaire never left 'processing'")


def test_upload_creates_processing_then_draft(client):
    test_client, session_factory, _ = client
    _make_study(session_factory)

    resp = _upload(test_client)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "processing"
    assert body["questionnaire_id"].startswith("quest_")

    status = _wait_draft(test_client)
    assert status["status"] == "draft"
    assert status["n_questions"] == 2
    # Title is derived from the uploaded filename stem (survey.docx -> "survey").
    assert status["title"] == "survey"
    assert status["extraction_summary"]["total_chunks"] == 2


def test_upload_rejects_bad_filetype(client):
    test_client, session_factory, _ = client
    _make_study(session_factory)
    resp = test_client.post(
        "/api/studies/study_q1/questionnaire/upload",
        files={"file": ("data.csv", b"x", "text/csv")},
    )
    assert resp.status_code == 400


def test_upload_404_unknown_study(client):
    test_client, _, _ = client
    resp = _upload(test_client, study_id="nope")
    assert resp.status_code == 404


def test_get_questions_shape(client):
    test_client, session_factory, _ = client
    _make_study(session_factory)
    _upload(test_client)
    _wait_draft(test_client)

    resp = test_client.get("/api/studies/study_q1/questionnaire/questions")
    assert resp.status_code == 200
    questions = resp.json()["questions"]
    assert len(questions) == 2
    q2 = next(q for q in questions if q["qid"] == "Q2")
    assert q2["source_text"].startswith("Q2.")
    assert "extraction_confidence" in q2
    assert "flags" in q2
    assert q2["approved"] is False


def test_get_single_question(client):
    test_client, session_factory, _ = client
    _make_study(session_factory)
    _upload(test_client)
    _wait_draft(test_client)

    resp = test_client.get("/api/studies/study_q1/questionnaire/questions/Q1")
    assert resp.status_code == 200
    assert resp.json()["qid"] == "Q1"

    assert (
        test_client.get("/api/studies/study_q1/questionnaire/questions/ZZ").status_code
        == 404
    )


def test_patch_updates_question(client):
    test_client, session_factory, _ = client
    _make_study(session_factory)
    _upload(test_client)
    _wait_draft(test_client)

    resp = test_client.patch(
        "/api/studies/study_q1/questionnaire/questions/Q1",
        json={"text": "How old are you (in years)?"},
    )
    assert resp.status_code == 200
    assert resp.json()["text"] == "How old are you (in years)?"

    # Persisted.
    detail = test_client.get("/api/studies/study_q1/questionnaire/questions/Q1").json()
    assert detail["text"] == "How old are you (in years)?"


def test_patch_rejects_invalid_edit(client):
    """An edit that breaks SurveyDefinition.validate() (duplicate option codes)
    must be rejected and NOT persisted."""
    test_client, session_factory, _ = client
    _make_study(session_factory)
    _upload(test_client)
    _wait_draft(test_client)

    resp = test_client.patch(
        "/api/studies/study_q1/questionnaire/questions/Q2",
        json={"options": [{"code": 1, "label": "A"}, {"code": 1, "label": "B"}]},
    )
    assert resp.status_code == 422
    assert "problems" in resp.json()["detail"]

    # Q2 still has its original two distinct options.
    q2 = test_client.get("/api/studies/study_q1/questionnaire/questions/Q2").json()
    assert [o["code"] for o in q2["options"]] == [1, 2]


def test_approve_all_gate(client):
    """approve-all fails while questions are unresolved, succeeds once every
    question is approved and no blocking flags remain."""
    test_client, session_factory, state = client
    _make_study(session_factory)
    _upload(test_client)
    _wait_draft(test_client)

    # Nothing approved yet -> gate fails, lists both qids.
    resp = test_client.post("/api/studies/study_q1/questionnaire/approve-all")
    assert resp.status_code == 422
    unresolved = {u["qid"] for u in resp.json()["detail"]["unresolved"]}
    assert unresolved == {"Q1", "Q2"}

    # Approve both.
    for qid in ("Q1", "Q2"):
        r = test_client.post(f"/api/studies/study_q1/questionnaire/questions/{qid}/approve")
        assert r.status_code == 200

    resp = test_client.post("/api/studies/study_q1/questionnaire/approve-all")
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"
    assert test_client.get("/api/studies/study_q1/questionnaire").json()["status"] == "approved"


def test_approve_all_blocked_by_flags_until_approved(client):
    """A blocking flag stops approve-all while the question is un-approved, but
    individually approving the question CLEARS its blocking flags (T18), so
    approve-all becomes reachable once every question is hand-reviewed."""
    test_client, session_factory, state = client
    state["survey"] = _sample_survey("study_q1", with_flags=True)
    _make_study(session_factory)
    _upload(test_client)
    _wait_draft(test_client)

    # Approve Q1 only: Q2 still carries AMBIGUOUS_ROUTING and blocks the gate.
    test_client.post("/api/studies/study_q1/questionnaire/questions/Q1/approve")
    resp = test_client.post("/api/studies/study_q1/questionnaire/approve-all")
    assert resp.status_code == 422
    unresolved = {u["qid"] for u in resp.json()["detail"]["unresolved"]}
    assert unresolved == {"Q2"}  # not approved + AMBIGUOUS_ROUTING

    # Approving Q2 clears its blocking flag (reported back in the response) ...
    r = test_client.post("/api/studies/study_q1/questionnaire/questions/Q2/approve")
    assert r.status_code == 200
    assert "AMBIGUOUS_ROUTING" in r.json()["cleared_flags"]

    # ... the cleared flag is persisted (survives a reload) ...
    q2 = test_client.get("/api/studies/study_q1/questionnaire/questions/Q2").json()
    assert "AMBIGUOUS_ROUTING" not in q2["flags"]

    # ... and now the gate is reachable.
    resp = test_client.post("/api/studies/study_q1/questionnaire/approve-all")
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"


def test_reviewer_is_read_only(client):
    """Reviewer role: GET allowed, all writes 403."""
    test_client, session_factory, state = client
    _make_study(session_factory)
    # Seed a questionnaire as owner first.
    _upload(test_client)
    _wait_draft(test_client)

    state["role"] = "reviewer"

    # Reads work.
    assert test_client.get("/api/studies/study_q1/questionnaire").status_code == 200
    assert (
        test_client.get("/api/studies/study_q1/questionnaire/questions").status_code == 200
    )

    # Writes rejected.
    assert (
        test_client.post(
            "/api/studies/study_q1/questionnaire/upload",
            files={"file": ("s.docx", b"x", "application/octet-stream")},
        ).status_code
        == 403
    )
    assert (
        test_client.patch(
            "/api/studies/study_q1/questionnaire/questions/Q1", json={"text": "x"}
        ).status_code
        == 403
    )
    assert (
        test_client.post(
            "/api/studies/study_q1/questionnaire/questions/Q1/approve"
        ).status_code
        == 403
    )
    assert (
        test_client.post("/api/studies/study_q1/questionnaire/approve-all").status_code
        == 403
    )


def test_patch_clears_routing_with_explicit_null(client):
    """An explicit show_if: null clears routing; the Expression import proves the
    fixture path exercises real Expression round-tripping on disk."""
    test_client, session_factory, state = client
    survey = _sample_survey("study_q1")
    survey["Q2"].show_if = Expression(op="eq", qid="Q1", value=1)
    survey.metadata["validation_errors"] = survey.validate()
    state["survey"] = survey
    _make_study(session_factory)
    _upload(test_client)
    _wait_draft(test_client)

    assert (
        test_client.get("/api/studies/study_q1/questionnaire/questions/Q2").json()["show_if"]
        is not None
    )
    resp = test_client.patch(
        "/api/studies/study_q1/questionnaire/questions/Q2", json={"show_if": None}
    )
    assert resp.status_code == 200
    assert resp.json()["show_if"] is None


def _dup_qid_survey(study_id: str) -> SurveyDefinition:
    """A survey mirroring the real client doc: two questions both labelled S6
    (a genuinely ambiguous screener), plus a Q3 whose show_if references the
    FIRST S6, plus an unrelated Q1. `validate()` reports `duplicate qid: S6`."""
    s6a = Question(qid="S6", text="Screener 6a", type=QuestionType.SINGLE,
                   options=[Option(code=1, label="Yes"), Option(code=2, label="No")])
    s6b = Question(qid="S6", text="Screener 6b", type=QuestionType.SINGLE,
                   options=[Option(code=1, label="Yes"), Option(code=2, label="No")])
    q1 = Question(qid="Q1", text="Unrelated question", type=QuestionType.OPEN_TEXT)
    q3 = Question(
        qid="Q3",
        text="Follow-up",
        type=QuestionType.OPEN_TEXT,
        show_if=Expression(op="eq", qid="S6", value=1),
    )
    survey = SurveyDefinition(
        study_id=study_id,
        title="Dup",
        source="extracted",
        status="draft",
        blocks=[Block(block_id="B1", question_ids=["S6", "S6", "Q1", "Q3"])],
        questions=[s6a, s6b, q1, q3],
    )
    survey.metadata["validation_errors"] = survey.validate()
    survey.metadata["extraction_summary"] = {"total_chunks": 4}
    return survey


def test_patch_unrelated_question_despite_preexisting_duplicate(client):
    """T18 delta validation: a doc with a pre-existing `duplicate qid: S6` problem
    can still be edited elsewhere — the PATCH only rejects NEW problems."""
    test_client, session_factory, state = client
    state["survey"] = _dup_qid_survey("study_q1")
    _make_study(session_factory)
    _upload(test_client)
    _wait_draft(test_client)

    # Sanity: the pre-existing duplicate is really present.
    status = test_client.get("/api/studies/study_q1/questionnaire").json()
    assert "duplicate qid: S6" in status["validation_errors"]

    resp = test_client.patch(
        "/api/studies/study_q1/questionnaire/questions/Q1",
        json={"text": "Unrelated question, edited"},
    )
    assert resp.status_code == 200
    assert resp.json()["text"] == "Unrelated question, edited"


def test_patch_rename_qid_resolves_duplicate(client):
    """Renaming one of the duplicate S6 questions removes the `duplicate qid: S6`
    problem from the document's problem list (T18 qid rename)."""
    test_client, session_factory, state = client
    state["survey"] = _dup_qid_survey("study_q1")
    _make_study(session_factory)
    _upload(test_client)
    _wait_draft(test_client)

    resp = test_client.patch(
        "/api/studies/study_q1/questionnaire/questions/S6",
        json={"qid": "S6b"},
    )
    assert resp.status_code == 200
    assert resp.json()["qid"] == "S6b"

    status = test_client.get("/api/studies/study_q1/questionnaire").json()
    assert "duplicate qid: S6" not in status["validation_errors"]

    # Both distinct qids are now addressable.
    assert test_client.get(
        "/api/studies/study_q1/questionnaire/questions/S6b"
    ).status_code == 200
    assert test_client.get(
        "/api/studies/study_q1/questionnaire/questions/S6"
    ).status_code == 200


def test_patch_rename_qid_rewrites_referencing_show_if(client):
    """Renaming a qid referenced by another question's show_if rewrites that
    reference — no dangling pointer to the old qid (T18)."""
    test_client, session_factory, state = client
    # Use a survey where the referenced qid is unique so the rename is clean.
    q1 = Question(qid="Q1", text="Age", type=QuestionType.NUMERIC)
    q2 = Question(
        qid="Q2", text="Follow-up", type=QuestionType.OPEN_TEXT,
        show_if=Expression(op="eq", qid="Q1", value=1),
    )
    survey = SurveyDefinition(
        study_id="study_q1", title="Ref", source="extracted", status="draft",
        blocks=[Block(block_id="B1", question_ids=["Q1", "Q2"])],
        questions=[q1, q2],
    )
    survey.metadata["validation_errors"] = survey.validate()
    survey.metadata["extraction_summary"] = {}
    state["survey"] = survey
    _make_study(session_factory)
    _upload(test_client)
    _wait_draft(test_client)

    resp = test_client.patch(
        "/api/studies/study_q1/questionnaire/questions/Q1",
        json={"qid": "AGE"},
    )
    assert resp.status_code == 200

    q2 = test_client.get("/api/studies/study_q1/questionnaire/questions/Q2").json()
    assert q2["show_if"]["qid"] == "AGE"


def test_patch_rename_qid_rejects_collision(client):
    """A rename onto an already-used distinct qid is rejected (would merge two
    questions under one qid)."""
    test_client, session_factory, state = client
    _make_study(session_factory)
    _upload(test_client)
    _wait_draft(test_client)

    resp = test_client.patch(
        "/api/studies/study_q1/questionnaire/questions/Q1",
        json={"qid": "Q2"},  # Q2 already exists and is a different question
    )
    assert resp.status_code == 422
    assert "already in use" in resp.json()["detail"]["error"]


def test_stale_processing_reported_as_error(client):
    """A questionnaire stuck on 'processing' with an old updated_at is reported
    as 'error' by the status endpoint (T18 staleness guard)."""
    from api.db import QuestionnaireDB

    test_client, session_factory, _ = client
    _make_study(session_factory)

    old = (datetime.now() - timedelta(minutes=30)).isoformat()
    db = session_factory()
    db.add(
        QuestionnaireDB(
            id="quest_stale",
            study_id="study_q1",
            status="processing",
            source_filename="stuck.docx",
            version=1,
            definition_json=None,
            definition_path=None,
            approved_qids=[],
            created_at=old,
            updated_at=old,
        )
    )
    db.commit()
    db.close()

    body = test_client.get("/api/studies/study_q1/questionnaire").json()
    assert body["status"] == "error"
    assert body["error_detail"] is not None
    assert "did not complete" in body["error_detail"]


def test_no_questionnaire_404(client):
    test_client, session_factory, _ = client
    _make_study(session_factory)
    assert test_client.get("/api/studies/study_q1/questionnaire").status_code == 404


def _wait_error(test_client, study_id="study_q1"):
    """Poll status until it leaves 'processing'; return the status body."""
    import time

    for _ in range(50):
        body = test_client.get(f"/api/studies/{study_id}/questionnaire").json()
        if body["status"] != "processing":
            return body
        time.sleep(0.02)
    raise AssertionError("questionnaire never left 'processing'")


def test_extraction_failure_surfaces_error_detail(client, monkeypatch):
    """T16: when extraction raises (e.g. circuit-broken on a missing key), the
    row flips to 'error' and the status endpoint reports the root-cause detail —
    not a bare 'error'."""
    from src.ai.router import BackendUnavailable

    test_client, session_factory, _ = client
    _make_study(session_factory)

    def boom(chunks, study_id):
        raise BackendUnavailable(
            "extraction aborted after 3 consecutive backend failures "
            "(circuit breaker): ANTHROPIC_API_KEY not set",
            config_error=True,
        )

    monkeypatch.setattr(qroutes, "extract_survey_definition", boom)

    _upload(test_client)
    status = _wait_error(test_client)
    assert status["status"] == "error"
    assert status["error_detail"] is not None
    assert "ANTHROPIC_API_KEY not set" in status["error_detail"]


def test_health_reports_llm_key_presence_booleans(client, monkeypatch):
    """T16 preflight: /api/health reports each LLM key as a presence boolean —
    never the value, length, or prefix."""
    test_client, _, _ = client
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-super-secret-value-1234567890")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    body = test_client.get("/api/health").json()
    keys = body["llm_keys"]
    assert keys["ANTHROPIC_API_KEY"] is True
    assert keys["GROQ_API_KEY"] is False
    # The actual secret must never leak into the payload (value/length/prefix).
    serialized = str(body)
    assert "sk-super-secret" not in serialized
    assert "super-secret-value" not in serialized


def test_backfill_definition_from_file(client, tmp_path):
    """Backward-compat migration (T17): a row with definition_path pointing at a
    still-present file but an empty definition_json column is backfilled from the
    file on read — GET returns the survey AND the column is now populated."""
    from api.db import QuestionnaireDB

    test_client, session_factory, _ = client
    _make_study(session_factory)

    # Simulate a pre-T17 row: JSON only on disk, column empty (as after a fresh
    # deploy but before /app/studies is wiped).
    survey = _sample_survey("study_q1")
    def_file = tmp_path / "study_q1" / "questionnaires" / "quest_legacy.json"
    def_file.parent.mkdir(parents=True, exist_ok=True)
    def_file.write_text(survey.to_json())

    db = session_factory()
    db.add(
        QuestionnaireDB(
            id="quest_legacy",
            study_id="study_q1",
            status="draft",
            source_filename="legacy.docx",
            version=1,
            definition_json=None,  # empty column — must be backfilled
            definition_path=str(def_file),
            approved_qids=[],
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
        )
    )
    db.commit()
    db.close()

    # GET reads the definition (via the file), returns correct data.
    resp = test_client.get("/api/studies/study_q1/questionnaire/questions")
    assert resp.status_code == 200
    qids = {q["qid"] for q in resp.json()["questions"]}
    assert qids == {"Q1", "Q2"}

    # The column was backfilled on that read — it is now populated.
    db = session_factory()
    row = db.query(QuestionnaireDB).filter(QuestionnaireDB.id == "quest_legacy").first()
    assert row.definition_json
    assert SurveyDefinition.from_json(row.definition_json)["Q1"].qid == "Q1"
    db.close()

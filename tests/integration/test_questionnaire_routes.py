"""Questionnaire ingestion + review endpoints.

TestClient against the real app with get_db / get_current_user overridden to a
temp sqlite session and a fake user (same pattern as test_studies_profile.py).
The extraction pipeline (load_docx/load_pdf -> segment -> extract) is patched so
tests make no real LLM calls and need no real .docx/.pdf files: upload's
background thread calls a stubbed `extract_survey_definition` that returns a
hand-built SurveyDefinition, and the loader/segmenter are patched to no-ops so
the real file-parsing path is never entered.
"""

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

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
        # **kwargs absorbs rules_for_profile (the correction loop's learned-rule
        # provider) so this double doesn't need editing again the next time the
        # extractor gains an optional argument.
        lambda chunks, study_id, blocks=None, **kwargs: state["survey"],
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


# Extraction runs in a background thread, so these helpers wait on wall clock.
# The budget was 50 x 0.02s = 1 second, which is ample on an idle machine and
# not ample under a loaded full-suite run — the tests passed alone and failed
# intermittently in CI-style runs. 10 seconds costs nothing when the job
# finishes (the loop exits on the first poll) and removes the flake.
_WAIT_ATTEMPTS = 200
_WAIT_INTERVAL = 0.05


def _wait_draft(test_client, study_id="study_q1"):
    import time

    for _ in range(_WAIT_ATTEMPTS):
        body = test_client.get(f"/api/studies/{study_id}/questionnaire").json()
        if body["status"] != "processing":
            return body
        time.sleep(_WAIT_INTERVAL)
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


def test_patch_constant_sum_round_trips(client):
    """Editing a plain numeric into "adds up to" (constant sum) by hand: the
    UI sends type=numeric + rows + validation.sum, and what comes back — and
    what compiles — must be the real constant-sum shape (multipletext + a sum
    validator), not a display-only convention."""
    test_client, session_factory, _ = client
    _make_study(session_factory)
    _upload(test_client)
    _wait_draft(test_client)

    resp = test_client.patch(
        "/api/studies/study_q1/questionnaire/questions/Q1",
        json={
            "type": "numeric",
            "text": "Split 100% across these",
            "rows": [
                {"code": 1, "label": "Brands I already trust"},
                {"code": 2, "label": "Brands new to me"},
            ],
            "validation": {"min": 0, "max": 100, "sum": 100},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "numeric"
    assert [r["code"] for r in body["rows"]] == [1, 2]
    assert body["validation"]["sum"] == 100

    # Persisted, not just echoed.
    detail = test_client.get("/api/studies/study_q1/questionnaire/questions/Q1").json()
    assert detail["rows"] is not None
    assert detail["validation"] == {"min": 0, "max": 100, "sum": 100}

    # Compiles to SurveyJS as a real constant-sum: one box per row, plus the
    # expression validator that enforces the total.
    from src.core.survey_definition import SurveyDefinition
    from src.renderers.surveyjs_compiler import compile_to_surveyjs

    survey = SurveyDefinition.from_json(_fetch_definition_json(session_factory, "study_q1"))
    compiled = compile_to_surveyjs(survey)
    element = next(e for p in compiled["pages"] for e in p["elements"] if e["name"] == "Q1")
    assert element["type"] == "multipletext"
    assert [item["name"] for item in element["items"]] == ["1", "2"]
    assert element["validators"][0]["expression"] == "{Q1.1} + {Q1.2} = 100"


def test_patch_plain_numeric_edit_leaves_rows_none(client):
    """A numeric question WITHOUT rows stays a plain number — editing its text
    (or its range) must not invent a constant-sum shape nobody asked for."""
    test_client, session_factory, _ = client
    _make_study(session_factory)
    _upload(test_client)
    _wait_draft(test_client)

    resp = test_client.patch(
        "/api/studies/study_q1/questionnaire/questions/Q1",
        json={"validation": {"min": 0, "max": 120}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["rows"] is None
    assert body["validation"] == {"min": 0, "max": 120}


def _fetch_definition_json(session_factory, study_id: str) -> str:
    from api.db import QuestionnaireDB

    db = session_factory()
    try:
        row = (
            db.query(QuestionnaireDB)
            .filter(QuestionnaireDB.study_id == study_id)
            .order_by(QuestionnaireDB.created_at.desc())
            .first()
        )
        return row.definition_json
    finally:
        db.close()


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


def test_upload_auto_approves_clean_questions(client):
    """Exception-based review: the extractor's clean set (metadata
    auto_approved_qids) lands in approved_qids at draft time. Status stays
    'draft'; approve-all then complains only about the flagged exception."""
    test_client, session_factory, state = client
    _make_study(session_factory)

    q1 = Question(qid="Q1", text="Age?", type=QuestionType.NUMERIC,
                  source_text="Q1", source_location="p1")
    q2 = Question(qid="Q2", text="Gender?", type=QuestionType.SINGLE,
                  options=[Option(code=1, label="M"), Option(code=2, label="F")],
                  source_text="Q2", source_location="p2")
    q3 = Question(qid="Q3", text="Brands?", type=QuestionType.MULTI,
                  options=[Option(code=1, label="A")],
                  source_text="Q3", source_location="p3",
                  flags=["AMBIGUOUS_ROUTING"])
    survey = SurveyDefinition(
        study_id="study_q1", title="S", source="extracted", status="draft",
        blocks=[Block(block_id="B1", question_ids=["Q1", "Q2", "Q3"])],
        questions=[q1, q2, q3],
    )
    survey.metadata["validation_errors"] = survey.validate()
    survey.metadata["auto_approved_qids"] = ["Q1", "Q2"]
    state["survey"] = survey

    _upload(test_client)
    status = _wait_draft(test_client)
    assert status["status"] == "draft"  # auto-approval does NOT complete the review
    assert status["n_approved"] == 2

    questions = test_client.get(
        "/api/studies/study_q1/questionnaire/questions"
    ).json()["questions"]
    assert {q["qid"]: q["approved"] for q in questions} == {
        "Q1": True, "Q2": True, "Q3": False,
    }

    # approve-all now complains only about the still-flagged Q3.
    resp = test_client.post("/api/studies/study_q1/questionnaire/approve-all")
    assert resp.status_code == 422
    assert {u["qid"] for u in resp.json()["detail"]["unresolved"]} == {"Q3"}


def test_approve_clean_bulk_approves_only_unflagged(client):
    """approve-clean bulk-approves every question with no blocking flag and
    leaves flagged ones untouched (still requiring a deliberate individual
    approval), so it can't be used to bypass the approve-all gate."""
    test_client, session_factory, state = client
    # Q2 carries AMBIGUOUS_ROUTING; Q1 is clean.
    state["survey"] = _sample_survey("study_q1", with_flags=True)
    _make_study(session_factory)
    _upload(test_client)
    _wait_draft(test_client)

    resp = test_client.post(
        "/api/studies/study_q1/questionnaire/questions/approve-clean"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["newly_approved"] == ["Q1"]  # only the unflagged one
    assert {b["qid"] for b in body["still_blocked"]} == {"Q2"}

    # Q2's blocking flag is NOT cleared — the gate still rejects it.
    q2 = test_client.get("/api/studies/study_q1/questionnaire/questions/Q2").json()
    assert "AMBIGUOUS_ROUTING" in q2["flags"]
    gate = test_client.post("/api/studies/study_q1/questionnaire/approve-all")
    assert gate.status_code == 422
    assert {u["qid"] for u in gate.json()["detail"]["unresolved"]} == {"Q2"}

    # Hand-approving Q2 clears its flag; now the gate passes.
    test_client.post("/api/studies/study_q1/questionnaire/questions/Q2/approve")
    assert (
        test_client.post("/api/studies/study_q1/questionnaire/approve-all").status_code
        == 200
    )


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

    for _ in range(_WAIT_ATTEMPTS):
        body = test_client.get(f"/api/studies/{study_id}/questionnaire").json()
        if body["status"] != "processing":
            return body
        time.sleep(_WAIT_INTERVAL)
    raise AssertionError("questionnaire never left 'processing'")


def test_extraction_failure_surfaces_error_detail(client, monkeypatch):
    """T16: when extraction raises (e.g. circuit-broken on a missing key), the
    row flips to 'error' and the status endpoint reports the root-cause detail —
    not a bare 'error'."""
    from src.ai.router import BackendUnavailable

    test_client, session_factory, _ = client
    _make_study(session_factory)

    def boom(chunks, study_id, blocks=None, **kwargs):
        raise BackendUnavailable(
            "extraction aborted after 3 consecutive backend failures "
            "(circuit breaker): GROQ_API_KEY not set",
            config_error=True,
        )

    monkeypatch.setattr(qroutes, "extract_survey_definition", boom)

    _upload(test_client)
    status = _wait_error(test_client)
    assert status["status"] == "error"
    assert status["error_detail"] is not None
    # The surfaced text is written for the RESEARCHER who uploaded the file, not
    # for whoever runs the deployment: a missing key is their administrator's
    # problem, and an env var name on an upload screen tells them nothing they
    # can act on. The raw cause is still logged server-side.
    detail = status["error_detail"]
    assert "administrator" in detail
    assert "GROQ_API_KEY" not in detail


def test_health_reports_llm_key_presence_booleans(client, monkeypatch):
    """T16 preflight: /api/health reports each LLM key as a presence boolean —
    never the value, length, or prefix."""
    test_client, _, _ = client
    monkeypatch.setenv("CEREBRAS_API_KEY", "sk-super-secret-value-1234567890")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    body = test_client.get("/api/health").json()
    keys = body["llm_keys"]
    assert keys["CEREBRAS_API_KEY"] is True
    assert keys["GROQ_API_KEY"] is False
    # The actual secret must never leak into the payload (value/length/prefix).
    serialized = str(body)
    assert "sk-super-secret" not in serialized
    assert "super-secret-value" not in serialized


# --- T12: authored survey builder -------------------------------------------


def _create_authored(test_client, study_id="study_q1", title="From scratch"):
    return test_client.post(
        f"/api/studies/{study_id}/questionnaire/authored", json={"title": title}
    )


def test_create_authored_happy_path(client):
    test_client, session_factory, _ = client
    _make_study(session_factory)

    resp = _create_authored(test_client)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "draft"
    assert body["title"] == "From scratch"
    assert body["n_questions"] == 0

    # The blank authored survey is immediately reachable via the normal endpoints.
    qs = test_client.get("/api/studies/study_q1/questionnaire/questions")
    assert qs.status_code == 200
    assert qs.json()["questions"] == []


def test_create_authored_409_when_exists(client):
    test_client, session_factory, _ = client
    _make_study(session_factory)
    _create_authored(test_client)
    resp = _create_authored(test_client)
    assert resp.status_code == 409


def test_create_authored_404_unknown_study(client):
    test_client, _, _ = client
    assert _create_authored(test_client, study_id="nope").status_code == 404


def test_create_authored_reviewer_403(client):
    test_client, session_factory, state = client
    _make_study(session_factory)
    state["role"] = "reviewer"
    assert _create_authored(test_client).status_code == 403


def test_settings_theme_persists_to_metadata(client):
    """PATCH settings writes metadata.theme; the status echoes it back."""
    test_client, session_factory, _ = client
    _make_study(session_factory)
    _create_authored(test_client)

    resp = test_client.patch(
        "/api/studies/study_q1/questionnaire/settings", json={"theme": "telecom"}
    )
    assert resp.status_code == 200
    assert resp.json()["theme"] == "telecom"
    # Durable: a fresh status read still reports it.
    assert test_client.get("/api/studies/study_q1/questionnaire").json()["theme"] == "telecom"


def test_settings_theme_unknown_slug_422(client):
    test_client, session_factory, _ = client
    _make_study(session_factory)
    _create_authored(test_client)
    resp = test_client.patch(
        "/api/studies/study_q1/questionnaire/settings", json={"theme": "neon"}
    )
    assert resp.status_code == 422


def test_settings_theme_null_clears(client):
    test_client, session_factory, _ = client
    _make_study(session_factory)
    _create_authored(test_client)
    test_client.patch("/api/studies/study_q1/questionnaire/settings", json={"theme": "bfsi"})
    cleared = test_client.patch(
        "/api/studies/study_q1/questionnaire/settings", json={"theme": None}
    )
    assert cleared.status_code == 200
    assert cleared.json()["theme"] is None


def test_settings_theme_reviewer_403(client):
    test_client, session_factory, state = client
    _make_study(session_factory)
    _create_authored(test_client)
    state["role"] = "reviewer"
    resp = test_client.patch(
        "/api/studies/study_q1/questionnaire/settings", json={"theme": "retail"}
    )
    assert resp.status_code == 403


def _add_question(test_client, study_id="study_q1", **fields):
    return test_client.post(
        f"/api/studies/{study_id}/questionnaire/questions", json=fields
    )


def test_add_question_appends(client):
    test_client, session_factory, _ = client
    _make_study(session_factory)
    _create_authored(test_client)

    resp = _add_question(test_client, qid="Q1", text="Age", type="numeric")
    assert resp.status_code == 200
    assert resp.json()["qid"] == "Q1"

    qids = [q["qid"] for q in test_client.get(
        "/api/studies/study_q1/questionnaire/questions"
    ).json()["questions"]]
    assert qids == ["Q1"]


def test_add_question_rejects_unknown_routing_ref(client):
    """A new question whose show_if references a nonexistent qid is a NEW
    validation problem — rejected 422."""
    test_client, session_factory, _ = client
    _make_study(session_factory)
    _create_authored(test_client)

    resp = _add_question(
        test_client,
        qid="Q1",
        text="Follow-up",
        type="single",
        options=[{"code": 1, "label": "Yes"}],
        show_if={"op": "eq", "qid": "NOPE", "value": 1},
    )
    assert resp.status_code == 422
    assert "problems" in resp.json()["detail"]


def test_delete_question_dangling_reference_422(client):
    test_client, session_factory, _ = client
    _make_study(session_factory)
    _create_authored(test_client)
    _add_question(test_client, qid="Q1", text="Age", type="numeric")
    _add_question(
        test_client,
        qid="Q2",
        text="Follow-up",
        type="single",
        options=[{"code": 1, "label": "Yes"}],
        show_if={"op": "gt", "qid": "Q1", "value": 17},
    )

    resp = test_client.delete("/api/studies/study_q1/questionnaire/questions/Q1")
    assert resp.status_code == 422
    assert "Q2" in resp.json()["detail"]["referenced_by"]

    # Deleting the referencer first, then Q1, works.
    assert test_client.delete(
        "/api/studies/study_q1/questionnaire/questions/Q2"
    ).status_code == 200
    assert test_client.delete(
        "/api/studies/study_q1/questionnaire/questions/Q1"
    ).status_code == 200


def test_reorder_validates_permutation(client):
    test_client, session_factory, _ = client
    _make_study(session_factory)
    _create_authored(test_client)
    _add_question(test_client, qid="Q1", text="One", type="open_text")
    _add_question(test_client, qid="Q2", text="Two", type="open_text")

    # Not a permutation -> 422.
    bad = test_client.put(
        "/api/studies/study_q1/questionnaire/questions/order", json={"qids": ["Q1"]}
    )
    assert bad.status_code == 422

    ok = test_client.put(
        "/api/studies/study_q1/questionnaire/questions/order",
        json={"qids": ["Q2", "Q1"]},
    )
    assert ok.status_code == 200
    qids = [q["qid"] for q in test_client.get(
        "/api/studies/study_q1/questionnaire/questions"
    ).json()["questions"]]
    assert qids == ["Q2", "Q1"]


def test_authored_survey_reaches_fielding(client):
    """The key T12 unification test: a survey BUILT from scratch flows through
    the SAME approve -> fielding path an extracted one does. Create authored ->
    add 2 questions (one with terminate_if) -> approve each -> approve-all ->
    open a fielding link."""
    test_client, session_factory, _ = client
    _make_study(session_factory)
    _create_authored(test_client, title="Fieldable")

    assert _add_question(
        test_client, qid="Q1", text="Age", type="numeric"
    ).status_code == 200
    # Q2 carries a terminate_if referencing the prior Q1.
    assert _add_question(
        test_client,
        qid="Q2",
        text="Interested?",
        type="single",
        options=[{"code": 1, "label": "Yes"}, {"code": 2, "label": "No"}],
        terminate_if={"op": "lt", "qid": "Q1", "value": 18},
    ).status_code == 200

    for qid in ("Q1", "Q2"):
        assert test_client.post(
            f"/api/studies/study_q1/questionnaire/questions/{qid}/approve"
        ).status_code == 200

    approved = test_client.post("/api/studies/study_q1/questionnaire/approve-all")
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"

    # Same fielding path as an extracted survey — proves the unification.
    link = test_client.post("/api/studies/study_q1/fielding/links")
    assert link.status_code == 200
    assert link.json()["token"]
    assert link.json()["status"] == "open"


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


# --- the document itself, not only what we extracted from it -----------------


def test_uploaded_questionnaire_can_be_downloaded_back(client) -> None:
    """The .docx a client sent must outlive the restart that wipes the disk.

    `definition_json` keeps the EXTRACTED questionnaire, which is what the tool
    works from — but "which questionnaire is this wave running?" is answered by
    the document, and a reviewer comparing our reading against the source needs
    the file, not its name.
    """
    test_client, SessionLocal, _ = client
    _make_study(SessionLocal)
    _upload(test_client, filename="Wave 2 QRE.docx")
    _wait_draft(test_client)

    resp = test_client.get("/api/studies/study_q1/questionnaire/source")

    assert resp.status_code == 200
    assert resp.content == b"dummy-bytes"
    assert "Wave 2 QRE.docx" in resp.headers["content-disposition"]


def test_download_survives_the_on_disk_copy_being_deleted(client, tmp_path) -> None:
    """Served from the database row, so a wiped disk changes nothing."""
    import shutil

    test_client, SessionLocal, _ = client
    _make_study(SessionLocal)
    _upload(test_client)
    _wait_draft(test_client)

    shutil.rmtree(tmp_path / "study_q1", ignore_errors=True)

    resp = test_client.get("/api/studies/study_q1/questionnaire/source")
    assert resp.status_code == 200
    assert resp.content == b"dummy-bytes"


def test_download_says_what_to_do_when_nothing_was_archived(client) -> None:
    test_client, SessionLocal, _ = client
    _make_study(SessionLocal)

    resp = test_client.get("/api/studies/study_q1/questionnaire/source")
    assert resp.status_code == 404
    assert "no questionnaire" in resp.json()["detail"].lower()


def test_the_wave_is_identified_by_filename_and_version(client) -> None:
    """What the status endpoint already carries, pinned: this is wave tracking."""
    test_client, SessionLocal, _ = client
    _make_study(SessionLocal)
    _upload(test_client, filename="Wave 2 QRE.docx")
    body = _wait_draft(test_client)

    assert body["source_filename"] == "Wave 2 QRE.docx"
    assert body["version"] == 1
    assert body["created_at"]


# --- reading the document beside the extraction ------------------------------


def _real_docx_bytes() -> bytes:
    """A genuine .docx, since the renderer actually opens the file."""
    import io

    from docx import Document

    doc = Document()
    doc.add_paragraph("Q1. What is your age?")
    struck = doc.add_paragraph()
    run = struck.add_run("Q2. Which pharmacy did you use last?")
    run.font.strike = True
    doc.add_paragraph("Q3. How satisfied were you?")
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def test_the_document_can_be_read_beside_what_we_extracted(client) -> None:
    """A reviewer comparing our reading to the source needs to SEE the source.

    Colour and strikethrough are carrying information the parser drops on
    purpose — a struck-out question is this wave's edit, and a highlight after
    an option label is how these documents point at a hidden variable.
    """
    test_client, SessionLocal, _ = client
    _make_study(SessionLocal)
    test_client.post(
        "/api/studies/study_q1/questionnaire/upload",
        files={"file": ("Wave 2 QRE.docx", _real_docx_bytes(), "application/octet-stream")},
    )
    _wait_draft(test_client)

    resp = test_client.get("/api/studies/study_q1/questionnaire/source/rendered")

    assert resp.status_code == 200
    body = resp.json()
    assert body["filename"] == "Wave 2 QRE.docx"
    assert body["n_blocks"] >= 2
    # The struck-out question is SHOWN — that is what a reviewer is looking for.
    assert "Q2. Which pharmacy did you use last?" in body["html"]
    assert 'id="b0"' in body["html"]


def test_reading_a_pdf_side_by_side_says_so_rather_than_failing(client) -> None:
    test_client, SessionLocal, _ = client
    _make_study(SessionLocal)
    _upload(test_client, filename="screener.pdf")
    _wait_draft(test_client)

    resp = test_client.get("/api/studies/study_q1/questionnaire/source/rendered")
    assert resp.status_code == 415
    assert "pdf" in resp.json()["detail"].lower()


def test_a_document_that_cannot_be_opened_says_which_it_is(client) -> None:
    """The bytes are not a Word file. That is a fact about the file."""
    test_client, SessionLocal, _ = client
    _make_study(SessionLocal)
    _upload(test_client)  # uploads b"dummy-bytes"
    _wait_draft(test_client)

    resp = test_client.get("/api/studies/study_q1/questionnaire/source/rendered")
    assert resp.status_code == 422
    assert "could not be opened" in resp.json()["detail"].lower()


def test_reading_says_what_to_do_when_nothing_was_archived(client) -> None:
    test_client, SessionLocal, _ = client
    _make_study(SessionLocal)

    resp = test_client.get("/api/studies/study_q1/questionnaire/source/rendered")
    assert resp.status_code == 404


def test_diagnosis_reads_the_document_again_and_names_the_gap(client):
    """The reviewer's question is "why is there one question here?".

    The answer has to come from reading the SAME document with today's parser,
    because a study can be broken in a way that is already fixed and nothing in
    the stored definition can say so.
    """
    test_client, session_factory, state = client
    _make_study(session_factory)
    _upload(test_client)
    _wait_draft(test_client)

    # The document the study was extracted from, as the parser reads it TODAY:
    # four questions, where the stored fixture has two.
    from src.intake.questionnaire_loader import (
        DocumentBlock,
    )
    from src.intake.questionnaire_loader import (
        segment_into_questions as real_segment,
    )

    blocks = [
        DocumentBlock("paragraph", f"Q{i}. Question {i}?", i) for i in range(1, 5)
    ]
    # The fixture stubs the loader out for upload; the diagnosis has to use the
    # REAL segmenter, since re-segmenting is the whole mechanism under test.
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(qroutes, "load_docx", lambda path: blocks)
        mp.setattr(qroutes, "segment_into_questions", real_segment)
        body = test_client.get("/api/studies/study_q1/questionnaire/diagnosis").json()

    assert body["questions_stored"] == 2
    assert body["questions_the_parser_finds_now"] == 4
    assert "numbered" in body["document_convention"]
    assert any("Re-extracting would pick up" in f for f in body["findings"])
    assert body["can_reextract"] is True


def test_diagnosis_works_before_anything_is_approved(client):
    """A study is broken here long before anybody approves it.

    Requiring an approved questionnaire would make the check unavailable
    exactly when it is needed.
    """
    test_client, session_factory, state = client
    _make_study(session_factory)
    _upload(test_client)
    body = _wait_draft(test_client)
    assert body["status"] != "approved"

    resp = test_client.get("/api/studies/study_q1/questionnaire/diagnosis")
    assert resp.status_code == 200
    assert resp.json()["questions_stored"] == 2


def test_reextract_keeps_the_version_it_supersedes(client):
    """Re-extraction must never destroy a review somebody spent hours on.

    It is the only way a parser fix reaches a study already in the database, so
    it has to be safe enough to run on a study under review: a NEW version, with
    the old definition and its per-question approvals still there.
    """
    from api.db import QuestionnaireDB

    test_client, session_factory, state = client
    _make_study(session_factory)
    _upload(test_client)
    _wait_draft(test_client)
    test_client.post("/api/studies/study_q1/questionnaire/questions/Q1/approve")

    db = session_factory()
    before = db.query(QuestionnaireDB).filter_by(study_id="study_q1").one()
    before_id, before_approved = before.id, list(before.approved_qids or [])
    db.close()
    assert before_approved == ["Q1"]

    resp = test_client.post("/api/studies/study_q1/questionnaire/reextract")
    assert resp.status_code == 200
    assert resp.json()["superseded"] == before_id
    assert resp.json()["version"] == 2
    _wait_draft(test_client)

    db = session_factory()
    kept = db.query(QuestionnaireDB).filter_by(id=before_id).one()
    rows = db.query(QuestionnaireDB).filter_by(study_id="study_q1").all()
    db.close()
    assert list(kept.approved_qids or []) == before_approved
    assert kept.definition_json  # the superseded definition is still readable
    assert len(rows) == 2


def test_reextract_needs_the_archived_document(client):
    """Without the document there is nothing to read again — say that plainly."""
    from api.db import QuestionnaireDB

    test_client, session_factory, state = client
    _make_study(session_factory)
    _upload(test_client)
    _wait_draft(test_client)

    db = session_factory()
    row = db.query(QuestionnaireDB).filter_by(study_id="study_q1").one()
    row.source_file_b64 = None
    db.commit()
    db.close()

    resp = test_client.post("/api/studies/study_q1/questionnaire/reextract")
    assert resp.status_code == 404
    assert "uploaded again" in resp.json()["detail"].lower() or "upload it again" in (
        resp.json()["detail"].lower()
    )


def test_the_questionnaire_downloads_as_a_workbook(client):
    """Everything in it is already known; what it could not do is leave the
    screen. Built from the stored definition and the same audit that gates
    fielding — two readings of one questionnaire that disagree would be worse
    than one reading."""
    import io

    from openpyxl import load_workbook

    test_client, session_factory, state = client
    _make_study(session_factory)
    _upload(test_client)
    _wait_draft(test_client)

    resp = test_client.get("/api/studies/study_q1/questionnaire/workbook")
    assert resp.status_code == 200
    assert "spreadsheetml" in resp.headers["content-type"]
    assert "survey_questionnaire.xlsx" in resp.headers["content-disposition"]

    workbook = load_workbook(io.BytesIO(resp.content))
    assert workbook.sheetnames == [
        "Summary", "Questions", "Answers", "Routing", "Checks", "Flow",
    ]
    qids = [r[1] for r in workbook["Questions"].iter_rows(min_row=2, values_only=True)]
    assert qids == ["Q1", "Q2"]
    # The checks travel with it: a workbook showing the instrument but not what
    # is wrong with it is the more dangerous half of the picture.
    assert workbook["Checks"].max_row > 1


def test_the_app_shell_is_never_cached(client):
    """Vite content-hashes the bundle, so a new build is a new filename — and
    the only thing that NAMES that filename is index.html.

    Served without a cache-control header it was cached heuristically, so a
    browser kept loading the previous bundle and a deploy simply did not
    arrive: an auto-advance fix shipped, was verified in the served bundle,
    and the person testing it still had the old one. Worse for a respondent
    mid-interview, who has no reason to reload anything.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.main import mount_web

    dist = Path(tempfile.mkdtemp())
    (dist / "assets").mkdir()
    (dist / "index.html").write_text("<html><body>app</body></html>")
    (dist / "assets" / "index-abc123.js").write_text("console.log(1)")

    app = FastAPI()
    mount_web(app, dist)
    with TestClient(app) as web:
        shell = web.get("/projects/x/fielding")
        assert shell.status_code == 200
        assert "no-cache" in shell.headers.get("cache-control", "")

        # A hashed asset is safe to cache — its name changes when it does.
        asset = web.get("/assets/index-abc123.js")
        assert asset.status_code == 200
        assert "no-cache" not in asset.headers.get("cache-control", "")


def test_approve_all_refuses_a_definition_short_of_the_document(client):
    """Every question present is approved and clean — and the document marked
    forty. Per-question checks cannot see what never became a question."""
    test_client, session_factory, state = client
    state["survey"].metadata["document_markers"] = {
        "expected_questions": 40, "document_chars": 10_000,
    }
    _make_study(session_factory)
    _upload(test_client)
    _wait_draft(test_client)
    for qid in ("Q1", "Q2"):
        test_client.post(f"/api/studies/study_q1/questionnaire/questions/{qid}/approve")

    resp = test_client.post("/api/studies/study_q1/questionnaire/approve-all")
    assert resp.status_code == 422
    first = resp.json()["detail"]["unresolved"][0]
    assert first["qid"] == "Document"
    assert "marks 40 questions" in first["reasons"][0]
    # The reviewer sees it before ever pressing approve.
    issues = test_client.get("/api/studies/study_q1/questionnaire").json()["validation_errors"]
    assert "marks 40 questions" in issues[0]


def _with_unanswerable_q2(state):
    """Q2 as the TRD study shipped it: a choice question with no answers."""
    q2 = next(q for q in state["survey"].questions if q.qid == "Q2")
    q2.options = []
    q2.flags = ["EMPTY_OPTIONS"]


def test_a_question_with_nothing_to_answer_cannot_be_approved(client):
    test_client, session_factory, state = client
    _with_unanswerable_q2(state)
    _make_study(session_factory)
    _upload(test_client)
    _wait_draft(test_client)

    resp = test_client.post("/api/studies/study_q1/questionnaire/questions/Q2/approve")
    assert resp.status_code == 422
    assert "nothing to answer" in resp.json()["detail"]


def test_approve_clean_leaves_every_flagged_question_for_a_person(client):
    """A non-blocking flag is still the system asking someone to look. Bulk
    approval took "Empty options" as clean and a question with no answers
    reached a test link."""
    test_client, session_factory, state = client
    _with_unanswerable_q2(state)
    _make_study(session_factory)
    _upload(test_client)
    _wait_draft(test_client)

    body = test_client.post("/api/studies/study_q1/questionnaire/questions/approve-clean").json()
    held = {b["qid"]: b["flags"] for b in body["still_blocked"]}
    assert "Q2" in held and "NOTHING_TO_ANSWER" in held["Q2"]
    assert body["n_newly_approved"] == 1  # Q1 only


def test_approve_all_refuses_an_unanswerable_question_even_if_approved_before(client):
    """Approvals given before this check existed must not carry it to field."""
    from api.db import set_questionnaire_approved_qids

    test_client, session_factory, state = client
    _with_unanswerable_q2(state)
    _make_study(session_factory)
    _upload(test_client)
    _wait_draft(test_client)
    db = session_factory()
    qid = test_client.get("/api/studies/study_q1/questionnaire").json()["id"]
    set_questionnaire_approved_qids(db, qid, ["Q1", "Q2"])
    db.close()

    resp = test_client.post("/api/studies/study_q1/questionnaire/approve-all")
    assert resp.status_code == 422
    reasons = {u["qid"]: u["reasons"] for u in resp.json()["detail"]["unresolved"]}
    assert any("nothing for a respondent to answer" in r for r in reasons["Q2"])


def test_qa_runs_on_a_stored_study_and_the_record_is_shared(client):
    """The QA loop on demand: it saves its record where the review page and the
    copilot read it, and it never changes a question a person approved."""
    test_client, session_factory, state = client
    q2 = next(q for q in state["survey"].questions if q.qid == "Q2")
    q2.options = [Option(code=1, label="Alpha |"), Option(code=2, label="Beta")]
    _make_study(session_factory)
    _upload(test_client)
    _wait_draft(test_client)

    record = test_client.post("/api/studies/study_q1/questionnaire/qa").json()
    assert record["fixed"] == 1
    assert test_client.get("/api/studies/study_q1/questionnaire").json()["qa"]["fixed"] == 1
    labels = [o["label"] for o in test_client.get(
        "/api/studies/study_q1/questionnaire/questions/Q2").json()["options"]]
    assert labels == ["Alpha", "Beta"]


def test_qa_never_changes_an_approved_question(client):
    test_client, session_factory, state = client
    q2 = next(q for q in state["survey"].questions if q.qid == "Q2")
    q2.options = [Option(code=1, label="Alpha |"), Option(code=2, label="Beta")]
    _make_study(session_factory)
    _upload(test_client)
    _wait_draft(test_client)
    test_client.post("/api/studies/study_q1/questionnaire/questions/Q2/approve")

    record = test_client.post("/api/studies/study_q1/questionnaire/qa").json()
    assert record["fixed"] == 0 and record["needs_you"] == 1
    labels = [o["label"] for o in test_client.get(
        "/api/studies/study_q1/questionnaire/questions/Q2").json()["options"]]
    assert labels == ["Alpha |", "Beta"]


def test_the_question_agent_suggests_and_saves_nothing(client):
    test_client, session_factory, state = client
    q2 = next(q for q in state["survey"].questions if q.qid == "Q2")
    q2.options = [Option(code=1, label="Alpha |"), Option(code=2, label="Beta")]
    _make_study(session_factory)
    _upload(test_client)
    _wait_draft(test_client)

    body = test_client.post(
        "/api/studies/study_q1/questionnaire/questions/Q2/agent", json={}
    ).json()
    assert body["suggestions"] and body["error"] is None
    stored = test_client.get("/api/studies/study_q1/questionnaire/questions/Q2").json()
    assert [o["label"] for o in stored["options"]] == ["Alpha |", "Beta"]


def test_saving_a_question_keeps_every_setting_of_its_answers(client):
    """Saving sent only code and label: a typo fix wiped "None of these"
    being exclusive and "Other" having its text box."""
    test_client, session_factory, _ = client
    _make_study(session_factory)
    _upload(test_client)
    _wait_draft(test_client)
    test_client.patch("/api/studies/study_q1/questionnaire/questions/Q2", json={"options": [
        {"code": 1, "label": "Alpha"},
        {"code": 2, "label": "None of these", "exclusive": True, "anchored": True},
    ]})
    stored = test_client.get("/api/studies/study_q1/questionnaire/questions/Q2").json()
    none = stored["options"][1]
    assert none["exclusive"] is True and none["anchored"] is True


def test_the_preview_is_the_links_own_rendering_of_unsaved_edits(client):
    """Compiled by the link's compiler from what the editor shows, unsaved;
    a condition on earlier answers is shown and said, QA is run live."""
    test_client, session_factory, _ = client
    _make_study(session_factory)
    _upload(test_client)
    _wait_draft(test_client)

    body = test_client.post(
        "/api/studies/study_q1/questionnaire/questions/Q2/preview",
        json={"current": {"text": "Edited wording?", "options": [],
                          "show_if": {"op": "eq", "qid": "Q1", "value": 1}}},
    ).json()
    element = body["survey_json"]["pages"][0]["elements"][0]
    assert element["title"] == "Edited wording?"
    assert "visibleIf" not in element
    assert any("only shows when its condition is met" in n for n in body["notes"])
    assert any("nothing to answer" in p for p in body["problems"])
    stored = test_client.get("/api/studies/study_q1/questionnaire/questions/Q2").json()
    assert stored["text"] == "Which brands do you know?"  # nothing saved


def test_every_variable_says_what_fills_it_and_only_owners_and_analysts_see_it(client):
    """Hidden variables are where the data layout goes wrong unseen: one with no
    rule arrives empty. The list says, column by column, what fills it."""
    from src.core.survey_definition import Block, Expression, Option, Question, QuestionType

    test_client, session_factory, state = client
    d2 = Question(qid="D2", type=QuestionType.NUMERIC, text="People in your home?")
    at_least_2 = Expression(op="not", children=[Expression(op="lt", qid="D2", value=2)])
    with_rule = Question(qid="D2a", type=QuestionType.INFO, text="CODE SIZE OF HOUSEHOLD",
                         flags=["DERIVED_VARIABLE"],
                         options=[Option(1, "1", derive_if=Expression(op="eq", qid="D2", value=1)),
                                  Option(2, "2+", derive_if=at_least_2)])
    no_rule = Question(qid="C7a", type=QuestionType.INFO, text="CODE REGION",
                       flags=["DERIVED_VARIABLE"], options=[Option(1, "West")])
    path = Question(qid="HID_TEEN", type=QuestionType.INFO, text="Teen path",
                    flags=["DERIVED_VARIABLE"],
                    show_if=Expression(op="eq", qid="D2", value=3, verbatim_text="IF D2 = 3"))
    state["survey"] = SurveyDefinition(
        study_id="study_q1", questions=[d2, with_rule, no_rule, path],
        blocks=[Block(block_id="B", question_ids=["D2", "D2a", "C7a", "HID_TEEN"])])
    _make_study(session_factory)
    _upload(test_client)
    _wait_draft(test_client)

    body = test_client.get("/api/studies/study_q1/questionnaire/datamap").json()
    by_name = {v["name"]: v for v in body["variables"]}
    assert by_name["D2"]["filled_by"] == "asked"
    assert by_name["D2a"]["filled_by"] == "computed" and "D2 ≥ 2" in by_name["D2a"]["how"]
    assert by_name["C7a"]["filled_by"] == "not_filled"
    assert by_name["HID_TEEN"]["filled_by"] == "computed" and "D2 = 3" in by_name["HID_TEEN"]["how"]
    assert by_name["vStatus"]["filled_by"] == "recorded"

    state["role"] = "reviewer"
    assert test_client.get("/api/studies/study_q1/questionnaire/datamap").status_code == 403


def test_approving_a_model_read_question_clears_its_mark_and_bulk_approval_skips_it(client):
    from src.core.survey_definition import Block, Option, Question, QuestionType

    test_client, session_factory, state = client
    read_by_model = Question(qid="Q1", type=QuestionType.SINGLE, text="Which pharmacy?",
                             options=[Option(1, "CVS")], flags=["MODEL_READ"])
    clean = Question(qid="Q2", type=QuestionType.SINGLE, text="Age band?",
                     options=[Option(1, "18-34")])
    state["survey"] = SurveyDefinition(study_id="study_q1", questions=[read_by_model, clean],
                                       blocks=[Block(block_id="B", question_ids=["Q1", "Q2"])])
    _make_study(session_factory)
    _upload(test_client)
    _wait_draft(test_client)

    bulk = test_client.post("/api/studies/study_q1/questionnaire/questions/approve-clean").json()
    assert "Q1" not in bulk["newly_approved"]  # a person has to look at it
    r = test_client.post("/api/studies/study_q1/questionnaire/questions/Q1/approve").json()
    assert "MODEL_READ" in r["cleared_flags"]

"""Quota engine + vendor redirect protocol (T10).

TestClient against the real app with get_db / get_current_user overridden to a
temp sqlite session and a fake user (same pattern as test_fielding_routes). The
seeded instrument is the coffee survey: Q1 age (terminate < 18), Q2 single
Yes(1)/No(2), Q3 open. Quota cells are built on Q2 == 1 ("coffee drinkers"),
reusing the SAME Expression AST as routing/terminates (key decision #4).

Enforcement is server-authoritative (key decision #5): all counting, boundary,
overquota and security-30 outcomes are decided by the API, never the client.
"""

from datetime import datetime

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


def _sample_survey(study_id: str) -> SurveyDefinition:
    q1 = Question(
        qid="Q1",
        text="What is your age?",
        type=QuestionType.NUMERIC,
        terminate_if=Expression(op="lt", qid="Q1", value=18),
    )
    q2 = Question(
        qid="Q2",
        text="Do you drink coffee?",
        type=QuestionType.SINGLE,
        options=[Option(code=1, label="Yes"), Option(code=2, label="No")],
    )
    q3 = Question(
        qid="Q3", text="Which brand?", type=QuestionType.OPEN_TEXT, required=False
    )
    return SurveyDefinition(
        study_id=study_id,
        title="Coffee Study",
        source="authored",
        status="approved",
        version=1,
        blocks=[Block(block_id="B1", question_ids=["Q1", "Q2", "Q3"])],
        questions=[q1, q2, q3],
    )


@pytest.fixture
def client(tmp_path):
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

    state = {"role": "owner"}

    def override_get_current_user():
        return UserDB(
            id="u1", email="test@x.com", name="Test", role=state["role"], is_active=True
        )

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    yield TestClient(app), TestSessionLocal, state
    app.dependency_overrides.clear()


def _make_study(sf, study_id="study_q1"):
    db = sf()
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


def _make_questionnaire(sf, study_id="study_q1"):
    survey = _sample_survey(study_id)
    db = sf()
    db.add(
        QuestionnaireDB(
            id="quest_q1",
            study_id=study_id,
            status="approved",
            source_filename="survey.docx",
            version=survey.version,
            definition_json=survey.to_json(),
            definition_path=None,
            approved_qids=[q.qid for q in survey.questions],
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
        )
    )
    db.commit()
    db.close()


COFFEE = Expression(op="eq", qid="Q2", value=1).to_dict()


def _setup(tc, sf, redirects=None):
    """Seed study + questionnaire, open a link, return its token + id."""
    _make_study(sf)
    _make_questionnaire(sf)
    body = tc.post("/api/studies/study_q1/fielding/links", json=redirects or {})
    link = body.json()
    return link["token"], link["id"]


def _quotas_url(link_id):
    return f"/api/studies/study_q1/fielding/links/{link_id}/quotas"


def _add_cell(tc, link_id, condition, limit, name="coffee"):
    return tc.post(
        _quotas_url(link_id),
        json={"cells": [{"name": name, "condition": condition, "limit": limit}]},
    )


def _count(tc, link_id, idx=0):
    return tc.get(_quotas_url(link_id)).json()["cells"][idx]["count"]


def _enter(tc, token, query=""):
    return tc.get(f"/api/r/{token}{query}").json()["session_id"]


def _answer(tc, token, sid, answers):
    return tc.post(f"/api/r/{token}/sessions/{sid}/answers", json={"answers": answers})


def _complete(tc, token, sid):
    return tc.post(f"/api/r/{token}/sessions/{sid}/complete", json={})


# --- CRUD + authz -----------------------------------------------------------


def test_quota_crud_and_reviewer_403(client):
    tc, sf, state = client
    _token, link_id = _setup(tc, sf)

    resp = _add_cell(tc, link_id, COFFEE, 5)
    assert resp.status_code == 200
    cells = resp.json()["cells"]
    assert len(cells) == 1
    assert cells[0]["count"] == 0
    assert cells[0]["limit"] == 5
    assert cells[0]["condition"] == COFFEE
    quota_id = cells[0]["id"]

    state["role"] = "reviewer"
    assert tc.get(_quotas_url(link_id)).status_code == 200  # read allowed
    assert _add_cell(tc, link_id, COFFEE, 1).status_code == 403
    assert (
        tc.patch(f"{_quotas_url(link_id)}/{quota_id}", json={"limit": 9}).status_code
        == 403
    )


# --- Complete-time counting -------------------------------------------------


def test_complete_increments_and_substitutes_redirect(client):
    tc, sf, _ = client
    token, link_id = _setup(
        tc, sf, redirects={"redirect_complete": "https://panel.test/done?pid={pid}"}
    )
    _add_cell(tc, link_id, COFFEE, 5)

    sid = _enter(tc, token, "?pid=P1")
    _answer(tc, token, sid, {"Q2": 1})
    resp = _complete(tc, token, sid).json()

    assert resp["status_code"] == 10
    assert resp["redirect_url"] == "https://panel.test/done?pid=P1"
    assert _count(tc, link_id) == 1


def test_non_matching_complete_does_not_count(client):
    tc, sf, _ = client
    token, link_id = _setup(tc, sf)
    _add_cell(tc, link_id, COFFEE, 5)

    sid = _enter(tc, token)
    _answer(tc, token, sid, {"Q2": 2})  # not a coffee drinker
    assert _complete(tc, token, sid).json()["status_code"] == 10
    assert _count(tc, link_id) == 0


def test_recomplete_does_not_double_increment(client):
    tc, sf, _ = client
    token, link_id = _setup(tc, sf)
    _add_cell(tc, link_id, COFFEE, 5)

    sid = _enter(tc, token)
    _answer(tc, token, sid, {"Q2": 1})
    assert _complete(tc, token, sid).json()["status_code"] == 10
    assert _complete(tc, token, sid).json()["status_code"] == 10  # idempotent
    assert _count(tc, link_id) == 1


# --- Boundary / overquota ---------------------------------------------------


def test_boundary_last_slot_then_overquota(client):
    tc, sf, _ = client
    token, link_id = _setup(
        tc, sf, redirects={"redirect_overquota": "https://panel.test/full?pid={pid}"}
    )
    _add_cell(tc, link_id, COFFEE, 1)  # room for exactly one

    a = _enter(tc, token, "?pid=A")
    _answer(tc, token, a, {"Q2": 1})
    assert _complete(tc, token, a).json()["status_code"] == 10

    b = _enter(tc, token, "?pid=B")
    _answer(tc, token, b, {"Q2": 1})
    resp = _complete(tc, token, b).json()
    assert resp["status_code"] == 40
    assert resp["complete"] is False
    assert resp["redirect_url"] == "https://panel.test/full?pid=B"
    assert _count(tc, link_id) == 1  # the full cell was never over-counted


def test_early_answer_time_overquota(client):
    """A full DECIDABLE cell screens the respondent out at answer time (40),
    before they finish the survey."""
    tc, sf, _ = client
    token, link_id = _setup(tc, sf)
    _add_cell(tc, link_id, COFFEE, 1)

    a = _enter(tc, token)
    _answer(tc, token, a, {"Q2": 1})
    _complete(tc, token, a)  # fills the cell

    b = _enter(tc, token)
    resp = _answer(tc, token, b, {"Q2": 1}).json()  # Q2 answered -> decidable + full
    assert resp["terminated"] is True
    assert resp["status_code"] == 40


# --- PATCH rules ------------------------------------------------------------


def test_limit_raise_ok_condition_change_rejected_after_session(client):
    tc, sf, _ = client
    token, link_id = _setup(tc, sf)
    quota_id = _add_cell(tc, link_id, COFFEE, 1).json()["cells"][0]["id"]

    # A session now exists on the link.
    _enter(tc, token)

    # Raising the limit mid-field is routine -> allowed.
    resp = tc.patch(f"{_quotas_url(link_id)}/{quota_id}", json={"limit": 50})
    assert resp.status_code == 200
    assert resp.json()["limit"] == 50

    # Changing the condition once a session exists -> rejected.
    other = Expression(op="eq", qid="Q2", value=2).to_dict()
    resp = tc.patch(f"{_quotas_url(link_id)}/{quota_id}", json={"condition": other})
    assert resp.status_code == 409


# --- Security 30 ------------------------------------------------------------


def test_duplicate_pid_security_30_with_redirect(client):
    tc, sf, _ = client
    token, _link_id = _setup(
        tc, sf, redirects={"redirect_security": "https://panel.test/dupe?pid={pid}"}
    )

    a = _enter(tc, token, "?pid=DUP")
    _complete(tc, token, a)  # A reaches a terminal status

    # Same pid re-enters -> served a security screen-out, not the survey.
    resp = tc.get(f"/api/r/{token}?pid=DUP").json()
    assert resp["status_code"] == 30
    assert resp["surveyjs_json"] == {}
    assert resp["redirect_url"] == "https://panel.test/dupe?pid=DUP"


# --- No template -> null redirect ------------------------------------------


def test_no_template_redirect_url_null(client):
    tc, sf, _ = client
    token, link_id = _setup(tc, sf)  # no redirect templates
    _add_cell(tc, link_id, COFFEE, 5)

    sid = _enter(tc, token)
    _answer(tc, token, sid, {"Q2": 1})
    resp = _complete(tc, token, sid).json()
    assert resp["status_code"] == 10
    assert resp.get("redirect_url") is None  # built-in thank-you screen still used

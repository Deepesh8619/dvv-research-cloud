"""Fielding runtime — management + respondent endpoints (T9').

TestClient against the real app with get_db / get_current_user overridden to a
temp sqlite session and a fake user (same pattern as test_questionnaire_routes).
No LLM calls anywhere in this surface. An approved questionnaire is seeded
directly as a QuestionnaireDB row (status='approved', definition_json set) using
a small hand-built SurveyDefinition: one normal question, one with a
`terminate_if` (age < 18 terminates), one with a `show_if`.
"""

import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.auth import get_current_user
from api.db import (
    Base,
    QuestionnaireDB,
    RespondentSessionDB,
    StudyDB,
    UserDB,
    get_db,
)
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
    """Approved instrument: age (terminate < 18), a single-select, and an
    open-text follow-up gated by show_if on the single-select."""
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
        qid="Q3",
        text="Which coffee brand?",
        type=QuestionType.OPEN_TEXT,
        required=False,
        show_if=Expression(op="eq", qid="Q2", value=1),
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

    state = {"role": "owner"}

    def override_get_current_user():
        return UserDB(
            id="u1", email="test@x.com", name="Test", role=state["role"], is_active=True
        )

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    yield TestClient(app), TestSessionLocal, state
    app.dependency_overrides.clear()


def _make_study(session_factory, study_id="study_f1"):
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


def _make_questionnaire(
    session_factory, study_id="study_f1", qid="quest_f1", status="approved", survey=None
):
    survey = survey or _sample_survey(study_id)
    db = session_factory()
    db.add(
        QuestionnaireDB(
            id=qid,
            study_id=study_id,
            status=status,
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


def _create_link(test_client, study_id="study_f1"):
    return test_client.post(f"/api/studies/{study_id}/fielding/links")


# --- Management -------------------------------------------------------------


def test_create_link_requires_approved_questionnaire(client):
    """A draft (unapproved) questionnaire cannot be fielded — 409."""
    test_client, sf, _ = client
    _make_study(sf)
    _make_questionnaire(sf, status="draft")

    resp = _create_link(test_client)
    assert resp.status_code == 409


def test_create_link_no_questionnaire_409(client):
    test_client, sf, _ = client
    _make_study(sf)
    resp = _create_link(test_client)
    assert resp.status_code == 409


def test_create_link_unknown_study_404(client):
    test_client, _, _ = client
    resp = test_client.post("/api/studies/nope/fielding/links")
    assert resp.status_code == 404


def test_create_link_succeeds_on_approved(client):
    test_client, sf, _ = client
    _make_study(sf)
    _make_questionnaire(sf)

    resp = _create_link(test_client)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "open"
    assert body["survey_version"] == 1
    assert body["respondent_path"] == f"/r/{body['token']}"


def test_create_link_stamps_theme_into_snapshot(client):
    """A `theme` on create is stamped into the link's pinned snapshot, so the
    respondent entry endpoint returns it — without touching the questionnaire."""
    test_client, sf, _ = client
    _make_study(sf)
    _make_questionnaire(sf)

    resp = test_client.post(
        "/api/studies/study_f1/fielding/links", json={"theme": "healthcare"}
    )
    assert resp.status_code == 200
    token = resp.json()["token"]

    entry = test_client.get(f"/api/r/{token}")
    assert entry.status_code == 200
    assert entry.json()["theme"] == "healthcare"


def test_create_link_theme_does_not_mutate_questionnaire(client):
    """The link-level theme is a presentation choice on the SNAPSHOT only — the
    questionnaire's own definition (and any later link) stays theme-free."""
    test_client, sf, _ = client
    _make_study(sf)
    _make_questionnaire(sf)

    themed = test_client.post(
        "/api/studies/study_f1/fielding/links", json={"theme": "auto"}
    )
    assert themed.status_code == 200

    # A second link with no theme sees no theme — proves the definition is clean.
    plain = _create_link(test_client)
    plain_token = plain.json()["token"]
    assert test_client.get(f"/api/r/{plain_token}").json()["theme"] is None


def test_create_link_unknown_theme_422(client):
    test_client, sf, _ = client
    _make_study(sf)
    _make_questionnaire(sf)

    resp = test_client.post(
        "/api/studies/study_f1/fielding/links", json={"theme": "chartreuse"}
    )
    assert resp.status_code == 422


def test_settings_theme_flows_into_fresh_link_entry(client):
    """A theme set via the builder settings PATCH becomes the default a fresh,
    un-themed link inherits — the entry endpoint returns it end-to-end."""
    test_client, sf, _ = client
    _make_study(sf)
    _make_questionnaire(sf)

    assert (
        test_client.patch(
            "/api/studies/study_f1/questionnaire/settings", json={"theme": "retail"}
        ).status_code
        == 200
    )
    token = _create_link(test_client).json()["token"]
    assert test_client.get(f"/api/r/{token}").json()["theme"] == "retail"


def test_list_links_reports_counts(client):
    test_client, sf, _ = client
    _make_study(sf)
    _make_questionnaire(sf)
    token = _create_link(test_client).json()["token"]

    # Two entries: one completes, one terminates.
    s_done = test_client.get(f"/api/r/{token}").json()["session_id"]
    test_client.post(f"/api/r/{token}/sessions/{s_done}/complete")
    s_term = test_client.get(f"/api/r/{token}").json()["session_id"]
    test_client.post(f"/api/r/{token}/sessions/{s_term}/answers", json={"answers": {"Q1": 15}})

    resp = test_client.get("/api/studies/study_f1/fielding/links")
    assert resp.status_code == 200
    counts = resp.json()["links"][0]["counts"]
    assert counts == {
        "starts": 2,
        "completes": 1,
        "terminates": 1,
        "security": 0,
        "overquota": 0,
    }


def test_reviewer_can_get_but_not_create(client):
    """Reviewer: GET links allowed, POST create/close 403."""
    test_client, sf, state = client
    _make_study(sf)
    _make_questionnaire(sf)

    state["role"] = "reviewer"
    assert test_client.get("/api/studies/study_f1/fielding/links").status_code == 200
    assert _create_link(test_client).status_code == 403
    assert (
        test_client.post(
            "/api/studies/study_f1/fielding/links/whatever/close"
        ).status_code
        == 403
    )


# --- Respondent entry -------------------------------------------------------


def test_entry_creates_session_and_serves_surveyjs_without_terminate_logic(client):
    """The compiled payload the respondent gets carries NO terminate logic — the
    server-authoritative guarantee — while show_if (visibleIf) IS emitted."""
    test_client, sf, _ = client
    _make_study(sf)
    _make_questionnaire(sf)
    token = _create_link(test_client).json()["token"]

    resp = test_client.get(f"/api/r/{token}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"].startswith("rsess_")
    assert body["status"] == "open"

    surveyjs = body["surveyjs_json"]
    serialized = json.dumps(surveyjs)
    # No trace of terminate enforcement leaked to the client.
    assert "terminate" not in serialized
    # The terminate threshold (18) must not appear anywhere as a client rule.
    assert "18" not in serialized
    # But show_if DID compile to a visibleIf (proves show-logic is emitted).
    assert "visibleIf" in serialized


def test_entry_captures_query_params(client):
    test_client, sf, _ = client
    _make_study(sf)
    _make_questionnaire(sf)
    token = _create_link(test_client).json()["token"]

    session_id = test_client.get(f"/api/r/{token}?pid=P123&rid=R9").json()["session_id"]

    db = sf()
    session = (
        db.query(RespondentSessionDB)
        .filter(RespondentSessionDB.id == session_id)
        .first()
    )
    assert session.entry_params == {"pid": "P123", "rid": "R9"}
    db.close()


def test_entry_theme_is_null_when_metadata_absent(client):
    """A survey with no metadata.theme entry-serves theme=None (default)."""
    test_client, sf, _ = client
    _make_study(sf)
    _make_questionnaire(sf)  # _sample_survey has no metadata
    token = _create_link(test_client).json()["token"]

    body = test_client.get(f"/api/r/{token}").json()
    assert body["theme"] is None


def test_entry_theme_passed_through_from_metadata(client):
    """A survey pinned with metadata.theme surfaces that slug on entry."""
    test_client, sf, _ = client
    _make_study(sf)
    survey = _sample_survey("study_f1")
    survey.metadata = {"theme": "fmcg"}
    _make_questionnaire(sf, survey=survey)
    token = _create_link(test_client).json()["token"]

    body = test_client.get(f"/api/r/{token}").json()
    assert body["theme"] == "fmcg"


def test_unknown_token_404(client):
    test_client, sf, _ = client
    _make_study(sf)
    assert test_client.get("/api/r/does-not-exist").status_code == 404


# --- Incremental capture + terminate ----------------------------------------


def test_answers_persist_and_accumulate(client):
    test_client, sf, _ = client
    _make_study(sf)
    _make_questionnaire(sf)
    token = _create_link(test_client).json()["token"]
    sid = test_client.get(f"/api/r/{token}").json()["session_id"]

    r1 = test_client.post(
        f"/api/r/{token}/sessions/{sid}/answers",
        json={"answers": {"Q1": 25}, "current_page": 1},
    )
    assert r1.status_code == 200 and r1.json()["ok"] is True
    test_client.post(
        f"/api/r/{token}/sessions/{sid}/answers",
        json={"answers": {"Q2": 1}, "current_page": 2},
    )

    resume = test_client.get(f"/api/r/{token}/sessions/{sid}").json()
    assert resume["answers"] == {"Q1": 25, "Q2": 1}
    assert resume["current_page"] == 2
    assert resume["status_code"] is None


def test_terminate_fires_server_side_sets_status_20(client):
    test_client, sf, _ = client
    _make_study(sf)
    _make_questionnaire(sf)
    token = _create_link(test_client).json()["token"]
    sid = test_client.get(f"/api/r/{token}").json()["session_id"]

    resp = test_client.post(
        f"/api/r/{token}/sessions/{sid}/answers",
        json={"answers": {"Q1": 16}, "current_page": 1},
    )
    assert resp.status_code == 200
    assert resp.json() == {"terminated": True, "status_code": 20}

    resume = test_client.get(f"/api/r/{token}/sessions/{sid}").json()
    assert resume["status_code"] == 20
    assert resume["completed_at"] is not None


def test_terminated_session_rejects_further_answers(client):
    test_client, sf, _ = client
    _make_study(sf)
    _make_questionnaire(sf)
    token = _create_link(test_client).json()["token"]
    sid = test_client.get(f"/api/r/{token}").json()["session_id"]

    test_client.post(
        f"/api/r/{token}/sessions/{sid}/answers", json={"answers": {"Q1": 16}}
    )
    # A resubmit must not un-terminate or write more answers.
    resp = test_client.post(
        f"/api/r/{token}/sessions/{sid}/answers", json={"answers": {"Q1": 40, "Q2": 1}}
    )
    assert resp.json()["already_final"] is True
    assert resp.json()["status_code"] == 20

    # The Q1=40 overwrite did NOT land; status is still 20.
    resume = test_client.get(f"/api/r/{token}/sessions/{sid}").json()
    assert resume["status_code"] == 20
    assert resume["answers"] == {"Q1": 16}


def test_complete_sets_status_10_and_is_idempotent(client):
    test_client, sf, _ = client
    _make_study(sf)
    _make_questionnaire(sf)
    token = _create_link(test_client).json()["token"]
    sid = test_client.get(f"/api/r/{token}").json()["session_id"]

    r1 = test_client.post(f"/api/r/{token}/sessions/{sid}/complete")
    assert r1.json() == {"status_code": 10, "complete": True}
    # Idempotent: calling again returns 10, does not error.
    r2 = test_client.post(f"/api/r/{token}/sessions/{sid}/complete")
    assert r2.json() == {"status_code": 10, "complete": True}


def test_complete_does_not_override_terminate(client):
    test_client, sf, _ = client
    _make_study(sf)
    _make_questionnaire(sf)
    token = _create_link(test_client).json()["token"]
    sid = test_client.get(f"/api/r/{token}").json()["session_id"]

    test_client.post(
        f"/api/r/{token}/sessions/{sid}/answers", json={"answers": {"Q1": 10}}
    )
    resp = test_client.post(f"/api/r/{token}/sessions/{sid}/complete")
    # First terminal status wins — stays 20.
    assert resp.json()["status_code"] == 20


def test_resume_returns_accumulated_answers(client):
    test_client, sf, _ = client
    _make_study(sf)
    _make_questionnaire(sf)
    token = _create_link(test_client).json()["token"]
    sid = test_client.get(f"/api/r/{token}").json()["session_id"]

    test_client.post(
        f"/api/r/{token}/sessions/{sid}/answers",
        json={"answers": {"Q1": 30, "Q2": 2}, "current_page": 2},
    )
    resume = test_client.get(f"/api/r/{token}/sessions/{sid}").json()
    assert resume["answers"] == {"Q1": 30, "Q2": 2}
    assert resume["current_page"] == 2


def test_session_must_belong_to_link(client):
    """A session id from one link is not addressable via another link's token."""
    test_client, sf, _ = client
    _make_study(sf)
    _make_questionnaire(sf)
    token_a = _create_link(test_client).json()["token"]
    token_b = _create_link(test_client).json()["token"]
    sid_a = test_client.get(f"/api/r/{token_a}").json()["session_id"]

    # Using link B's token with link A's session → 404, no info leak.
    assert test_client.get(f"/api/r/{token_b}/sessions/{sid_a}").status_code == 404


# --- Close semantics --------------------------------------------------------


def test_closed_link_refuses_new_sessions_cleanly(client):
    """Entering a closed link returns a clean closed payload, not a 500."""
    test_client, sf, _ = client
    _make_study(sf)
    _make_questionnaire(sf)
    link = _create_link(test_client).json()
    token = link["token"]

    close = test_client.post(
        f"/api/studies/study_f1/fielding/links/{link['id']}/close"
    )
    assert close.status_code == 200
    assert close.json()["status"] == "closed"

    resp = test_client.get(f"/api/r/{token}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "closed"
    assert resp.json()["session_id"] == ""


def test_closed_link_lets_inflight_sessions_finish(client):
    """A session created while open can still submit + complete after close."""
    test_client, sf, _ = client
    _make_study(sf)
    _make_questionnaire(sf)
    link = _create_link(test_client).json()
    token = link["token"]
    sid = test_client.get(f"/api/r/{token}").json()["session_id"]  # in-flight

    test_client.post(f"/api/studies/study_f1/fielding/links/{link['id']}/close")

    # In-flight session continues to work.
    ans = test_client.post(
        f"/api/r/{token}/sessions/{sid}/answers", json={"answers": {"Q1": 22}}
    )
    assert ans.status_code == 200 and ans.json()["ok"] is True
    done = test_client.post(f"/api/r/{token}/sessions/{sid}/complete")
    assert done.json()["status_code"] == 10


def test_close_unknown_link_404(client):
    test_client, sf, _ = client
    _make_study(sf)
    resp = test_client.post("/api/studies/study_f1/fielding/links/nope/close")
    assert resp.status_code == 404


# --- dry run: what the script does to people, before it does it to people ----


def _impossible_survey(study_id: str) -> SurveyDefinition:
    """Q3's base tests a code Q2 never offers, so nobody can ever be shown it."""
    survey = _sample_survey(study_id)
    survey[survey.questions[2].qid].show_if = Expression(op="eq", qid="Q2", value=9)
    return survey


def test_dry_run_reports_what_the_script_does(client) -> None:
    test_client, SessionLocal = client[0], client[1]
    _make_study(SessionLocal)
    _make_questionnaire(SessionLocal)

    resp = test_client.post(
        "/api/studies/study_f1/fielding/dry-run", json={"n": 200, "seed": 5}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["n_respondents"] == 200
    assert sum(body["dispositions"].values()) == 200
    # The screen-out removed some but not all — an incidence figure, before a
    # single real respondent has been paid for.
    screen_out = next(
        c for c in body["conditions"] if c["kind"] == "screen_out" and c["qid"] == "Q1"
    )
    assert 0 < screen_out["fired"] < 200
    # Everything the link-creation response warns about is here too: the point
    # of a dry run is to see it all BEFORE publishing, not after.
    assert "display_issues" in body and "unenforced_routing" in body


def test_a_link_is_refused_when_a_condition_can_never_be_true(client) -> None:
    """A screener that cannot screen looks perfect until the data comes back."""
    test_client, SessionLocal = client[0], client[1]
    _make_study(SessionLocal)
    _make_questionnaire(SessionLocal, survey=_impossible_survey("study_f1"))

    resp = test_client.post("/api/studies/study_f1/fielding/links", json={})

    assert resp.status_code == 409
    assert "never be true" in resp.json()["detail"]


def test_the_refusal_can_be_overridden_deliberately(client) -> None:
    """Blocking with no way through is its own failure. The decision is logged."""
    test_client, SessionLocal = client[0], client[1]
    _make_study(SessionLocal)
    _make_questionnaire(SessionLocal, survey=_impossible_survey("study_f1"))

    resp = test_client.post(
        "/api/studies/study_f1/fielding/links", json={"force": True}
    )

    assert resp.status_code == 200


# --- Soft launch ------------------------------------------------------------


def _complete_one(test_client, token):
    """Enter and complete one respondent; returns the session id."""
    sid = test_client.get(f"/api/r/{token}").json()["session_id"]
    test_client.post(
        f"/api/r/{token}/sessions/{sid}/answers", json={"answers": {"Q1": 30, "Q2": 1}}
    )
    test_client.post(f"/api/r/{token}/sessions/{sid}/complete")
    return sid


def test_soft_launch_holds_entry_once_the_batch_is_complete(client):
    """The whole point: after N completes the link stops admitting NEW people."""
    test_client, sf, _ = client
    _make_study(sf)
    _make_questionnaire(sf)
    link = test_client.post(
        "/api/studies/study_f1/fielding/links", json={"soft_launch_limit": 1}
    ).json()
    token = link["token"]
    assert link["soft_launch_limit"] == 1
    assert link["soft_launch_reached"] is False

    _complete_one(test_client, token)

    held = test_client.get(f"/api/r/{token}").json()
    assert held["status"] == "paused"
    assert held["session_id"] == ""
    assert held["surveyjs_json"] == {}
    # A hold must not invent a disposition the field never had.
    db = sf()
    assert db.query(RespondentSessionDB).count() == 1
    db.close()


def test_soft_launch_does_not_kill_respondents_already_in_flight(client):
    """Someone mid-interview when the batch fills still finishes — real people."""
    test_client, sf, _ = client
    _make_study(sf)
    _make_questionnaire(sf)
    token = test_client.post(
        "/api/studies/study_f1/fielding/links", json={"soft_launch_limit": 1}
    ).json()["token"]

    in_flight = test_client.get(f"/api/r/{token}").json()["session_id"]
    _complete_one(test_client, token)  # batch is now full
    assert test_client.get(f"/api/r/{token}").json()["status"] == "paused"

    # ...but the one already inside is unaffected, so completes overshoot by 1.
    done = test_client.post(f"/api/r/{token}/sessions/{in_flight}/complete")
    assert done.json() == {"status_code": 10, "complete": True}


def test_release_resumes_the_same_link(client):
    """Release is a PATCH to null — the link and its token stay the same."""
    test_client, sf, _ = client
    _make_study(sf)
    _make_questionnaire(sf)
    link = test_client.post(
        "/api/studies/study_f1/fielding/links", json={"soft_launch_limit": 1}
    ).json()
    token, link_id = link["token"], link["id"]
    _complete_one(test_client, token)
    assert test_client.get(f"/api/r/{token}").json()["status"] == "paused"

    released = test_client.patch(
        f"/api/studies/study_f1/fielding/links/{link_id}/soft-launch", json={"limit": None}
    )
    assert released.status_code == 200
    assert released.json()["soft_launch_limit"] is None
    assert released.json()["soft_launch_reached"] is False

    resumed = test_client.get(f"/api/r/{token}").json()
    assert resumed["status"] == "open"
    assert resumed["session_id"]
    assert resumed["surveyjs_json"]


def test_hold_can_be_applied_after_the_link_is_already_open(client):
    """A live link can be paused without closing it."""
    test_client, sf, _ = client
    _make_study(sf)
    _make_questionnaire(sf)
    link = test_client.post("/api/studies/study_f1/fielding/links").json()
    token, link_id = link["token"], link["id"]
    _complete_one(test_client, token)
    assert test_client.get(f"/api/r/{token}").json()["status"] == "open"

    resp = test_client.patch(
        f"/api/studies/study_f1/fielding/links/{link_id}/soft-launch", json={"limit": 1}
    )
    assert resp.json()["soft_launch_reached"] is True
    assert test_client.get(f"/api/r/{token}").json()["status"] == "paused"


def test_held_link_bounces_arrivals_on_the_overquota_redirect(client):
    """A panel expects a redirect, not a dead end, for 'we can't use you now'."""
    test_client, sf, _ = client
    _make_study(sf)
    _make_questionnaire(sf)
    token = test_client.post(
        "/api/studies/study_f1/fielding/links",
        json={"soft_launch_limit": 1, "redirect_overquota": "https://panel.example/oq?rid={rid}"},
    ).json()["token"]
    _complete_one(test_client, token)

    held = test_client.get(f"/api/r/{token}?rid=abc123").json()
    assert held["status"] == "paused"
    assert held["redirect_url"] == "https://panel.example/oq?rid=abc123"


def test_closed_beats_paused(client):
    """A closed link reports closed even while a hold is set — closed is final."""
    test_client, sf, _ = client
    _make_study(sf)
    _make_questionnaire(sf)
    link = test_client.post(
        "/api/studies/study_f1/fielding/links", json={"soft_launch_limit": 1}
    ).json()
    _complete_one(test_client, link["token"])
    test_client.post(f"/api/studies/study_f1/fielding/links/{link['id']}/close")
    assert test_client.get(f"/api/r/{link['token']}").json()["status"] == "closed"


def test_soft_launch_limit_must_be_at_least_one(client):
    test_client, sf, _ = client
    _make_study(sf)
    _make_questionnaire(sf)
    link_id = test_client.post("/api/studies/study_f1/fielding/links").json()["id"]
    resp = test_client.patch(
        f"/api/studies/study_f1/fielding/links/{link_id}/soft-launch", json={"limit": 0}
    )
    assert resp.status_code == 422


def test_soft_launch_patch_unknown_link_404(client):
    test_client, sf, _ = client
    _make_study(sf)
    resp = test_client.patch(
        "/api/studies/study_f1/fielding/links/nope/soft-launch", json={"limit": 5}
    )
    assert resp.status_code == 404


def test_a_screen_out_discards_answers_given_after_it_on_the_same_page(client):
    """Q1 screens the respondent out, so Q2/Q3 from the same submit are not data.

    They were sent together, before the server could judge any of them. Keeping
    them would put screened-out people into every downstream base — and our own
    QC check reads exactly that as "the screen-out did not stop the interview".
    """
    test_client, sf, _ = client
    _make_study(sf)
    _make_questionnaire(sf)
    token = _create_link(test_client).json()["token"]
    sid = test_client.get(f"/api/r/{token}").json()["session_id"]

    resp = test_client.post(
        f"/api/r/{token}/sessions/{sid}/answers",
        json={"answers": {"Q1": 16, "Q2": 1, "Q3": "Brand X"}},
    )
    assert resp.json()["terminated"] is True

    # Q1 — the screening answer itself — is kept: it is the evidence.
    assert test_client.get(f"/api/r/{token}/sessions/{sid}").json()["answers"] == {"Q1": 16}

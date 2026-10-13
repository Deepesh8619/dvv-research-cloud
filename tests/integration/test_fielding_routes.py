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
    # The panel's own params survive alongside the paradata now captured at
    # entry (device, a hashed address): a redirect template reads them back.
    assert session.entry_params["pid"] == "P123"
    assert session.entry_params["rid"] == "R9"
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
    # Refused by the pre-link check, which names the rule that cannot be met.
    failing = {c["id"] for c in resp.json()["detail"]["checks"]}
    assert "possible" in failing


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


def _exclusive_survey(study_id: str) -> SurveyDefinition:
    """One multi-punch whose last option is "None of the above" — anchored and
    exclusive, the shape every screener ends on."""
    q = Question(
        qid="Q5",
        text="Which have you bought from in the past 6 months?",
        type=QuestionType.MULTI,
        randomize_options=True,
        options=[
            Option(code=1, label="Ulta"),
            Option(code=2, label="Sephora"),
            Option(code=3, label="Target"),
            Option(code=4, label="None of the above", anchored=True, exclusive=True),
        ],
    )
    return SurveyDefinition(
        study_id=study_id,
        title="Skincare",
        source="authored",
        status="approved",
        version=1,
        blocks=[Block(block_id="B1", question_ids=["Q5"])],
        questions=[q],
    )


def test_exclusive_option_wins_over_co_selections_through_the_api(client):
    test_client, sf, _ = client
    _make_study(sf)
    _make_questionnaire(sf, survey=_exclusive_survey("study_f1"))
    token = _create_link(test_client).json()["token"]
    entry = test_client.get(f"/api/r/{token}").json()
    sid = entry["session_id"]

    # The respondent ticks two retailers AND "None of the above".
    posted = test_client.post(
        f"/api/r/{token}/sessions/{sid}/answers",
        json={"answers": {"Q5": [1, 2, 4]}, "current_page": 1},
    )
    assert posted.status_code == 200

    # What is STORED is only the exclusive punch — the contradiction never
    # reaches the tabs, whatever the browser sent.
    assert test_client.get(f"/api/r/{token}/sessions/{sid}").json()["answers"] == {"Q5": [4]}


def test_anchored_option_is_last_in_the_link_the_respondent_gets(client):
    test_client, sf, _ = client
    _make_study(sf)
    _make_questionnaire(sf, survey=_exclusive_survey("study_f1"))
    token = _create_link(test_client).json()["token"]

    firsts = set()
    for _ in range(25):
        entry = test_client.get(f"/api/r/{token}").json()
        element = next(
            e
            for page in entry["surveyjs_json"]["pages"]
            for e in page["elements"]
            if e.get("name") == "Q5"
        )
        assert element["choices"][-1]["text"] == "None of the above"
        firsts.add(element["choices"][0]["text"])
    assert len(firsts) > 1  # a real shuffle, per respondent


# --- Test links: dry-run data + jump-to-question ----------------------------


def test_a_test_link_opens_even_when_a_real_link_would_be_refused(client):
    """The pre-link check would 409 this survey (see the non-test version of
    this test above) — a test link skips that gate entirely, on purpose: it
    exists to try a script, including a broken one, on nobody real."""
    test_client, sf, _ = client
    _make_study(sf)
    _make_questionnaire(sf, survey=_impossible_survey("study_f1"))

    resp = test_client.post("/api/studies/study_f1/fielding/links", json={"test": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_test"] is True
    assert test_client.get(f"/api/r/{body['token']}").json()["is_test"] is True
    # Opened, but not silently: the tester is told what a live link would refuse.
    assert body["preflight_failures"], "a test link must still report what would block going live"


def test_populate_adds_synthetic_sessions_and_shows_in_field_report(client):
    test_client, sf, _ = client
    _make_study(sf)
    _make_questionnaire(sf)
    link = test_client.post(
        "/api/studies/study_f1/fielding/links", json={"test": True}
    ).json()

    resp = test_client.post(
        f"/api/studies/study_f1/fielding/links/{link['id']}/populate", json={"n": 50}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["added"] == 50
    assert sum(body["dispositions"].values()) == 50

    report = test_client.get(
        f"/api/studies/study_f1/fielding/links/{link['id']}/field-report"
    ).json()
    assert report["starts"] == 50


def test_populate_on_a_real_link_is_refused(client):
    test_client, sf, _ = client
    _make_study(sf)
    _make_questionnaire(sf)
    link = _create_link(test_client).json()

    resp = test_client.post(
        f"/api/studies/study_f1/fielding/links/{link['id']}/populate", json={"n": 10}
    )
    assert resp.status_code == 409


def test_jump_on_a_test_link_reaches_the_target_with_the_screener_answered(client):
    """Q3 sits behind Q2 (show_if Q2==1) — jumping there must also clear Q1's
    terminate (age >= 18), since a screened-out respondent never reaches Q3."""
    test_client, sf, _ = client
    _make_study(sf)
    _make_questionnaire(sf)
    link = test_client.post(
        "/api/studies/study_f1/fielding/links", json={"test": True}
    ).json()
    token = link["token"]
    sid = test_client.get(f"/api/r/{token}").json()["session_id"]

    resp = test_client.post(
        f"/api/r/{token}/sessions/{sid}/jump", json={"qid": "Q3"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["target"] == "Q3"
    assert body["answers"]["Q1"] >= 18
    assert body["answers"]["Q2"] == 1

    # And the session itself now carries that path.
    resume = test_client.get(f"/api/r/{token}/sessions/{sid}").json()
    assert resume["answers"]["Q1"] >= 18
    assert resume["answers"]["Q2"] == 1


def test_jump_on_a_real_link_is_refused(client):
    test_client, sf, _ = client
    _make_study(sf)
    _make_questionnaire(sf)
    token = _create_link(test_client).json()["token"]
    sid = test_client.get(f"/api/r/{token}").json()["session_id"]

    resp = test_client.post(
        f"/api/r/{token}/sessions/{sid}/jump", json={"qid": "Q3"}
    )
    assert resp.status_code == 403


def test_a_phone_and_a_stated_place_are_recorded_at_entry(client, monkeypatch) -> None:
    """Both questions a client asks of every study and nobody can answer
    afterwards: what were they on, and were they where they said they were.
    The place check is a FLAG — the interview is never dropped for it."""
    import src.engines.fielding.paradata as paradata

    test_client, SessionLocal = client[0], client[1]
    _make_study(SessionLocal)
    zip_survey = _sample_survey("study_f1")
    zip_survey.questions.append(
        Question(qid="Q4", text="What is your ZIP code?", type=QuestionType.OPEN_TEXT,
                 required=False)
    )
    zip_survey.blocks[0].question_ids.append("Q4")
    _make_questionnaire(SessionLocal, survey=zip_survey)
    token = test_client.post("/api/studies/study_f1/fielding/links", json={}).json()["token"]

    # The server's own location database says this connection is in California.
    monkeypatch.setattr(paradata, "locate", lambda ip: {"postal": "90210", "region": "California"})

    iphone = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
              "AppleWebKit/605.1 Mobile/15E148")
    entry = test_client.get(
        f"/api/r/{token}", headers={"User-Agent": iphone, "X-Forwarded-For": "203.0.113.9"}
    )
    session_id = entry.json()["session_id"]

    db = SessionLocal()
    stored = db.query(RespondentSessionDB).filter_by(id=session_id).first()
    assert stored.entry_params["device"] == "mobile"
    assert "203.0.113.9" not in str(stored.entry_params)  # the address itself is not kept
    db.close()

    # They say they are in New York; the connection says California.
    test_client.post(
        f"/api/r/{token}/sessions/{session_id}/answers",
        json={"answers": {"Q1": 30, "Q2": 1, "Q4": "11042"}, "current_page": 1},
    )
    db = SessionLocal()
    stored = db.query(RespondentSessionDB).filter_by(id=session_id).first()
    assert stored.entry_params["place_check"] == "mismatch"
    assert stored.status_code is None  # flagged, not screened out
    db.close()


def test_a_pre_link_check_that_cannot_run_says_what_kind_of_failure_it_was(
    client, monkeypatch
) -> None:
    """Seen in production: "Could not run the pre-link check." and nothing more,
    which tells a reviewer nothing and the engineer they bring it to no more.
    The KIND of failure is named; the detail stays in the server log."""
    import api.routes.fielding as fielding_routes

    test_client, SessionLocal = client[0], client[1]
    _make_study(SessionLocal)
    _make_questionnaire(SessionLocal)

    def explode(*_args, **_kwargs):
        raise TypeError("unhashable type: 'dict'")

    monkeypatch.setattr(fielding_routes, "run_heavy_detached", explode)
    failed = test_client.get("/api/studies/study_f1/fielding/preflight")
    assert failed.status_code == 500
    assert "TypeError" in failed.json()["detail"]
    assert "server log" in failed.json()["detail"]


def test_a_pre_link_check_killed_for_memory_is_told_as_an_operational_problem(
    client, monkeypatch
) -> None:
    """A worker killed on a small box is not a bug in the questionnaire, and a
    reviewer should not go looking for one."""
    from concurrent.futures.process import BrokenProcessPool

    import api.routes.fielding as fielding_routes

    test_client, SessionLocal = client[0], client[1]
    _make_study(SessionLocal)
    _make_questionnaire(SessionLocal)

    def die(*_args, **_kwargs):
        raise BrokenProcessPool("worker died")

    monkeypatch.setattr(fielding_routes, "run_heavy_detached", die)
    out = test_client.get("/api/studies/study_f1/fielding/preflight")
    assert out.status_code == 503
    assert "ran out of room" in out.json()["detail"]


def test_a_rank_masked_by_an_awareness_grid_is_reachable_on_a_test_link(client) -> None:
    """The shape that broke in production, driven the way a tester drove it.

    A rank question masked by an awareness GRID — "rank your top 3 of the
    pharmacies you have heard of" — which is the shape that produced
    "Server error: TypeError: unhashable type: 'dict'" on a live study.

    Checked against the bug: with the fix reverted, the dry run below raises
    that exact TypeError, and this test fails. A regression test nobody has
    seen fail is a guess about what it covers.
    """
    test_client, SessionLocal = client[0], client[1]
    _make_study(SessionLocal)

    survey = _sample_survey("study_f1")
    aware = Question(
        qid="Q5", text="Which of these pharmacies have you heard of?",
        type=QuestionType.GRID_SINGLE,
        rows=[Option(code=1, label="CVS"), Option(code=2, label="Walgreens")],
        cols=[Option(code=1, label="Yes"), Option(code=2, label="No")],
    )
    ranked = Question(
        qid="Q6", text="Rank your top 3", type=QuestionType.RANK,
        options=[Option(code=1, label="CVS"), Option(code=2, label="Walgreens")],
        # Asked only of people who know at least one — a condition that names
        # the grid without naming a row, which is how scripters write it.
        show_if=Expression(op="selected", qid="Q5", value=1),
        validation={"techniques": {"masks": [{"mode": "selected", "source_qid": "Q5"}]}},
    )
    survey.questions.extend([aware, ranked])
    survey.blocks[0].question_ids.extend(["Q5", "Q6"])
    _make_questionnaire(SessionLocal, survey=survey)

    # The dry run is where it bites: every synthetic respondent answers the
    # grid as {row: column}, and the mask on the rank question then reads it.
    simulated = test_client.post("/api/studies/study_f1/fielding/dry-run", json={"n": 20})
    assert simulated.status_code == 200, simulated.text
    assert simulated.json()["n_respondents"] == 20

    # And a tester walking the link answers that grid the same way.
    link = test_client.post("/api/studies/study_f1/fielding/links",
                            json={"test": True}).json()
    session_id = test_client.get(f"/api/r/{link['token']}").json()["session_id"]
    answered = test_client.post(
        f"/api/r/{link['token']}/sessions/{session_id}/answers",
        json={"answers": {"Q1": 30, "Q2": 1, "Q5": {"1": 1, "2": 2}}, "current_page": 1},
    )
    assert answered.status_code == 200, answered.text


def _capped_survey(study_id="study_f1", **validation):
    """One multi-punch that states how many answers it allows."""
    survey = _sample_survey(study_id)
    survey.questions.append(
        Question(
            qid="Q9",
            text="Which have you used? Select up to 2.",
            type=QuestionType.MULTI,
            options=[Option(code=i, label=f"Brand {i}") for i in (1, 2, 3, 4)],
            validation=validation,
        )
    )
    survey.blocks[0].question_ids.append("Q9")
    return survey


def test_a_page_over_the_cap_is_refused_and_stores_nothing(client):
    """The cap is enforced on the server, not only in the browser.

    SurveyJS holds it for a respondent using the survey we served, so anything
    that gets here is a client that is not ours. It is refused rather than
    trimmed to fit: keeping the first two of four picks an answer the
    respondent never gave, and in the tabs that is indistinguishable from one
    they did.
    """
    test_client, sf, _ = client
    _make_study(sf)
    _make_questionnaire(sf, survey=_capped_survey(max_select=2))
    token = _create_link(test_client).json()["token"]
    sid = test_client.get(f"/api/r/{token}").json()["session_id"]

    resp = test_client.post(
        f"/api/r/{token}/sessions/{sid}/answers",
        json={"answers": {"Q9": [1, 2, 3]}, "current_page": 1},
    )
    assert resp.status_code == 400
    assert "at most 2" in resp.json()["detail"]
    assert test_client.get(f"/api/r/{token}/sessions/{sid}").json()["answers"] == {}

    ok = test_client.post(
        f"/api/r/{token}/sessions/{sid}/answers",
        json={"answers": {"Q9": [1, 2]}, "current_page": 1},
    )
    assert ok.status_code == 200
    assert test_client.get(f"/api/r/{token}/sessions/{sid}").json()["answers"] == {"Q9": [1, 2]}


def test_a_contradictory_cap_never_strands_a_respondent(client):
    """A minimum above the maximum rejects every possible answer.

    Enforcing it would trap anybody who reached the question with no way to
    finish. Preflight fails the link over it instead — the place where a person
    can still fix it — so the respondent is let through.
    """
    test_client, sf, _ = client
    _make_study(sf)
    _make_questionnaire(sf, survey=_capped_survey(min_select=3, max_select=2))
    # Preflight refuses this link — that is the check working. Forced open here
    # to prove what happens to the respondent if somebody overrides it anyway.
    refused = _create_link(test_client)
    assert refused.status_code != 200
    token = test_client.post(
        "/api/studies/study_f1/fielding/links", json={"force": True}
    ).json()["token"]
    sid = test_client.get(f"/api/r/{token}").json()["session_id"]

    resp = test_client.post(
        f"/api/r/{token}/sessions/{sid}/answers",
        json={"answers": {"Q9": [1, 2, 3]}, "current_page": 1},
    )
    assert resp.status_code == 200


def _maxdiff_radios(page):
    """The screen's two radio lists, from inside the panel that holds them."""
    return [
        element
        for group in page["elements"]
        for element in (group.get("elements") or [group])
        if element.get("type") == "radiogroup"
    ]


def _maxdiff_survey(study_id="study_f1"):
    survey = _sample_survey(study_id)
    survey.questions.append(
        Question(
            qid="Q20",
            text="Which of these matters most?",
            type=QuestionType.MAXDIFF,
            options=[Option(code=i, label=f"Item {i}") for i in range(1, 21)],
            validation={
                "maxdiff": {"screens": 12, "items_per_screen": 4, "versions": 10}
            },
        )
    )
    survey.blocks.append(Block(block_id="blk_md", question_ids=["Q20"]))
    return survey


def test_a_respondent_is_dealt_a_version_and_sees_its_items(client):
    """The exercise arrives as twelve screens with four items each, and which
    four is this respondent's design version — not the same four for everyone."""
    test_client, sf, _ = client
    _make_study(sf)
    _make_questionnaire(sf, survey=_maxdiff_survey())
    token = _create_link(test_client).json()["token"]

    entry = test_client.get(f"/api/r/{token}").json()
    screens = [p for p in entry["surveyjs_json"]["pages"] if "maxdiffScreen" in p]
    assert len(screens) == 12
    for page in screens:
        radios = _maxdiff_radios(page)
        assert len(radios) == 2  # best and worst, together on one screen
        for element in radios:
            assert len(element["choices"]) == 4


def test_versions_are_dealt_by_least_fill_not_at_random(client):
    """The design's balance is a property of the SAMPLE. A version that ends up
    with half the respondents of another undoes it."""
    from api.db import RespondentSessionDB

    test_client, sf, _ = client
    _make_study(sf)
    _make_questionnaire(sf, survey=_maxdiff_survey())
    token = _create_link(test_client).json()["token"]

    for _ in range(20):
        test_client.get(f"/api/r/{token}")

    db = sf()
    dealt = [
        s.maxdiff_version
        for s in db.query(RespondentSessionDB).all()
        if s.maxdiff_version is not None
    ]
    db.close()
    assert len(dealt) == 20
    counts = {v: dealt.count(v) for v in set(dealt)}
    # Ten versions, twenty respondents: exactly two each, by construction.
    assert sorted(counts.values()) == [2] * 10


def test_an_answer_naming_an_item_that_was_never_shown_is_refused(client):
    """The browser only ever offered four items, so this cannot be a mistake
    the respondent made — it is a client we did not serve. A utility model fed
    one of these ranks an item on evidence that does not exist.
    """
    test_client, sf, _ = client
    _make_study(sf)
    _make_questionnaire(sf, survey=_maxdiff_survey())
    token = _create_link(test_client).json()["token"]
    entry = test_client.get(f"/api/r/{token}").json()
    sid = entry["session_id"]

    first = next(p for p in entry["surveyjs_json"]["pages"] if "maxdiffScreen" in p)
    shown = [c["value"] for c in _maxdiff_radios(first)[0]["choices"]]
    unshown = next(code for code in range(1, 21) if code not in shown)

    bad = test_client.post(
        f"/api/r/{token}/sessions/{sid}/answers",
        json={"answers": {"Q20_s1_best": unshown, "Q20_s1_worst": shown[0]}},
    )
    assert bad.status_code == 400
    assert "not on this screen" in bad.json()["detail"]
    assert test_client.get(f"/api/r/{token}/sessions/{sid}").json()["answers"] == {}

    good = test_client.post(
        f"/api/r/{token}/sessions/{sid}/answers",
        json={"answers": {"Q20_s1_best": shown[0], "Q20_s1_worst": shown[1]}},
    )
    assert good.status_code == 200
    assert test_client.get(f"/api/r/{token}/sessions/{sid}").json()["answers"] == {
        "Q20_s1_best": shown[0],
        "Q20_s1_worst": shown[1],
    }


def test_the_same_item_cannot_be_both_best_and_worst(client):
    test_client, sf, _ = client
    _make_study(sf)
    _make_questionnaire(sf, survey=_maxdiff_survey())
    token = _create_link(test_client).json()["token"]
    entry = test_client.get(f"/api/r/{token}").json()
    sid = entry["session_id"]
    first = next(p for p in entry["surveyjs_json"]["pages"] if "maxdiffScreen" in p)
    shown = [c["value"] for c in _maxdiff_radios(first)[0]["choices"]]

    resp = test_client.post(
        f"/api/r/{token}/sessions/{sid}/answers",
        json={"answers": {"Q20_s1_best": shown[0], "Q20_s1_worst": shown[0]}},
    )
    assert resp.status_code == 400
    assert "both best and worst" in resp.json()["detail"]


def test_delivery_files_are_reported_from_disk_not_from_memory(client, tmp_path, monkeypatch):
    """The download buttons were drawn from the materialize RESPONSE, so they
    appeared once and were gone on the next page load — with the files still
    sitting on disk and no way to reach them.

    Asked of the filesystem rather than remembered, because the file is the
    only thing that matters: a flag saying the export ran, over a file the disk
    no longer holds, offers a download that 404s.
    """
    import api.routes.fielding as froutes

    # Into tmp_path, not the real studies directory: an earlier cut of this
    # wrote delivery files into the repo and every later test in the suite
    # then saw a study with deliveries it never materialized.
    monkeypatch.setattr(froutes, "STUDIES_DIR", tmp_path)

    test_client, sf, _ = client
    _make_study(sf)
    _make_questionnaire(sf)

    body = test_client.get("/api/studies/study_f1/fielding/links").json()
    assert body["delivery_formats"] == []

    folder = tmp_path / "study_f1"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "study_f1_responses.sav").write_bytes(b"x")
    body = test_client.get("/api/studies/study_f1/fielding/links").json()
    # "sav" — the name the API actually uses. The page was testing for "spss",
    # so the SPSS button never appeared at all, not even right after a
    # materialize, and that is the primary client deliverable.
    assert body["delivery_formats"] == ["sav"]

    (folder / "study_f1_responses.xlsx").write_bytes(b"x")
    body = test_client.get("/api/studies/study_f1/fielding/links").json()
    assert body["delivery_formats"] == ["sav", "xlsx"]


def test_vendor_fields_round_trip_patch_and_viewer_refused(client):
    test_client, sf, state = client
    _make_study(sf)
    _make_questionnaire(sf)
    link = test_client.post(
        "/api/studies/study_f1/fielding/links", json={"vendor_name": "Cint", "cpi": 4.5}
    ).json()
    listed = test_client.get("/api/studies/study_f1/fielding/links").json()["links"][0]
    assert (listed["vendor_name"], listed["cpi"], listed["target_completes"]) == ("Cint", 4.5, None)

    url = f"/api/studies/study_f1/fielding/links/{link['id']}/vendor"
    resp = test_client.patch(url, json={"vendor_name": "Dynata", "cpi": 6, "target_completes": 300})
    assert resp.status_code == 200
    assert (resp.json()["vendor_name"], resp.json()["cpi"], resp.json()["target_completes"]) == (
        "Dynata", 6, 300)
    assert test_client.patch(url, json={"cpi": -1}).status_code == 422
    assert test_client.patch(url, json={"target_completes": 0}).status_code == 422

    state["role"] = "reviewer"
    assert test_client.patch(url, json={"cpi": 1}).status_code == 403

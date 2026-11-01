"""Removing a user and deleting a project: safe, reversible, owner-only."""

from api.db import FieldingLinkDB, UserDB
from tests.integration.test_fielding_routes import (  # noqa: F401 - `client` is a fixture
    _make_questionnaire,
    _make_study,
    client,
)


def _user(sf, uid, role="analyst", active=True):
    db = sf()
    db.add(UserDB(id=uid, email=f"{uid}@example.com", name=uid, role=role, password_hash="x",
                  is_active=active, created_at="2026-01-01"))
    db.commit()
    db.close()


def test_a_removed_user_is_deactivated_and_adding_them_back_restores_them(client):  # noqa: F811
    c, sf, _ = client
    _user(sf, "u_a")
    assert c.delete("/api/auth/users/u_a").status_code == 204
    db = sf()
    assert db.get(UserDB, "u_a").is_active is False  # kept: their name stays on past work
    db.close()
    assert c.delete("/api/auth/users/u_a").status_code == 404
    back = c.post("/api/auth/users", json={"email": "u_a@example.com", "name": "Back",
                                           "role": "reviewer", "password": "Passw0rd!long"})
    assert back.status_code == 200 and back.json()["id"] == "u_a"
    assert back.json()["is_active"] is True and back.json()["role"] == "reviewer"


def test_a_project_is_deleted_only_with_no_live_link_and_can_be_restored(client):  # noqa: F811
    c, sf, _ = client
    _make_study(sf)
    _make_questionnaire(sf)
    link = c.post("/api/studies/study_f1/fielding/links").json()
    refused = c.delete("/api/studies/study_f1")
    assert refused.status_code == 409 and link["id"] in refused.json()["detail"]

    db = sf()
    db.get(FieldingLinkDB, link["id"]).status = "closed"
    db.commit()
    before = c.get("/api/studies/study_f1").json()["status"]
    db.close()
    assert c.delete("/api/studies/study_f1").json()["status"] == "archived"
    assert c.post("/api/studies/study_f1/restore").json()["status"] == before
    assert c.post("/api/studies/study_f1/restore").status_code == 409


def test_you_cannot_remove_yourself_or_the_last_owner(client):  # noqa: F811
    c, sf, _ = client
    _user(sf, "u1", role="owner")   # the signed-in test user, and the only owner
    assert "yourself" in c.delete("/api/auth/users/u1").json()["detail"]
    _user(sf, "u_owner", role="owner")
    assert c.delete("/api/auth/users/u_owner").status_code == 204
    _user(sf, "u_last", role="owner", active=True)
    db = sf()
    db.get(UserDB, "u1").role = "analyst"
    db.commit()
    db.close()
    assert "last owner" in c.delete("/api/auth/users/u_last").json()["detail"]


def test_only_an_owner_can_remove_or_delete(client):  # noqa: F811
    c, sf, state = client
    _make_study(sf)
    _user(sf, "u_a")
    state["role"] = "analyst"
    assert c.delete("/api/auth/users/u_a").status_code == 403
    assert c.delete("/api/studies/study_f1").status_code == 403


def test_a_reading_decision_survives_reading_the_document_again(client):  # noqa: F811
    from api.routes.questionnaire import with_decisions

    c, sf, _ = client
    _make_study(sf)
    saved = c.post("/api/studies/study_f1/questionnaire/decisions",
                   json={"key": "k1", "ask": "Are they in order?", "answer": "Yes"})
    assert saved.status_code == 200
    from api.db import StudyDB

    db = sf()
    meta = db.get(StudyDB, "study_f1").study_metadata
    db.close()
    decoding = {"analyst": {"questions": [{"key": "k1", "ask": "?"}, {"key": "k2", "ask": "?"}]}}
    merged = with_decisions(decoding, meta)
    assert merged["analyst"]["questions"][0]["decided"]["answer"] == "Yes"
    assert "decided" not in merged["analyst"]["questions"][1]


# --- An answer changes the survey ------------------------------------------------

def _read_survey(gender=True):
    from src.engines.questionnaire.note_ledger import ledger
    from src.engines.questionnaire.reading_questions import analyst_reading
    from tests.unit.test_reading_questions import _nested_survey

    survey = _nested_survey("lines", gender=gender)
    survey.metadata["decoding"] = {"analyst": analyst_reading(survey)}
    survey.metadata["note_ledger"] = ledger(survey)
    return survey


def _ask_key(survey, kind):
    asks = survey.metadata["decoding"]["analyst"]["questions"]
    return next(a["key"] for a in asks if a["kind"] == kind)


def _stored(sf):
    from api.db import QuestionnaireDB
    from src.core.survey_definition import SurveyDefinition

    db = sf()
    try:
        return SurveyDefinition.from_json(db.get(QuestionnaireDB, "quest_f1").definition_json)
    finally:
        db.close()


def _answer(c, key, answer):
    return c.post("/api/studies/study_f1/questionnaire/decisions",
                  json={"key": key, "ask": "?", "answer": answer})


def _names(survey):
    from src.renderers.surveyjs_compiler import compile_to_surveyjs

    return {v["name"] for v in compile_to_surveyjs(survey).get("calculatedValues", [])}


def test_keep_both_makes_both_variables_and_is_audited(client):  # noqa: F811
    from api.db import AuditLogDB

    c, sf, _ = client
    survey = _read_survey()
    _make_study(sf)
    _make_questionnaire(sf, survey=survey)
    key = _ask_key(survey, "two_lists")
    assert _names(_stored(sf)) == set()

    first = _answer(c, key, "Keep both").json()
    assert first["effect"]["status"] == "applied"
    assert first["effect"]["made"] == ["HID_AGEBANDS", "HID_AGEBANDS_2"]
    assert {"HID_AGEBANDS", "HID_AGEBANDS_2"} <= _names(_stored(sf))
    n = len(_stored(sf).questions)

    assert _answer(c, key, "Keep both").json()["effect"]["status"] == "already"  # idempotent
    assert len(_stored(sf).questions) == n
    db = sf()
    applied = db.query(AuditLogDB).filter_by(entity="reading_decision", action="applied").all()
    db.close()
    assert len(applied) == 1 and applied[0].actor_id == "u1"
    assert "HID_AGEBANDS" in applied[0].summary


def test_only_the_first_makes_only_the_first(client):  # noqa: F811
    c, sf, _ = client
    survey = _read_survey()
    _make_study(sf)
    _make_questionnaire(sf, survey=survey)
    _answer(c, _ask_key(survey, "two_lists"), "Only the first")
    names = _names(_stored(sf))
    assert "HID_AGEBANDS" in names and "HID_AGEBANDS_2" not in names


def test_reading_the_document_again_keeps_the_answer(client):  # noqa: F811
    from api.db import QuestionnaireDB, get_study
    from api.routes.questionnaire import _persist_reread

    c, sf, _ = client
    survey = _read_survey()
    _make_study(sf)
    _make_questionnaire(sf, survey=survey)
    _answer(c, _ask_key(survey, "two_lists"), "Keep both")
    db = sf()
    row = db.get(QuestionnaireDB, "quest_f1")
    assert get_study(db, "study_f1").study_metadata["reading_decisions"]
    fresh = _read_survey()  # what a re-read produces: the variables are gone
    assert _names(fresh) == set()
    _persist_reread(db, row, fresh, [])
    db.commit()
    db.close()
    assert {"HID_AGEBANDS", "HID_AGEBANDS_2"} <= _names(_stored(sf))


def test_a_live_survey_is_not_changed_by_an_answer(client):  # noqa: F811
    c, sf, _ = client
    survey = _read_survey()
    _make_study(sf)
    _make_questionnaire(sf, survey=survey)
    assert c.post("/api/studies/study_f1/fielding/links").status_code == 200
    key = _ask_key(survey, "two_lists")
    out = _answer(c, key, "Keep both").json()
    assert "live" in out["message"] and out["effect"] is None
    assert _names(_stored(sf)) == set()
    db = sf()
    from api.db import get_study

    assert get_study(db, "study_f1").study_metadata["reading_decisions"][key]["answer"]
    db.close()


def test_an_answer_to_a_question_that_is_gone_changes_nothing(client):  # noqa: F811
    c, sf, _ = client
    survey = _read_survey()
    _make_study(sf)
    _make_questionnaire(sf, survey=survey)
    out = _answer(c, "notakey123456", "Keep both").json()
    assert out["effect"]["status"] == "stale"
    assert len(_stored(sf).questions) == len(survey.questions)


def test_leave_for_the_scripter_only_marks_the_ledger(client):  # noqa: F811
    c, sf, _ = client
    survey = _read_survey(gender=False)
    _make_study(sf)
    _make_questionnaire(sf, survey=survey)
    key = _ask_key(survey, "variable")
    before = [r for r in survey.metadata["note_ledger"]["records"] if r["kind"] == "hidden_var"]
    assert any(r["outcome"] == "asked" for r in before)
    assert _answer(c, key, "Leave for the scripter").json()["effect"]["status"] == "applied"
    after = _stored(sf)
    ledger = after.metadata["note_ledger"]
    assert any(r["outcome"] == "scripter" and r["kind"] == "hidden_var"
               for r in ledger["records"])
    assert any(ch["kind"] == "hidden_var" for ch in ledger["checks"])
    assert len(after.questions) == len(survey.questions)
    assert _answer(c, key, "Leave for the scripter").json()["effect"]["status"] == "already"
    other = _answer(c, key, "I will write the rule").json()["effect"]
    assert other["status"] == "not_applied"

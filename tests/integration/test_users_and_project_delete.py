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

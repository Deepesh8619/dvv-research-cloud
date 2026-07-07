"""POST /auth/emergency-reset — recovering a locked-out admin account.

Deliberately unauthenticated (that's the point of an emergency reset), so
these tests exercise the token/email gating directly rather than an auth
dependency override. api/routes/auth.py imports ADMIN_EMAIL/ADMIN_RESET_TOKEN
by name at module load, so tests patch api.routes.auth's bound names, not
api.config's — patching the source module alone wouldn't affect the already-
imported reference.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import api.routes.auth as auth_routes
from api.auth import hash_password, verify_password
from api.db import Base, UserDB, create_user, get_db
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

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(auth_routes, "ADMIN_EMAIL", "owner@example.com")
    monkeypatch.setattr(auth_routes, "ADMIN_RESET_TOKEN", "test-reset-token")
    yield TestClient(app), TestSessionLocal
    app.dependency_overrides.clear()


def test_reset_updates_existing_admin_password(client):
    test_client, session_factory = client
    db = session_factory()
    create_user(db, email="owner@example.com", name="Owner", password_hash=hash_password("old-pw"))
    db.close()

    resp = test_client.post(
        "/api/auth/emergency-reset",
        json={"email": "owner@example.com", "token": "test-reset-token", "new_password": "new-pw"},
    )
    assert resp.status_code == 200

    db = session_factory()
    user = db.query(UserDB).filter(UserDB.email == "owner@example.com").first()
    assert verify_password("new-pw", user.password_hash)
    assert not verify_password("old-pw", user.password_hash)
    db.close()


def test_reset_creates_admin_if_missing(client):
    test_client, session_factory = client
    resp = test_client.post(
        "/api/auth/emergency-reset",
        json={"email": "owner@example.com", "token": "test-reset-token", "new_password": "new-pw"},
    )
    assert resp.status_code == 200

    db = session_factory()
    user = db.query(UserDB).filter(UserDB.email == "owner@example.com").first()
    assert user is not None
    assert verify_password("new-pw", user.password_hash)
    db.close()


def test_reset_rejects_wrong_token(client):
    test_client, _ = client
    resp = test_client.post(
        "/api/auth/emergency-reset",
        json={"email": "owner@example.com", "token": "wrong-token", "new_password": "new-pw"},
    )
    assert resp.status_code == 403


def test_reset_rejects_non_admin_email(client):
    """Even with the correct token, only ADMIN_EMAIL's account is resettable
    — an attacker who guessed the token still can't take over other users."""
    test_client, session_factory = client
    db = session_factory()
    create_user(
        db, email="someone-else@example.com", name="Someone", password_hash=hash_password("pw")
    )
    db.close()

    resp = test_client.post(
        "/api/auth/emergency-reset",
        json={
            "email": "someone-else@example.com",
            "token": "test-reset-token",
            "new_password": "new-pw",
        },
    )
    assert resp.status_code == 403

    db = session_factory()
    user = db.query(UserDB).filter(UserDB.email == "someone-else@example.com").first()
    assert verify_password("pw", user.password_hash)  # unchanged
    db.close()


def test_reset_disabled_when_token_not_configured(client, monkeypatch):
    """Empty ADMIN_RESET_TOKEN (the default) must 404 — this is not an
    always-on backdoor, only active once explicitly configured."""
    test_client, _ = client
    monkeypatch.setattr(auth_routes, "ADMIN_RESET_TOKEN", "")

    resp = test_client.post(
        "/api/auth/emergency-reset",
        json={"email": "owner@example.com", "token": "anything", "new_password": "new-pw"},
    )
    assert resp.status_code == 404

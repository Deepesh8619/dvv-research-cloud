"""An internet-facing login must not allow unlimited password guessing."""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import api.routes.auth as auth_routes
from api.auth import hash_password
from api.db import Base, UserDB, get_db
from api.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine)
    db = factory()
    db.add(UserDB(id="u1", email="ana@agency.test", name="Ana", role="analyst", is_active=True,
                  created_at=datetime.now().isoformat(), password_hash=hash_password("right-pw")))
    db.commit()
    db.close()

    def override():
        s = factory()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override
    monkeypatch.setattr(auth_routes, "_failures", {})
    clock = {"t": 1000.0}
    monkeypatch.setattr(auth_routes.time, "monotonic", lambda: clock["t"])
    yield TestClient(app), clock
    app.dependency_overrides.clear()


def _login(c, pw, email="ana@agency.test"):
    return c.post("/api/auth/login", data={"username": email, "password": pw})


def test_five_wrong_passwords_lock_the_account(client):
    c, _ = client
    for _ in range(5):
        assert _login(c, "wrong").status_code == 401
    locked = _login(c, "right-pw")  # even the RIGHT password is refused while locked
    assert locked.status_code == 429
    assert "Too many failed sign-ins" in locked.json()["detail"]
    assert int(locked.headers["Retry-After"]) > 0


def test_the_lock_expires_and_a_success_clears_the_count(client):
    c, clock = client
    for _ in range(5):
        _login(c, "wrong")
    clock["t"] += 61
    assert _login(c, "right-pw").status_code == 200
    for _ in range(4):  # count was cleared: four more misses don't lock
        _login(c, "wrong")
    assert _login(c, "right-pw").status_code == 200


def test_repeat_offences_lock_for_longer(client):
    c, clock = client
    for _ in range(5):
        _login(c, "wrong")
    clock["t"] += 61
    _login(c, "wrong")  # sixth failure
    clock["t"] += 61  # one minute is no longer enough
    assert _login(c, "right-pw").status_code == 429
    clock["t"] += 60
    assert _login(c, "right-pw").status_code == 200


def test_spraying_many_accounts_from_one_address_is_stopped(client):
    c, _ = client
    for i in range(30):
        _login(c, "guess", email=f"user{i}@agency.test")
    assert _login(c, "right-pw").status_code == 429


def test_other_people_are_unaffected_by_one_locked_account(client):
    c, _ = client
    for _ in range(5):
        _login(c, "wrong", email="someone-else@agency.test")
    assert _login(c, "right-pw").status_code == 200

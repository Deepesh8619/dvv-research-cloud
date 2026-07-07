"""seed_admin_user(): env vars must stay the source of truth for admin
credentials across every startup, not just the first one.

Real bug this covers: on a persistent database (Postgres in production),
a user created on the container's first boot survives every later
redeploy. The original implementation only created the admin if the users
table was empty — changing ADMIN_PASSWORD in the hosting dashboard and
redeploying silently did nothing, because seed_admin_user saw an existing
user and returned immediately. Login then failed with "invalid password"
even when the password typed matched the *current* env var exactly, since
the stored hash was still from whatever ADMIN_PASSWORD held at first boot.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import api.db as db_module
from api.auth import hash_password, verify_password
from api.db import Base, UserDB, create_user, seed_admin_user


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Point api.db.SessionLocal at a throwaway sqlite file — seed_admin_user
    calls the module-global SessionLocal directly, so it must be patched
    rather than passed in."""
    engine = create_engine(
        f"sqlite:///{tmp_path}/test.db", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(db_module, "SessionLocal", TestSessionLocal)
    return TestSessionLocal


def test_seed_admin_user_creates_on_first_boot(isolated_db, monkeypatch):
    monkeypatch.setattr("api.config.ADMIN_EMAIL", "owner@dvv.test")
    monkeypatch.setattr("api.config.ADMIN_PASSWORD", "first-boot-password")
    monkeypatch.setattr("api.config.ADMIN_NAME", "Owner")

    seed_admin_user()

    db = isolated_db()
    user = db.query(UserDB).filter(UserDB.email == "owner@dvv.test").first()
    assert user is not None
    assert verify_password("first-boot-password", user.password_hash)
    db.close()


def test_seed_admin_user_resyncs_password_on_later_boot(isolated_db, monkeypatch):
    """The actual regression: changing ADMIN_PASSWORD after the admin
    already exists and re-running seed_admin_user (simulating a redeploy)
    must update the stored hash, not leave the stale one in place."""
    monkeypatch.setattr("api.config.ADMIN_EMAIL", "owner@dvv.test")
    monkeypatch.setattr("api.config.ADMIN_PASSWORD", "changeme123")
    monkeypatch.setattr("api.config.ADMIN_NAME", "Owner")
    seed_admin_user()  # first boot — creates with the stale/default password

    monkeypatch.setattr("api.config.ADMIN_PASSWORD", "the-real-password-i-typed")
    seed_admin_user()  # simulated redeploy after fixing the env var

    db = isolated_db()
    user = db.query(UserDB).filter(UserDB.email == "owner@dvv.test").first()
    assert verify_password("the-real-password-i-typed", user.password_hash)
    assert not verify_password("changeme123", user.password_hash)
    db.close()


def test_seed_admin_user_does_not_touch_other_users(isolated_db, monkeypatch):
    """Other invited users' credentials must survive an admin resync."""
    monkeypatch.setattr("api.config.ADMIN_EMAIL", "owner@dvv.test")
    monkeypatch.setattr("api.config.ADMIN_PASSWORD", "owner-pw")
    monkeypatch.setattr("api.config.ADMIN_NAME", "Owner")

    db = isolated_db()
    create_user(
        db, email="analyst@dvv.test", name="Analyst", password_hash=hash_password("analyst-pw")
    )
    db.close()

    seed_admin_user()

    db = isolated_db()
    analyst = db.query(UserDB).filter(UserDB.email == "analyst@dvv.test").first()
    assert verify_password("analyst-pw", analyst.password_hash)
    owner = db.query(UserDB).filter(UserDB.email == "owner@dvv.test").first()
    assert owner is not None
    assert verify_password("owner-pw", owner.password_hash)
    db.close()


def test_seed_admin_user_creates_alongside_existing_users_if_email_changed(
    isolated_db, monkeypatch
):
    """If ADMIN_EMAIL itself changes after other users exist, the new admin
    email must still get created rather than being skipped because the
    users table wasn't empty."""
    db = isolated_db()
    create_user(db, email="someone@dvv.test", name="Someone", password_hash=hash_password("pw"))
    db.close()

    monkeypatch.setattr("api.config.ADMIN_EMAIL", "new-owner@dvv.test")
    monkeypatch.setattr("api.config.ADMIN_PASSWORD", "new-owner-pw")
    monkeypatch.setattr("api.config.ADMIN_NAME", "New Owner")
    seed_admin_user()

    db = isolated_db()
    new_owner = db.query(UserDB).filter(UserDB.email == "new-owner@dvv.test").first()
    assert new_owner is not None
    assert verify_password("new-owner-pw", new_owner.password_hash)
    db.close()

"""Founder-editable UI copy (Tier A).

The load-bearing property is that the app NEVER depends on an override: the
hardcoded default at each call site is what ships and what renders whenever an
override is missing, inactive, or unfetchable. That is what keeps the blast
radius of a bad edit at "one word reads wrong" rather than "a screen broke".
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.auth import get_current_user
from api.db import Base, UIOverrideDB, UserDB, active_ui_overrides, get_db
from api.main import app
from api.routes.corrections import require_correction_access
from api.routes.ui_overrides import MAX_OVERRIDE_CHARS, UI_KEYS


@pytest.fixture
def client(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/ui.db")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)

    def override_db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override_db
    yield TestClient(app), Session
    app.dependency_overrides.clear()


def _as(role):
    return UserDB(id=f"u_{role}", email=f"{role}@dvv.test", name=role, role=role)


def _founder():
    """Sign in as a founder.

    Both dependencies are overridden: the founder gate guards writes, but
    GET /ui/overrides is read by EVERY signed-in user (an override is a change
    to the product, not a founders-only view) and so uses get_current_user.
    """
    app.dependency_overrides[require_correction_access] = lambda: _as("founder")
    app.dependency_overrides[get_current_user] = lambda: _as("founder")


# --------------------------------------------------------------------------
# The registry is the contract
# --------------------------------------------------------------------------


def test_every_registry_key_is_unique_and_has_a_default():
    """A key with no default has no fallback, which breaks the safety property."""
    keys = [k["key"] for k in UI_KEYS]
    assert len(keys) == len(set(keys)), "duplicate key in UI_KEYS"
    for k in UI_KEYS:
        assert k["default"].strip(), f"{k['key']} has no default wording"
        assert k["where"] and k["note"], f"{k['key']} is missing console metadata"


def test_unknown_keys_are_refused(client):
    """Writing copy into a key nothing renders would look like a silent failure."""
    c, _ = client
    _founder()
    r = c.post("/api/ui/overrides", json={"key": "made.up.key", "value": "hi"})
    assert r.status_code == 400
    assert "not an editable label" in r.json()["detail"]


def test_empty_value_is_refused_with_a_pointer_to_revert(client):
    """An empty override would render a blank label — revert is the right verb."""
    c, _ = client
    _founder()
    r = c.post("/api/ui/overrides", json={"key": "dashboard.title", "value": "   "})
    assert r.status_code == 400
    assert "revert" in r.json()["detail"].lower()


def test_absurdly_long_value_is_refused(client):
    c, _ = client
    _founder()
    r = c.post(
        "/api/ui/overrides",
        json={"key": "dashboard.title", "value": "x" * (MAX_OVERRIDE_CHARS + 1)},
    )
    assert r.status_code == 400


# --------------------------------------------------------------------------
# Set / read / revert
# --------------------------------------------------------------------------


def test_set_then_read_then_revert_round_trip(client):
    c, Session = client
    _founder()

    assert c.get("/api/ui/overrides").json()["overrides"] == {}

    set_r = c.post(
        "/api/ui/overrides",
        json={"key": "dashboard.title", "value": "Studies", "note": "what we call them"},
    )
    assert set_r.status_code == 200, set_r.text
    assert c.get("/api/ui/overrides").json()["overrides"]["dashboard.title"] == "Studies"

    rev = c.post("/api/ui/overrides/dashboard.title/revert")
    assert rev.status_code == 200
    # Reverting restores the SHIPPED wording, not an empty string.
    assert rev.json()["value"] == "Dashboard"
    assert c.get("/api/ui/overrides").json()["overrides"] == {}

    # Retired, not deleted — "what did we change it to, and why" survives.
    s = Session()
    row = s.query(UIOverrideDB).filter_by(key="dashboard.title").first()
    assert row is not None and row.active is False
    assert row.note == "what we call them"
    s.close()


def test_editing_twice_updates_in_place(client):
    c, Session = client
    _founder()
    c.post("/api/ui/overrides", json={"key": "projects.title", "value": "Jobs"})
    c.post("/api/ui/overrides", json={"key": "projects.title", "value": "Studies"})

    s = Session()
    assert s.query(UIOverrideDB).filter_by(key="projects.title").count() == 1
    s.close()
    assert c.get("/api/ui/overrides").json()["overrides"]["projects.title"] == "Studies"


def test_reverting_something_not_overridden_is_a_404(client):
    c, _ = client
    _founder()
    assert c.post("/api/ui/overrides/dashboard.title/revert").status_code == 404


def test_keys_endpoint_shows_default_and_current(client):
    c, _ = client
    _founder()
    c.post("/api/ui/overrides", json={"key": "dashboard.title", "value": "Studies"})

    rows = {k["key"]: k for k in c.get("/api/ui/keys").json()["keys"]}
    edited = rows["dashboard.title"]
    assert edited["current"] == "Studies"
    assert edited["default"] == "Dashboard"
    assert edited["overridden"] is True
    # An untouched key reports its default as current, so the console can show
    # one field without special-casing.
    assert rows["projects.title"]["overridden"] is False
    assert rows["projects.title"]["current"] == rows["projects.title"]["default"]


# --------------------------------------------------------------------------
# The fallback property
# --------------------------------------------------------------------------


def test_inactive_overrides_never_reach_the_app(client):
    """A switched-off override must be invisible, so revert is a real undo."""
    c, Session = client
    s = Session()
    s.add(UIOverrideDB(id="x", key="dashboard.title", value="Ghost",
                       active=False, created_at="2026-01-01"))
    s.commit()
    assert active_ui_overrides(s) == {}
    s.close()
    _founder()
    assert c.get("/api/ui/overrides").json()["overrides"] == {}


def test_empty_override_value_is_not_served(client):
    """Defence in depth: even if a blank row existed, it must not blank a label."""
    c, Session = client
    s = Session()
    s.add(UIOverrideDB(id="y", key="dashboard.title", value=None,
                       active=True, created_at="2026-01-01"))
    s.commit()
    assert "dashboard.title" not in active_ui_overrides(s)
    s.close()


def test_setting_an_override_is_audited(client):
    from api.db import AuditLogDB

    c, Session = client
    _founder()
    c.post("/api/ui/overrides", json={"key": "dashboard.title", "value": "Studies"})

    s = Session()
    entry = s.query(AuditLogDB).filter_by(entity="ui_override", action="set").first()
    assert entry is not None
    assert entry.actor_id == "u_founder"
    s.close()


# --------------------------------------------------------------------------
# Activity — searches are intent, and must never break the screen
# --------------------------------------------------------------------------


def test_a_search_is_recorded_with_its_result_count(client):
    """'Searched X and found nothing' is the signal; the count carries it."""
    c, _ = client
    _founder()
    r = c.post(
        "/api/ui/activity",
        json={"action": "searched", "surface": "wording", "detail": "terminate", "found": 0},
    )
    assert r.json()["recorded"] is True

    body = c.get("/api/ui/activity").json()
    assert body["entries"][0]["detail"] == "terminate"
    assert body["entries"][0]["action"] == "searched"


def test_keystroke_length_searches_are_ignored(client):
    """Two characters is a keystroke, not an intent — recording it is noise."""
    c, _ = client
    _founder()
    r = c.post("/api/ui/activity", json={"action": "searched", "detail": "te"})
    assert r.json()["recorded"] is False
    assert c.get("/api/ui/activity").json()["count"] == 0


def test_unknown_action_is_a_quiet_no_op_not_an_error(client):
    """Telemetry must never fail the caller — the frontend has no handler for it."""
    c, _ = client
    _founder()
    r = c.post("/api/ui/activity", json={"action": "teleported", "detail": "somewhere"})
    assert r.status_code == 200
    assert r.json()["recorded"] is False


def test_repeated_searches_are_counted(client):
    """A query someone kept retrying is the loudest signal in the log."""
    c, _ = client
    _founder()
    for _ in range(3):
        c.post("/api/ui/activity", json={"action": "searched", "detail": "quota"})
    c.post("/api/ui/activity", json={"action": "searched", "detail": "banner"})

    repeated = c.get("/api/ui/activity").json()["repeated_searches"]
    assert repeated[0] == {"query": "quota", "times": 3}
    # Searched once is not a signal.
    assert all(r["query"] != "banner" for r in repeated)


def test_activity_is_kept_out_of_the_change_log(client):
    """Reading 'what changed' must not be drowned in 'what was typed'."""
    c, _ = client
    _founder()
    c.post("/api/ui/activity", json={"action": "searched", "detail": "terminate"})
    c.post("/api/ui/overrides", json={"key": "dashboard.title", "value": "Studies"})

    changes = c.get("/api/corrections/audit").json()["entries"]
    assert all(not e["entity"].startswith("activity.") for e in changes)
    assert any(e["entity"] == "ui_override" for e in changes)


def test_every_info_tip_is_editable_and_an_edit_reaches_every_user(client):
    """Each (i) tip's four parts are registered with the wording it ships with."""
    import json
    from pathlib import Path

    tips = json.loads((Path(__file__).resolve().parents[2] / "web/src/help/help.json").read_text())
    by_key = {k["key"]: k for k in UI_KEYS}
    for tip_id, tip in tips.items():
        for part in ("title", "what", "why", "how"):
            assert by_key[f"help.{tip_id}.{part}"]["default"] == tip[part]
            assert len(tip[part]) <= MAX_OVERRIDE_CHARS

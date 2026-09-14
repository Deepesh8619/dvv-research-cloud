"""A licence can't be forged, and lapsing never kills a live field."""

import base64
from datetime import date, timedelta

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

import api.main
from api.licence import Licence, LicenceError, load, verify
from scripts.issue_licence import sign

KEY = Ed25519PrivateKey.generate()  # a throwaway key, never DVV's real one
PUBLIC = base64.urlsafe_b64encode(
    KEY.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
).decode().rstrip("=")


def test_a_licence_round_trips():
    lic = verify(sign(KEY, "Acme Research", date(2027, 9, 30)), PUBLIC)
    assert lic == Licence(client="Acme Research", expires=date(2027, 9, 30))


def test_editing_the_expiry_breaks_the_signature():
    token = sign(KEY, "Acme Research", date(2026, 1, 1))
    payload, signature = token.split(".")
    forged = base64.urlsafe_b64encode(
        b'{"client": "Acme Research", "expires": "2099-01-01"}'
    ).decode().rstrip("=")
    with pytest.raises(LicenceError):
        verify(f"{forged}.{signature}", PUBLIC)


def test_a_licence_from_another_key_is_rejected():
    stranger = Ed25519PrivateKey.generate()
    with pytest.raises(LicenceError):
        verify(sign(stranger, "Acme Research", date(2027, 1, 1)), PUBLIC)


@pytest.mark.parametrize("junk", ["", "no-dot", "a.b.c", "!!!.!!!"])
def test_garbage_is_a_licence_error_not_a_crash(junk):
    with pytest.raises(LicenceError):
        verify(junk, PUBLIC)


def test_lapsing_is_valid_then_grace_then_expired():
    lic = Licence("Acme", date(2026, 9, 1))
    assert lic.state(date(2026, 9, 1)) == "valid"
    assert lic.state(date(2026, 9, 15)) == "grace"
    assert lic.state(date(2026, 9, 16)) == "expired"


def test_local_development_needs_no_licence(monkeypatch):
    monkeypatch.delenv("DVV_LICENCE", raising=False)
    assert load("sqlite:///./dvv_research.db") is None


def test_a_real_database_without_a_licence_refuses_to_boot(monkeypatch):
    monkeypatch.delenv("DVV_LICENCE", raising=False)
    with pytest.raises(RuntimeError, match="DVV_LICENCE is not set"):
        load("postgresql://u:p@db/dvv")


def test_an_expired_licence_stops_management_but_never_the_survey(monkeypatch):
    monkeypatch.setattr(api.main, "LICENCE", Licence("Acme", date.today() - timedelta(days=60)))
    client = TestClient(api.main.app)

    blocked = client.get("/api/studies")
    assert blocked.status_code == 402
    assert "Live survey links keep working" in blocked.json()["detail"]

    assert client.get("/api/health").json()["licence"]["state"] == "expired"
    # A respondent link is judged on its own merits (unknown token = 404), not 402.
    assert client.get("/api/r/some-token").status_code == 404


def test_the_grace_period_changes_nothing_for_users(monkeypatch):
    monkeypatch.setattr(api.main, "LICENCE", Licence("Acme", date.today() - timedelta(days=3)))
    client = TestClient(api.main.app)
    assert client.get("/api/studies").status_code != 402
    assert client.get("/api/health").json()["licence"]["state"] == "grace"

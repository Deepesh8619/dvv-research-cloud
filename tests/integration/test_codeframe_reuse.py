"""Codeframe reuse over HTTP.

Reuse exists for consistency as much as cost: wave 2 coded with wave 1's
codeframe can actually be compared to wave 1. The tests that matter most are
the ones proving nothing is applied without a person choosing it.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.auth import get_current_user
from api.db import Base, CodeframeLibraryDB, UserDB, get_db
from api.main import app

CODEFRAME = {"codes": [{"id": "PRICE", "label": "Price"},
                       {"id": "TASTE", "label": "Taste"}]}


@pytest.fixture
def client(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/cf.db")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)

    def override_db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    user = UserDB(id="u1", email="a@dvv.test", name="A", role="analyst")
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    yield TestClient(app), Session
    app.dependency_overrides.clear()


def _save(c, question, client_name=None):
    return c.post("/api/codeframes", json={
        "question_text": question, "codeframe": CODEFRAME,
        "study_id": "s1", "client_name": client_name,
    })


def test_the_tracker_case_wave_two_finds_wave_ones_codeframe(client):
    """The whole point: the same question next wave reuses the same codes."""
    c, _ = client
    _save(c, "Q4. Why do you use that brand most often?")

    body = c.get("/api/codeframes/suggest",
                 params={"question": "Why do you use that brand most often? (OPEN)"}).json()
    assert body["count"] == 1
    top = body["matches"][0]
    assert top["exact"] is True
    assert "waves comparable" in top["reason"]


def test_an_unrelated_question_gets_no_suggestion(client):
    """Silence means 'generate a new one' — the safe default."""
    c, _ = client
    _save(c, "Why do you use that brand most often?")
    body = c.get("/api/codeframes/suggest",
                 params={"question": "What is your postcode?"}).json()
    assert body["matches"] == []


def test_suggestions_can_be_scoped_to_one_client(client):
    """The same wording can mean different things to different clients."""
    c, _ = client
    _save(c, "Why do you use that brand?", client_name="GBK")
    _save(c, "Why do you use that brand?", client_name="Other")

    scoped = c.get("/api/codeframes/suggest",
                   params={"question": "Why do you use that brand?",
                           "client_name": "GBK"}).json()
    assert scoped["count"] == 1


def test_the_full_codeframe_is_retrievable_for_review(client):
    """A suggestion is not usable until you can see the codes it carries."""
    c, _ = client
    entry_id = _save(c, "Why that brand?").json()["id"]
    got = c.get(f"/api/codeframes/{entry_id}").json()
    assert got["codeframe"] == CODEFRAME


def test_saving_requires_a_question_and_a_codeframe(client):
    c, _ = client
    assert c.post("/api/codeframes",
                  json={"question_text": "  ", "codeframe": CODEFRAME}).status_code == 400
    assert c.post("/api/codeframes",
                  json={"question_text": "Q?", "codeframe": {}}).status_code == 400


def test_reuse_count_promotes_the_house_standard(client):
    """A codeframe the team keeps choosing should surface first."""
    c, Session = client
    rare = _save(c, "Why do you use that brand?").json()["id"]
    standard = _save(c, "Why do you use that brand?").json()["id"]

    for _ in range(3):
        assert c.post(f"/api/codeframes/{standard}/reuse").status_code == 200

    top = c.get("/api/codeframes/suggest",
                params={"question": "Why do you use that brand?"}).json()["matches"][0]
    assert top["entry_id"] == standard
    assert top["times_reused"] == 3
    assert rare != standard


def test_saving_and_reusing_are_audited(client):
    """Which codeframe was reused where is a data-provenance question."""
    from api.db import AuditLogDB

    c, Session = client
    entry_id = _save(c, "Why that brand?").json()["id"]
    c.post(f"/api/codeframes/{entry_id}/reuse")

    s = Session()
    actions = {e.action for e in s.query(AuditLogDB).filter_by(entity="codeframe").all()}
    assert actions == {"saved", "reused"}
    s.close()


def test_a_corrupt_stored_codeframe_does_not_break_the_endpoint(client):
    """Bad JSON in one row must not take down retrieval."""
    c, Session = client
    s = Session()
    s.add(CodeframeLibraryDB(id="bad", question_text="Q?",
                             codeframe_json="{not json", created_at="2026-01-01"))
    s.commit()
    s.close()
    assert c.get("/api/codeframes/bad").json()["codeframe"] == {}


def test_missing_codeframe_is_a_404(client):
    c, _ = client
    assert c.get("/api/codeframes/nope").status_code == 404
    assert c.post("/api/codeframes/nope/reuse").status_code == 404

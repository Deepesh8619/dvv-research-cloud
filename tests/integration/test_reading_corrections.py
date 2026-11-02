"""C1: the correction store, through the real PATCH route. Made-up lines only."""

import json

import pytest

import api.routes.questionnaire as qroutes
from api.db import AuditLogDB, ReadingCorrectionDB, StudyDB
from scripts.export_corrections import export
from tests.integration.test_questionnaire_routes import (
    _make_study,
    _upload,
    _wait_draft,
    client,  # noqa: F401  (fixture)
)

URL = "/api/studies/study_q1/questionnaire/questions/"


@pytest.fixture
def ready(client):  # noqa: F811
    test_client, sf, state = client
    _make_study(sf)
    _upload(test_client)
    _wait_draft(test_client)
    return test_client, sf, state


def _rows(sf):
    with sf() as db:
        return db.query(ReadingCorrectionDB).all()


def test_edit_makes_one_row_and_second_edit_keeps_before(ready):
    c, sf, _ = ready
    assert c.patch(URL + "Q1", json={"text": "Your age in years?"}).status_code == 200
    (row,) = _rows(sf)
    assert json.loads(row.before)["text"] == "What is your age?"
    assert json.loads(row.after)["text"] == "Your age in years?"
    assert json.loads(row.changed_fields) == ["text"]
    assert row.source_text == "Q1. What is your age?" and row.by_id == "u1"

    c.patch(URL + "Q1", json={"text": "Age?", "required": False})
    (row,) = _rows(sf)
    assert json.loads(row.before)["text"] == "What is your age?"
    assert json.loads(row.after)["text"] == "Age?"
    assert sorted(json.loads(row.changed_fields)) == ["required", "text"]


def test_undoing_deletes_and_no_change_records_nothing(ready):
    c, sf, _ = ready
    c.patch(URL + "Q1", json={"text": "Age?"})
    assert len(_rows(sf)) == 1
    c.patch(URL + "Q1", json={"text": "What is your age?"})
    assert _rows(sf) == []
    c.patch(URL + "Q1", json={"text": "What is your age?"})
    assert _rows(sf) == []


def test_rename_moves_the_row(ready):
    c, sf, _ = ready
    c.patch(URL + "Q1", json={"text": "Age?"})
    assert c.patch(URL + "Q1", json={"qid": "Q9"}).status_code == 200
    (row,) = _rows(sf)
    assert row.qid == "Q9"
    assert "qid" not in json.loads(row.after)


def test_failure_in_record_still_saves(ready, monkeypatch):
    c, sf, _ = ready

    def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(qroutes, "record_correction", boom)
    assert c.patch(URL + "Q1", json={"text": "Age?"}).status_code == 200
    assert c.get(URL + "Q1").json()["text"] == "Age?"
    assert _rows(sf) == []


def test_consent_is_owner_only_and_audited_and_count_works(ready):
    c, sf, state = ready
    c.patch(URL + "Q1", json={"text": "Age?"})
    assert c.get("/api/studies/study_q1/questionnaire/corrections").json() == {
        "count": 1, "allowed": False}
    put = "/api/studies/study_q1/questionnaire/corrections-training"
    state["role"] = "reviewer"
    assert c.put(put, json={"allowed": True}).status_code == 403
    state["role"] = "analyst"
    assert c.put(put, json={"allowed": True}).status_code == 403
    assert c.get("/api/studies/study_q1/questionnaire/corrections").status_code == 200
    state["role"] = "owner"
    assert c.put(put, json={"allowed": True}).status_code == 200
    assert c.get("/api/studies/study_q1/questionnaire/corrections").json()["allowed"] is True
    with sf() as db:
        assert db.query(AuditLogDB).filter_by(action="corrections_training").count() == 1


def test_export_only_consented_masked_and_not_deleted(ready, tmp_path):
    c, sf, _ = ready
    c.patch(URL + "Q1", json={"text": "Do you like Zorbo juice?"})
    with sf() as db:
        db.add(StudyDB(id="other", client_name="Plain", status="draft", study_metadata={}))
        db.add(StudyDB(id="gone", client_name="Gone", status="archived",
                       study_metadata={"corrections_for_training": True}))
        for sid in ("other", "gone"):
            db.add(ReadingCorrectionDB(id=f"rc_{sid}", study_id=sid, qid="Q1", source_text="s",
                                       before="{}", after='{"text": "x"}'))
        db.commit()
        out = str(tmp_path / "o.jsonl")
        assert export(db, out, ["zorbo"])["rows_written"] == 0  # no consent yet
        study = db.get(StudyDB, "study_q1")
        study.study_metadata = {"corrections_for_training": True}
        db.commit()
        stats = export(db, out, ["zorbo"])
    lines = open(out).read().splitlines()
    assert stats["rows_written"] == 1 and stats["terms_masked"] >= 1
    row = json.loads(lines[0])
    assert "Zorbo" not in lines[0] and "[MASKED] juice" in row["output"]["text"]
    assert row["input"] == "Q1. What is your age?" and row["before"]["text"]

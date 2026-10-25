"""The study ledger: a hash chain over the audit log, and issued-file lookup."""

import os
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import api.routes.materialize as mat
from api.auth import get_current_user
from api.db import AuditLogDB, Base, LedgerHeadDB, UserDB, create_study, get_db, record_audit
from api.main import app
from src.core.survey_definition import Block, Option, Question, QuestionType, SurveyDefinition
from src.engines.fielding.materialize import STATUS_COMPLETE, materialize_fielding
from src.platform import ledger


@pytest.fixture
def factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture
def db(factory):
    with factory() as s:
        yield s


def _write(db, n, study="s1"):
    for i in range(n):
        record_audit(db, entity="correction", entity_id=f"c{i}", action="created",
                     actor_id="u1", study_id=study, summary=f"step {i}", after={"i": i})
        db.commit()


def test_a_chain_of_20_verifies(db):
    _write(db, 20)
    v = ledger.verify(db, "s1")
    assert v["ok"] and v["n"] == 20 and v["first_bad_seq"] is None and len(v["head"]) == 64
    assert "unchained" not in v


def test_studies_chain_separately_and_global_has_its_own(db):
    _write(db, 3, "a")
    _write(db, 2, "b")
    record_audit(db, entity="x", entity_id="1", action="y", actor_id="u")
    db.commit()
    assert ledger.verify(db, "a")["n"] == 3 and ledger.verify(db, "b")["n"] == 2
    assert ledger.verify(db, None)["n"] == 1


def test_editing_one_entry_is_reported_at_its_seq(db):
    _write(db, 10)
    row = db.query(AuditLogDB).filter(AuditLogDB.seq == 4).one()
    row.summary = "rewritten"
    db.commit()
    v = ledger.verify(db, "s1")
    assert not v["ok"] and v["first_bad_seq"] == 4


def test_deleting_the_last_entry_is_noticed(db):
    _write(db, 5)
    db.query(AuditLogDB).filter(AuditLogDB.seq == 5).delete()
    db.commit()
    v = ledger.verify(db, "s1")
    assert not v["ok"] and v["first_bad_seq"] == 5


def test_entries_from_before_the_chain_are_unchained_not_tampering(db):
    _write(db, 2)
    db.add(AuditLogDB(id="old", entity="e", entity_id="1", action="a", actor_id="u",
                      study_id="s1", at="2020-01-01"))
    db.commit()
    v = ledger.verify(db, "s1")
    assert v["ok"] and v["n"] == 2 and v["unchained"] == 1


def test_record_audit_still_never_raises(db, monkeypatch):
    monkeypatch.setattr(ledger, "chain", lambda *a: 1 / 0)
    _write(db, 1)  # the line is kept, unchained
    assert db.query(AuditLogDB).one().seq is None


# --- files ---

def _survey():
    q = Question(qid="Q1", text="Coffee?", type=QuestionType.SINGLE,
                 options=[Option(code=1, label="Yes"), Option(code=2, label="No")])
    return SurveyDefinition(study_id="s1", title="T", questions=[q],
                            blocks=[Block(block_id="B1", question_ids=["Q1"])])


@pytest.fixture
def client(factory, monkeypatch, tmp_path):
    def override():
        s = factory()
        try:
            yield s
        finally:
            s.close()

    monkeypatch.setattr(mat, "STUDIES_DIR", tmp_path)
    app.dependency_overrides[get_db] = override
    app.dependency_overrides[get_current_user] = lambda: UserDB(
        id="u1", email="o@x.com", name="Owner", role="owner", is_active=True)
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_a_file_written_by_materialize_is_found_and_another_is_not(client, factory, tmp_path):
    sessions = [SimpleNamespace(
        id="x1", status_code=STATUS_COMPLETE, started_at="2026-01-01T00:00:00",
        completed_at="2026-01-01T00:05:00", entry_params=None)]
    datamap, df = materialize_fielding(_survey(), sessions, {"x1": {"Q1": 1}})
    with factory() as db:
        create_study(db, "s1", "Acme")
        study = mat.get_study(db, "s1")
        run_dir, *_ = mat._write_and_publish(db, study, datamap, df, datamap, df, publish=False)
    written = run_dir / "s1_responses.xlsx"
    assert written.is_file()
    url = "/api/studies/s1/ledger/check-file"
    r = client.post(url, files={"file": ("renamed.xlsx", written.read_bytes())})
    assert r.status_code == 200 and r.json()["issued"] is True
    assert r.json()["name"] == "s1_responses.xlsx"
    assert r.json()["at"]
    other = client.post(url, files={"file": ("a.xlsx", b"not ours")})
    assert other.json() == {"issued": False, "at": None, "name": None}
    assert client.get("/api/studies/s1/ledger/verify").json()["ok"] is True
    assert client.get("/api/studies/nope/ledger/verify").status_code == 404


@pytest.mark.skipif(not os.environ.get("DVV_TEST_PG"), reason="needs DVV_TEST_PG")
def test_concurrent_writers_keep_one_unbroken_chain():
    import threading

    engine = create_engine(os.environ["DVV_TEST_PG"])
    Base.metadata.drop_all(bind=engine, tables=[AuditLogDB.__table__, LedgerHeadDB.__table__])
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine)

    def work(k):
        for i in range(10):
            with factory() as s:
                record_audit(s, entity="e", entity_id=f"{k}-{i}", action="a", actor_id="u",
                             study_id="pg1")
                s.commit()

    threads = [threading.Thread(target=work, args=(k,)) for k in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    with factory() as s:
        v = ledger.verify(s, "pg1")
        assert v["ok"] and v["n"] == 40
    with engine.begin() as c:
        c.execute(text("DELETE FROM audit_log WHERE study_id='pg1'"))
        c.execute(text("DELETE FROM ledger_heads WHERE study_id='pg1'"))

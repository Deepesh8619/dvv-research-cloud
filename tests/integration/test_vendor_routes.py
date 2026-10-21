"""Vendor scorecard route: per-link incidence, cost, and role gate."""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.auth import get_current_user
from api.db import (
    Base,
    FieldingLinkDB,
    RespondentAnswerDB,
    RespondentSessionDB,
    StudyDB,
    UserDB,
    get_db,
)
from api.main import app
from src.core.survey_definition import (
    Block,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
)

SURVEY = SurveyDefinition(
    study_id="s1", title="T", source="authored", status="approved", version=1,
    blocks=[Block(block_id="B1", question_ids=["Q1"])],
    questions=[Question(qid="Q1", text="Coffee?", type=QuestionType.SINGLE,
                        options=[Option(code=1, label="Yes"), Option(code=2, label="No")])],
)


@pytest.fixture
def client(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    state = {"role": "owner"}
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: UserDB(
        id="u1", email="t@x.com", name="T", role=state["role"], is_active=True)
    yield TestClient(app), Session, state
    app.dependency_overrides.clear()


def _seed(Session):
    now = datetime.now().isoformat()
    db = Session()
    db.add(StudyDB(id="s1", client_name="C", status="classifying", created_at=now,
                   updated_at=now, study_metadata={}, approval_chain={}))
    # (link id, cpi, target, test?, statuses): A has 3 complete + 1 terminate.
    for lid, name, cpi, target, test, statuses in [
        ("lA", "Alpha", 2.0, 10, False, [10, 10, 10, 20]),
        ("lB", "Beta", None, None, False, [10, 20, 20, 20]),
        ("lT", "Test", 1.0, 5, True, [10]),
    ]:
        db.add(FieldingLinkDB(
            id=lid, study_id="s1", token=f"tok{lid}", status="open", survey_version=1,
            survey_json=SURVEY.to_json(), created_at=now, is_test=test,
            vendor_name=name, cpi=cpi, target_completes=target))
        for i, code in enumerate(statuses):
            sid = f"{lid}_{i}"
            db.add(RespondentSessionDB(id=sid, fielding_link_id=lid, status_code=code,
                                       entry_params={}, started_at=now, completed_at=now))
            db.add(RespondentAnswerDB(id=f"a{sid}", session_id=sid, qid="Q1", value=1 + i % 2,
                                      answered_at=now))
    db.commit()
    db.close()


def test_scorecard_rows(client):
    c, Session, _ = client
    _seed(Session)
    resp = c.get("/api/studies/s1/fielding/vendors")
    assert resp.status_code == 200
    rows = {r["link_id"]: r for r in resp.json()["vendors"]}
    assert set(rows) == {"lA", "lB"}  # test link excluded
    a, b = rows["lA"], rows["lB"]
    assert (a["starts"], a["completes"], a["incidence_pct"]) == (4, 3, 75.0)
    assert (a["cost_to_date"], a["projected_cost"], a["pct_of_target"]) == (6.0, 20.0, 30.0)
    assert (b["completes"], b["incidence_pct"]) == (1, 25.0)
    assert b["cost_to_date"] is None and b["projected_cost"] is None
    assert b["pct_of_target"] is None


def test_reviewer_refused(client):
    c, Session, state = client
    _seed(Session)
    state["role"] = "reviewer"
    assert c.get("/api/studies/s1/fielding/vendors").status_code == 403


def test_quota_balance_suggests_and_changes_nothing(client):
    from api.db import QuotaCellDB

    c, Session, _ = client
    _seed(Session)
    db = Session()
    for i, (lid, name, n) in enumerate([("lA", "Male", 40), ("lB", "Male", 5), ("lA", "Dup", 1),
                                         ("lA", "Dup", 2), ("lT", "Male", 9)]):
        db.add(QuotaCellDB(id=f"q{i}", fielding_link_id=lid, name=name, condition_json="{}",
                           cell_limit=50, count=n))
    db.commit()
    db.close()
    resp = c.get("/api/studies/s1/fielding/quota-balance?target=100")
    assert resp.status_code == 200
    body = resp.json()
    male = next(x for x in body["cells"] if x["cell"] == "Male")
    a = next(v for v in male["vendors"] if v["link_id"] == "lA")
    assert len(male["vendors"]) == 2 and a["suggested_limit"] > 50  # test link excluded
    assert [x["name"] for x in body["ambiguous"]] == ["Dup"]
    assert "Nothing was changed" in body["note"]
    db = Session()
    assert sorted((q.id, q.cell_limit, q.count) for q in db.query(QuotaCellDB)) == [
        ("q0", 50, 40), ("q1", 50, 5), ("q2", 50, 1), ("q3", 50, 2), ("q4", 50, 9)]
    db.close()


def test_quota_balance_reviewer_refused(client):
    c, Session, state = client
    _seed(Session)
    state["role"] = "reviewer"
    assert c.get("/api/studies/s1/fielding/quota-balance?target=100").status_code == 403


def _counts(Session):
    db = Session()
    n = (db.query(RespondentSessionDB).count(), db.query(RespondentAnswerDB).count(),
         db.query(FieldingLinkDB).count())
    db.close()
    return n


def _post(c, kind, body, lid="lA"):
    return c.post(f"/api/studies/s1/fielding/links/{lid}/{kind}",
                  files={"file": ("c.csv", body, "text/csv")})


def test_invitations_and_reminders(client):
    import csv
    import io
    import os

    from api.config import JWT_SECRET
    from src.engines.fielding.vendors import pid_for

    c, Session, state = client
    _seed(Session)
    secret = os.getenv("DVV_IP_SALT") or JWT_SECRET
    pa, pb = pid_for(secret, "a@x.com"), pid_for(secret, "b@x.com")
    db = Session()
    db.add(RespondentSessionDB(id="sp", fielding_link_id="lA", status_code=10, pid=pa,
                               entry_params={}, started_at="2026-01-01"))
    db.commit()
    db.close()
    before = _counts(Session)
    body = "Email,Name\n A@X.com ,A\nb@x.com,B\n"
    r = _post(c, "invitations", body)
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert r.status_code == 200
    assert [x["url"].split("?")[1] for x in rows] == [f"pid={pa}", f"pid={pb}"]
    assert "/r/toklA?pid=" in rows[0]["url"]
    rem = list(csv.DictReader(io.StringIO(_post(c, "reminders", body).text)))
    assert [x["Name"] for x in rem] == ["B"]
    assert _post(c, "invitations", "foo,bar\n1,2\n").status_code == 422
    assert _post(c, "invitations", body, lid="nope").status_code == 404
    assert _counts(Session) == before
    state["role"] = "reviewer"
    assert _post(c, "reminders", body).status_code == 403


def test_reconciliation_accepts_completes_and_rejects_only_what_a_person_removed(client):
    import csv
    import io

    from api.db import record_audit

    c, Session, _ = client
    _seed(Session)
    db = Session()
    db.get(RespondentSessionDB, "lA_0").pid = "P100"
    db.get(RespondentSessionDB, "lA_1").entry_params = {"rid": "R7"}
    record_audit(db, entity="cleaning.respondent", entity_id="lA_1", action="remove",
                 actor_id="u1", study_id="s1", summary="x",
                 after={"reason_code": "speeder", "note": "4 minutes"})
    db.commit()
    db.close()

    r = c.get("/api/studies/s1/fielding/links/lA/reconciliation.csv")
    assert r.status_code == 200
    assert 'filename="reconciliation_Alpha.csv"' in r.headers["content-disposition"]
    rows = {x["session_id"]: x for x in csv.DictReader(io.StringIO(r.text))}
    assert (rows["lA_0"]["panel_id"], rows["lA_0"]["decision"]) == ("P100", "accepted")
    assert (rows["lA_1"]["panel_id"], rows["lA_1"]["decision"], rows["lA_1"]["reason"]) == (
        "R7", "rejected", "speeder: 4 minutes")
    assert (rows["lA_3"]["outcome"], rows["lA_3"]["decision"]) == ("terminate", "")

    a = {v["link_id"]: v for v in c.get("/api/studies/s1/fielding/vendors").json()["vendors"]}["lA"]
    assert (a["accepted"], a["billable"], a["cpi"], a["target_completes"]) == (2, 4.0, 2.0, 10)
    assert c.get("/api/studies/s1/fielding/links/nope/reconciliation.csv").status_code == 404

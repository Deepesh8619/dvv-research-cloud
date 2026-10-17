"""Cleaning review API: decisions are audited, keep is one-at-a-time, lock is honoured."""

from datetime import datetime

import polars as pl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.auth import get_current_user
from api.db import Base, QuestionnaireDB, StudyDB, UserDB, get_db
from api.main import app
from src.core.models import DataMap, Variable

COLS = [f"Q{i}" for i in range(1, 19)]  # >= MIN_DUP_ANSWERS answered columns


@pytest.fixture
def client(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path}/test.db", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    sf = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        db = sf()
        try:
            yield db
        finally:
            db.close()

    state = {"role": "owner"}
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: UserDB(
        id="u1", email="t@x.com", name="T", role=state["role"], is_active=True
    )
    # s1 and s2 are identical to s0, so the duplicate check flags s1 and s2.
    rows = [{"vSessionId": f"s{i}", **{c: (1 if i < 3 else i) for c in COLS}} for i in range(6)]
    run = tmp_path / "run"
    run.mkdir()
    pl.DataFrame(rows).write_parquet(run / "data.parquet")
    (run / "datamap.json").write_text(
        # The key column stays out of the map: it differs per row and would hide duplicates.
        DataMap([Variable(name=c, label=f"Question {c}", question_ref=c) for c in COLS]).to_json()
    )
    now = datetime.now().isoformat()
    db = sf()
    db.add(StudyDB(
        id="st1", client_name="T", status="qc", created_at=now, updated_at=now,
        excel_path=str(run / "out.xlsx"), study_metadata={}, approval_chain={},
    ))
    db.add(QuestionnaireDB(
        id="q1", study_id="st1", status="approved", source_filename="s.docx",
        definition_json="{}", approved_qids=[], created_at=now, updated_at=now,
    ))
    db.commit()
    db.close()
    yield TestClient(app), sf, state
    app.dependency_overrides.clear()


def _post(c, keys, decision="remove", reason="duplicate", note=None):
    return c.post(
        "/api/studies/st1/cleaning/decisions",
        json={"keys": keys, "decision": decision, "reason_code": reason, "note": note},
    )


def test_remove_is_reviewed_and_audited(client):
    c, _, _ = client
    before = c.get("/api/studies/st1/cleaning").json()
    flagged = sorted(before["flags"])
    assert flagged and before["n_unreviewed_flagged"] == len(flagged)
    assert _post(c, flagged[:2]).status_code == 200
    after = c.get("/api/studies/st1/cleaning").json()
    assert set(after["decisions"]) == set(flagged[:2])
    assert after["n_unreviewed_flagged"] == len(flagged) - 2
    audit = c.get("/api/corrections/audit?study_id=st1").json()["entries"]
    assert sorted(e["entity_id"] for e in audit if e["entity"] == "cleaning.respondent") == sorted(
        flagged[:2]
    )


def test_keep_takes_exactly_one_key(client):
    c, _, _ = client
    assert _post(c, ["s1", "s2"], "keep").status_code == 422
    assert _post(c, ["s1"], "keep").status_code == 200


def test_other_needs_note_and_unknown_key_rejected(client):
    c, _, _ = client
    assert _post(c, ["s1"], reason="other").status_code == 422
    assert _post(c, ["s1"], reason="nonsense").status_code == 422
    assert _post(c, ["nope"]).status_code == 422


def test_locked_is_409(client):
    c, sf, _ = client
    db = sf()
    st = db.get(StudyDB, "st1")
    st.study_metadata = {"cleaning": {"locked": True}}
    db.commit()
    db.close()
    assert _post(c, ["s1"]).status_code == 409


def test_viewer_refused(client):
    c, _, state = client
    state["role"] = "reviewer"
    assert _post(c, ["s1"]).status_code == 403


def _review_all(c):
    flagged = sorted(c.get("/api/studies/st1/cleaning").json()["flags"])
    assert _post(c, flagged).status_code == 200
    return flagged


def test_lock_refused_with_unreviewed_flag(client):
    c, _, _ = client
    r = c.post("/api/studies/st1/cleaning/lock")
    assert r.status_code == 409 and "no decision" in r.json()["detail"]


def test_lock_shrinks_tables_and_writes_marked_sav(client, tmp_path, monkeypatch):
    import json

    import pyreadstat

    import api.routes.cleaning as cl

    monkeypatch.setattr(cl, "STUDIES_DIR", tmp_path / "studies")
    c, _, state = client
    run = tmp_path / "run"
    from src.core.models import VarType
    (run / "datamap.json").write_text(DataMap([
        Variable(name=c_, label=f"Question {c_}", question_ref=c_, type=VarType.SINGLE,
                 values={i: f"Opt {i}" for i in range(6)})
        for c_ in COLS
    ]).to_json())
    flagged = _review_all(c)
    r = c.post("/api/studies/st1/cleaning/lock")
    assert r.status_code == 200, r.text
    v = r.json()
    assert v["n_after"] == v["n_before"] - len(flagged) and set(v["removed"]) == set(flagged)
    assert _post(c, ["s0"]).status_code == 409  # locked
    run = tmp_path / "run"
    assert pl.read_parquet(run / "data.parquet").height == v["n_after"]
    assert pl.read_parquet(run / "data_raw.parquet").height == v["n_before"]
    def bases(o):
        if isinstance(o, dict):
            yield from ([o["base"]] if "base" in o else [])
            for x in o.values():
                yield from bases(x)
        elif isinstance(o, list):
            for x in o:
                yield from bases(x)

    got = list(bases(json.loads((run / "crosstabs.json").read_text())))
    assert got and max(got) == v["n_after"]  # no base still counts a removed respondent
    df, _meta = pyreadstat.read_sav(str(tmp_path / "studies/st1/st1_responses.sav"))
    assert len(df) == v["n_before"]
    marked = df.loc[df["vCleanStatus"] == 2, "vSessionId"].tolist()
    assert sorted(marked) == flagged
    # unlock: owner + note only
    assert c.post("/api/studies/st1/cleaning/unlock", json={"note": " "}).status_code == 422
    state["role"] = "analyst"
    assert c.post("/api/studies/st1/cleaning/unlock", json={"note": "x"}).status_code == 403
    state["role"] = "owner"
    assert c.post("/api/studies/st1/cleaning/unlock", json={"note": "redo"}).status_code == 200
    assert c.get("/api/studies/st1/cleaning").json()["locked"] is False
    assert (run / "data_raw.parquet").exists()


def _put_rules(c, rules):
    return c.put("/api/studies/st1/cleaning/rules", json={"rules": rules})


def test_rules_rejected_when_invalid(client):
    c, _, _ = client
    r = _put_rules(c, [{"kind": "trap", "variable": "Nope", "pass_codes": [1]}])
    assert r.status_code == 422 and "Nope" in r.json()["detail"][0]
    r = _put_rules(c, [{"kind": "range", "variable": "Q1"}])
    assert r.status_code == 422 and "min or max" in r.json()["detail"][0]
    assert c.get("/api/studies/st1/cleaning/rules").json()["rules"] == []


def test_trap_rule_flags_and_blocks_lock_until_decided(client):
    c, _, _ = client
    before = set(c.get("/api/studies/st1/cleaning").json()["flags"])
    assert _put_rules(c, [{"kind": "trap", "variable": "Q1", "pass_codes": [1]}]).status_code == 200
    got = c.get("/api/studies/st1/cleaning").json()
    trapped = {k for k, f in got["flags"].items() if any(x["check"] == "trap_failed" for x in f)}
    assert trapped == {"s3", "s4", "s5"}
    assert got["n_unreviewed_flagged"] == len(before | trapped)
    assert c.post("/api/studies/st1/cleaning/lock").status_code == 409
    assert c.get("/api/studies/st1/cleaning/rules").json()["rules"][0]["kind"] == "trap"


def test_proposed_rules_are_not_flags(client):
    c, _, _ = client
    r = c.get("/api/studies/st1/cleaning/rules").json()
    assert r["rules"] == [] and isinstance(r["proposed"], list)
    assert not any(
        x["check"] == "out_of_range"
        for f in c.get("/api/studies/st1/cleaning").json()["flags"].values() for x in f
    )


def test_rules_put_refused_when_locked_or_reviewer(client):
    c, sf, state = client
    state["role"] = "reviewer"
    assert _put_rules(c, []).status_code == 403
    state["role"] = "owner"
    from api.db import StudyDB
    db = sf()
    s = db.get(StudyDB, "st1")
    s.study_metadata = {"cleaning": {"locked": True}}
    db.commit()
    db.close()
    assert _put_rules(c, []).status_code == 409


# ---- imputation policy (C9) ----

def _numeric_setup(tmp_path, monkeypatch, routed=False):
    import api.routes.cleaning as cl
    from api.routes import materialize
    from src.core.models import VarType
    from src.core.survey_definition import Expression, Question, SurveyDefinition

    monkeypatch.setattr(cl, "STUDIES_DIR", tmp_path / "studies")
    run = tmp_path / "run"
    rows = [{"vSessionId": f"s{i}", **{c: (1 if i < 3 else i) for c in COLS}} for i in range(6)]
    rows[4]["Q1"] = None  # s4 has a missing Q1
    pl.DataFrame(rows).write_parquet(run / "data.parquet")
    (run / "datamap.json").write_text(DataMap([
        Variable(name=c_, label=c_, question_ref=c_, type=VarType.NUMERIC) for c_ in COLS
    ]).to_json())
    q = Question(qid="Q1", text="q")
    if routed:
        q.show_if = Expression(op="eq", qid="Q2", value=1)
    monkeypatch.setattr(
        materialize, "_approved_survey",
        lambda db, sid: SurveyDefinition(study_id=sid, questions=[q]),
    )
    return run


_IMP = {"variable": "Q1", "method": "median", "target": "missing"}
URL = "/api/studies/st1/cleaning/imputations"


def _meta_and_audit(sf):
    db = sf()
    try:
        from api.db import AuditLogDB
        return (db.get(StudyDB, "st1").study_metadata,
                db.query(AuditLogDB).filter(AuditLogDB.entity == "cleaning.imputation").count())
    finally:
        db.close()


def test_imputation_preview_writes_nothing_and_confirm_required(client, tmp_path, monkeypatch):
    c, sf, _ = client
    _numeric_setup(tmp_path, monkeypatch)
    before = _meta_and_audit(sf)
    r = c.post(URL + "?preview=true", json=_IMP)
    assert r.status_code == 200, r.text
    assert [(x["key"], x["old"], x["new"]) for x in r.json()["cells"]] == [("s4", None, 1)]
    assert _meta_and_audit(sf) == before
    assert c.post(URL + "?preview=false", json=_IMP).status_code == 422
    assert _meta_and_audit(sf) == before


def test_imputation_routed_question_refused(client, tmp_path, monkeypatch):
    c, _, _ = client
    _numeric_setup(tmp_path, monkeypatch, routed=True)
    r = c.post(URL, json=_IMP)
    assert r.status_code == 422 and "skip logic" in r.json()["detail"]


def test_locked_imputation_is_409(client, tmp_path, monkeypatch):
    c, sf, _ = client
    _numeric_setup(tmp_path, monkeypatch)
    db = sf()
    db.get(StudyDB, "st1").study_metadata = {"cleaning": {"locked": True}}
    db.commit()
    db.close()
    assert c.post(URL, json=_IMP).status_code == 409


def test_lock_without_policy_changes_no_values(client, tmp_path, monkeypatch):
    c, _, _ = client
    run = _numeric_setup(tmp_path, monkeypatch)
    _review_all(c)
    assert c.post("/api/studies/st1/cleaning/lock").status_code == 200
    out = pl.read_parquet(run / "data.parquet")
    raw = pl.read_parquet(run / "data_raw.parquet")
    kept = raw.filter(pl.col("vSessionId").is_in(out["vSessionId"]))
    assert out.equals(kept)  # no policy: kept rows are the raw rows, values unchanged
    assert not (tmp_path / "studies/st1/st1_cleaning_log.csv").read_text().count("median")


def test_lock_applies_policy_and_writes_cleaning_log(client, tmp_path, monkeypatch):
    c, _, _ = client
    run = _numeric_setup(tmp_path, monkeypatch)
    assert c.post(URL + "?preview=false", json={**_IMP, "confirm": True}).status_code == 200
    got = c.get("/api/studies/st1/cleaning").json()
    assert got["imputations"][0]["variable"] == "Q1"
    # keep s4 (if flagged) so the imputed cell survives; review the rest as removals
    flagged = sorted(got["flags"])
    if "s4" in flagged:
        assert _post(c, ["s4"], "keep").status_code == 200
        flagged.remove("s4")
    if flagged:
        assert _post(c, flagged).status_code == 200
    assert c.post("/api/studies/st1/cleaning/lock").status_code == 200
    import csv
    rows = list(csv.DictReader(open(tmp_path / "studies/st1/st1_cleaning_log.csv")))
    imp = [r for r in rows if r["variable"] == "Q1"]
    assert len(imp) == 1
    assert imp[0]["key"] == "s4" and imp[0]["old"] == "" and imp[0]["method"] == "median"
    assert imp[0]["new"] and imp[0]["reason_code"] == "missing"
    out = pl.read_parquet(run / "data.parquet")
    assert out.filter(pl.col("vSessionId") == "s4")["Q1"].to_list() == [float(imp[0]["new"])]
    assert pl.read_parquet(run / "data_raw.parquet").filter(
        pl.col("vSessionId") == "s4")["Q1"].to_list() == [None]

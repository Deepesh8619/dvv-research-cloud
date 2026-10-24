"""The whole journey, end to end, through the public API — the way a study runs.

A brand tracker built from the template, approved, opened as a link, answered
by forty respondents through the respondent endpoints (some screened out by
age, one refusing consent), then turned into data and everything downstream:
tables, findings, cleaning, Open ends, methods, the PowerPoint, the supplier
reconciliation.

Each feature has its own tests. This one exists because features break each
other: coding silently never ran on a whole class of studies for months while
every coding unit test passed.
"""

import csv
import io
import random

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.auth import get_current_user
from api.db import Base, UserDB, get_db
from api.main import app

INPUTS = {"organisation": "DVV Research", "purpose": "understanding coffee choices",
          "contact": "privacy@dvv.example", "category": "instant coffee",
          "brands": "Nescafé\nBru\nDavidoff\nSunrise", "subject": "Nescafé",
          "attributes": "Taste\nPrice\nPackaging\nAroma"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        db = session()
        try:
            yield db
        finally:
            db.close()

    import api.routes.materialize as materialize_module
    import api.routes.studies as studies_module

    monkeypatch.setattr(materialize_module, "STUDIES_DIR", tmp_path)
    monkeypatch.setattr(studies_module, "STUDIES_DIR", tmp_path)
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: UserDB(
        id="u1", email="owner@x.com", name="Owner", role="owner", is_active=True)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _answer(q: dict, rng: random.Random):
    """A plausible answer of the right shape for one question."""
    codes = [o["code"] for o in q["options"]]
    kind = q["type"]
    if kind == "single":
        return rng.choice([c for c in codes if c < 90] or codes)
    if kind == "multi":
        real = [c for c in codes if c < 90]
        return rng.sample(real, rng.randint(1, len(real)))
    if kind == "grid_single":
        cols = [c["code"] for c in q["cols"]]
        return {str(r["code"]): rng.choice(cols) for r in q["rows"]}
    if kind == "numeric":
        return rng.randint(1, 6)
    if kind == "open_text":
        return rng.choice(["The taste is smooth and it dissolves quickly in hot water",
                           "Too expensive for what it is", "I like the aroma in the morning"])
    return None


def test_a_study_runs_from_template_to_deck(client):
    c, rng = client, random.Random(7)
    sid = c.post("/api/studies",
                 json={"client_name": "Coffee tracker W1", "kind": "builder"}).json()["id"]
    made = c.post(f"/api/studies/{sid}/questionnaire/authored", json={"title": "Coffee tracker"})
    assert made.status_code in (200, 201), made.text
    t = c.post(f"/api/studies/{sid}/questionnaire/template",
               json={"template": "brand_tracker", "inputs": INPUTS})
    assert t.status_code == 200, t.text

    # Built well: the expert checks have nothing to fix.
    assert c.get(f"/api/studies/{sid}/questionnaire/expert-checks").json()["n_fix"] == 0

    plan = c.put(f"/api/studies/{sid}/questionnaire/quotas", json={
        "completes": 40, "cells": [{"name": "Women", "qid": "S_GENDER", "codes": [2],
                                    "share_pct": 75}]})
    assert plan.status_code == 200 and plan.json()["limits"][0]["limit"] == 30, plan.text
    c.post(f"/api/studies/{sid}/questionnaire/questions/approve-clean")
    approved = c.post(f"/api/studies/{sid}/questionnaire/approve-all")
    assert approved.status_code == 200, approved.text

    link = c.post(f"/api/studies/{sid}/fielding/links", json={})
    assert link.status_code in (200, 201), link.text
    token, link_id = link.json()["token"], link.json()["id"]
    cells = c.get(f"/api/studies/{sid}/fielding/links/{link_id}/quotas").json()["cells"]
    assert [(q["name"], q["limit"]) for q in cells] == [("Women", 30)]  # the plan, applied
    c.patch(f"/api/studies/{sid}/fielding/links/{link_id}/vendor",
            json={"vendor_name": "Cint", "cpi": 3.5, "target_completes": 40})

    questions = c.get(f"/api/studies/{sid}/questionnaire/questions").json()["questions"]
    outcomes = {"complete": 0, "screened": 0}
    for i in range(40):
        entry = c.get(f"/api/r/{token}?pid=P{i:03d}").json()
        session = entry["session_id"]
        answers = {}
        for q in questions:
            if q["qid"] == "CONSENT":
                answers[q["qid"]] = 2 if i == 0 else 1  # one refuses consent
            elif q["qid"] == "S_AGE":
                answers[q["qid"]] = 16 if i % 8 == 1 else rng.randint(18, 70)
            elif (value := _answer(q, rng)) is not None:
                answers[q["qid"]] = value
        r = c.post(f"/api/r/{token}/sessions/{session}/answers",
                   json={"answers": answers, "current_page": len(questions)}).json()
        if r.get("terminated"):
            outcomes["screened"] += 1
            continue
        done = c.post(f"/api/r/{token}/sessions/{session}/complete")
        assert done.status_code == 200, done.text
        outcomes["complete"] += 1
    assert outcomes == {"complete": 34, "screened": 6}  # 5 under 18, 1 no consent

    m = c.post(f"/api/studies/{sid}/fielding/materialize")
    assert m.status_code == 200, m.text

    # Everything downstream reads the same data.
    tables = c.get(f"/api/studies/{sid}/crosstabs").json()["tables"]
    assert tables, "no tables"
    findings = c.get(f"/api/studies/{sid}/findings").json()["findings"]
    # Multi-selects are tabulated: a tracker's awareness is its headline.
    assert {"AWR_AIDEDr1", "AWR_CONSIDERr1"} <= {t["stub"] for t in tables}
    assert any(f["kind"] == "most_chosen" and "heard of" in f["text"] for f in findings)
    assert c.get(f"/api/studies/{sid}/cleaning").status_code == 200
    open_ends = c.get(f"/api/studies/{sid}/coding/status").json()["open_ends"]
    assert {o["variable"] for o in open_ends} >= {"AWR_UNAIDED", "NPS_WHY"}
    methods = c.get(f"/api/studies/{sid}/methods").json()
    assert methods["n_respondents"] == 34
    turf = c.post(f"/api/studies/{sid}/methods",
                  json={"method": "turf", "question": "AWR_AIDED", "max_size": 2})
    assert turf.status_code == 200, turf.text
    deck = c.get(f"/api/studies/{sid}/download/pptx")
    assert deck.status_code == 200 and deck.content[:2] == b"PK"
    recon = list(csv.DictReader(io.StringIO(
        c.get(f"/api/studies/{sid}/fielding/links/{link_id}/reconciliation.csv").text)))
    assert sum(r["decision"] == "accepted" for r in recon) == 34
    assert {r["panel_id"] for r in recon} >= {"P001", "P002"}
    vendors = c.get(f"/api/studies/{sid}/fielding/vendors").json()["vendors"]
    assert vendors[0]["billable"] == pytest.approx(34 * 3.5)

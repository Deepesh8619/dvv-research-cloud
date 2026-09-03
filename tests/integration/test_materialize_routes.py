"""Materialize a fielding link -> real crosstab, end to end (T11).

Proves the actual payoff: fielded respondent data reaches the EXISTING
analysis stack (crosstab()) completely unmodified. Drives real sessions
through the public /r/{token} endpoints (same as a real respondent would),
materializes, then loads the written parquet/DataMap into a real crosstab().

Fixture conventions match test_fielding_routes.py / test_quota_routes.py.
"""

from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import api.routes.materialize as materialize_module
from api.auth import get_current_user
from api.db import Base, QuestionnaireDB, StudyDB, UserDB, get_db
from api.main import app
from src.core.models import DataMap
from src.core.survey_definition import (
    Block,
    Expression,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
)
from src.engines.tabs.crosstab import crosstab


def _survey(study_id: str) -> SurveyDefinition:
    q1 = Question(qid="Q1", text="Age", type=QuestionType.NUMERIC,
                  terminate_if=Expression(op="lt", qid="Q1", value=18))
    q2 = Question(
        qid="Q2", text="Do you drink coffee?", type=QuestionType.SINGLE,
        options=[Option(code=1, label="Yes"), Option(code=2, label="No")],
    )
    q3 = Question(
        qid="Q3", text="Which brands do you know?", type=QuestionType.MULTI,
        options=[Option(code=1, label="Alpha"), Option(code=2, label="Beta")],
        required=False,
    )
    q4 = Question(
        qid="Q4", text="Which coffee brand?", type=QuestionType.OPEN_TEXT,
        required=False, show_if=Expression(op="eq", qid="Q2", value=1),
    )
    return SurveyDefinition(
        study_id=study_id, title="Coffee Study", source="authored", status="approved",
        version=1, blocks=[Block(block_id="B1", question_ids=["Q1", "Q2", "Q3", "Q4"])],
        questions=[q1, q2, q3, q4],
    )


@pytest.fixture
def client(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path}/test.db", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    state = {"role": "owner"}

    def override_get_current_user():
        return UserDB(id="u1", email="test@x.com", name="Test", role=state["role"], is_active=True)

    monkeypatch.setattr(materialize_module, "STUDIES_DIR", tmp_path)
    # The download route resolves the same directory through its own module
    # constant; both are `api.config.STUDIES_DIR` in production.
    import api.routes.studies as studies_module

    monkeypatch.setattr(studies_module, "STUDIES_DIR", tmp_path)

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    yield TestClient(app), TestSessionLocal, state
    app.dependency_overrides.clear()


def _seed(session_factory, study_id="study_m1"):
    survey = _survey(study_id)
    db = session_factory()
    db.add(StudyDB(
        id=study_id, client_name="Test", status="classifying",
        created_at=datetime.now().isoformat(), updated_at=datetime.now().isoformat(),
        study_metadata={}, approval_chain={},
    ))
    db.add(QuestionnaireDB(
        id="quest_m1", study_id=study_id, status="approved", source_filename="s.docx",
        version=survey.version, definition_json=survey.to_json(), definition_path=None,
        approved_qids=[q.qid for q in survey.questions],
        created_at=datetime.now().isoformat(), updated_at=datetime.now().isoformat(),
    ))
    db.commit()
    db.close()


def _run_respondent(test_client, token, *, age, coffee, brands, follow_up=None, entry_params=""):
    entry = test_client.get(f"/api/r/{token}{entry_params}")
    assert entry.status_code == 200
    session_id = entry.json()["session_id"]
    answers = {"Q1": age}
    if coffee is not None:
        answers["Q2"] = coffee
    if brands is not None:
        answers["Q3"] = brands
    if follow_up is not None:
        answers["Q4"] = follow_up
    resp = test_client.post(
        f"/api/r/{token}/sessions/{session_id}/answers",
        json={"answers": answers, "current_page": 1},
    )
    assert resp.status_code == 200
    body = resp.json()
    if body.get("terminated"):
        return session_id, body["status_code"]
    complete = test_client.post(f"/api/r/{token}/sessions/{session_id}/complete")
    assert complete.status_code == 200
    return session_id, complete.json().get("status_code", 10)


def test_materialize_reaches_real_crosstab_end_to_end(client, tmp_path):
    test_client, sf, _ = client
    _seed(sf)

    link_resp = test_client.post("/api/studies/study_m1/fielding/links")
    assert link_resp.status_code == 200
    token = link_resp.json()["token"]

    # 3 completes (mixed multi-select + show_if-gated follow-up), 1 terminate
    # (underage) that must be EXCLUDED from the materialized dataset.
    _run_respondent(test_client, token, age=30, coffee=1, brands=[1], follow_up="Alpha rocks")
    _run_respondent(test_client, token, age=40, coffee=2, brands=[1, 2])
    _run_respondent(test_client, token, age=25, coffee=1, brands=None)
    _, status = _run_respondent(test_client, token, age=15, coffee=1, brands=None)
    assert status == 20  # terminated, excluded

    link_id = link_resp.json()["id"]
    mat = test_client.post(f"/api/studies/study_m1/fielding/links/{link_id}/materialize")
    assert mat.status_code == 200
    body = mat.json()
    assert body["n_respondents"] == 3  # terminate excluded
    assert body["n_variables"] > 0

    # Load exactly what the existing engines would load and run a REAL crosstab.
    run_dir = tmp_path / "study_m1"
    datamap = DataMap.from_json((run_dir / "datamap.json").read_text())
    result = crosstab(run_dir / "data.parquet", stub="Q2", banner=[], datamap=datamap)
    assert result.counts.sum() == 3
    yes_idx = result.row_codes.index(1)
    no_idx = result.row_codes.index(2)
    assert result.counts[yes_idx, 0] == 2  # two "Yes" respondents
    assert result.counts[no_idx, 0] == 1  # one "No" respondent


def test_materialize_zero_completes_is_422(client):
    test_client, sf, _ = client
    _seed(sf)
    link_resp = test_client.post("/api/studies/study_m1/fielding/links")
    link_id = link_resp.json()["id"]

    mat = test_client.post(f"/api/studies/study_m1/fielding/links/{link_id}/materialize")
    assert mat.status_code == 422


def test_materialize_is_idempotent_refresh(client, tmp_path):
    test_client, sf, _ = client
    _seed(sf)
    link_resp = test_client.post("/api/studies/study_m1/fielding/links")
    token = link_resp.json()["token"]
    link_id = link_resp.json()["id"]

    _run_respondent(test_client, token, age=30, coffee=1, brands=[1])
    first = test_client.post(f"/api/studies/study_m1/fielding/links/{link_id}/materialize")
    assert first.json()["n_respondents"] == 1

    _run_respondent(test_client, token, age=31, coffee=2, brands=[2])
    second = test_client.post(f"/api/studies/study_m1/fielding/links/{link_id}/materialize")
    assert second.json()["n_respondents"] == 2  # full refresh, not append-only drift


def test_materialize_renders_real_excel_deck(client, tmp_path):
    import openpyxl

    from api.db import get_study

    test_client, sf, _ = client
    _seed(sf)
    link_resp = test_client.post("/api/studies/study_m1/fielding/links")
    token = link_resp.json()["token"]
    link_id = link_resp.json()["id"]

    _run_respondent(test_client, token, age=30, coffee=1, brands=[1])
    _run_respondent(test_client, token, age=40, coffee=2, brands=[1, 2])
    mat = test_client.post(f"/api/studies/study_m1/fielding/links/{link_id}/materialize")
    assert mat.status_code == 200

    # excel_path now points at a REAL .xlsx openpyxl can open, with >=1 sheet.
    db = sf()
    excel_path = get_study(db, "study_m1").excel_path
    db.close()
    assert excel_path.endswith(".xlsx")
    assert Path(excel_path).exists()
    wb = openpyxl.load_workbook(excel_path)
    assert len(wb.sheetnames) >= 1

    # The download endpoint serves it (200).
    dl = test_client.get("/api/studies/study_m1/download/excel")
    assert dl.status_code == 200

    # If the ephemeral file is wiped, the download degrades to a graceful 404
    # (never a 500).
    Path(excel_path).unlink()
    gone = test_client.get("/api/studies/study_m1/download/excel")
    assert gone.status_code == 404


def test_materialize_reviewer_403(client):
    test_client, sf, state = client
    _seed(sf)
    link_resp = test_client.post("/api/studies/study_m1/fielding/links")
    link_id = link_resp.json()["id"]

    state["role"] = "reviewer"
    resp = test_client.post(f"/api/studies/study_m1/fielding/links/{link_id}/materialize")
    assert resp.status_code == 403


# --- delivery: every disposition, and the field report ------------------------


def test_the_delivery_files_carry_every_disposition(client, tmp_path):
    """`data.parquet` stays completes-only — a topline over screened-out people
    is simply wrong — but a delivery carries the terminates. They are the
    evidence the screen-outs worked and the denominator of the incidence rate.
    """
    test_client, sf, _ = client
    _seed(sf)
    link_resp = test_client.post("/api/studies/study_m1/fielding/links")
    token, link_id = link_resp.json()["token"], link_resp.json()["id"]

    _run_respondent(test_client, token, age=30, coffee=1, brands=[1], follow_up="ok")
    _run_respondent(test_client, token, age=40, coffee=2, brands=[1, 2])
    _run_respondent(test_client, token, age=15, coffee=1, brands=None)  # terminated

    body = test_client.post(
        f"/api/studies/study_m1/fielding/links/{link_id}/materialize"
    ).json()

    assert body["n_respondents"] == 2  # the analysis dataset is unchanged
    assert body["delivery"]["n_rows"] == 3  # the delivery keeps the terminate
    assert set(body["delivery"]["formats"]) == {"sav", "xlsx"}
    assert (tmp_path / "study_m1" / "study_m1_responses.sav").exists()
    assert (tmp_path / "study_m1" / "study_m1_responses.xlsx").exists()


def test_the_spss_file_is_readable_and_keeps_its_labels(client, tmp_path):
    """A .sav a client cannot open, or that opens as bare integers, is not a
    deliverable."""
    from src.adapters.spss import read_sav

    test_client, sf, _ = client
    _seed(sf)
    link_resp = test_client.post("/api/studies/study_m1/fielding/links")
    token, link_id = link_resp.json()["token"], link_resp.json()["id"]
    _run_respondent(test_client, token, age=30, coffee=1, brands=[1], follow_up="ok")
    _run_respondent(test_client, token, age=15, coffee=1, brands=None)
    test_client.post(f"/api/studies/study_m1/fielding/links/{link_id}/materialize")

    df, datamap = read_sav(str(tmp_path / "study_m1" / "study_m1_responses.sav"))

    assert len(df) == 2
    # The disposition column is what makes a mixed file readable at all.
    assert datamap["vStatus"].values[10] == "Complete"
    assert datamap["vStatus"].values[20] == "Screened out"
    assert datamap["Q2"].label  # question wording travelled with the data


def test_both_delivery_files_are_downloadable(client, tmp_path):
    test_client, sf, _ = client
    _seed(sf)
    link_resp = test_client.post("/api/studies/study_m1/fielding/links")
    token, link_id = link_resp.json()["token"], link_resp.json()["id"]
    _run_respondent(test_client, token, age=30, coffee=1, brands=[1], follow_up="ok")
    test_client.post(f"/api/studies/study_m1/fielding/links/{link_id}/materialize")

    assert test_client.get("/api/studies/study_m1/download/spss").status_code == 200
    assert test_client.get("/api/studies/study_m1/download/responses").status_code == 200


def test_the_field_report_answers_the_morning_after_questions(client):
    test_client, sf, _ = client
    _seed(sf)
    link_resp = test_client.post("/api/studies/study_m1/fielding/links")
    token, link_id = link_resp.json()["token"], link_resp.json()["id"]

    _run_respondent(test_client, token, age=30, coffee=1, brands=[1], follow_up="ok")
    _run_respondent(test_client, token, age=40, coffee=2, brands=[1, 2])
    _run_respondent(test_client, token, age=15, coffee=1, brands=None)

    body = test_client.get(
        f"/api/studies/study_m1/fielding/links/{link_id}/field-report"
    ).json()

    assert body["starts"] == 3
    assert body["dispositions"]["complete"] == 2
    assert body["dispositions"]["terminate"] == 1
    assert body["incidence_pct"] == 66.7
    # And WHICH screen-out removed them — recomputed, because the live route
    # logs the decision and does not store it.
    assert body["terminates_by_question"] == [{"qid": "Q1", "n": 1}]

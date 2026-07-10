"""Materialize a fielding link -> real crosstab, end to end (T11).

Proves the actual payoff: fielded respondent data reaches the EXISTING
analysis stack (crosstab()) completely unmodified. Drives real sessions
through the public /r/{token} endpoints (same as a real respondent would),
materializes, then loads the written parquet/DataMap into a real crosstab().

Fixture conventions match test_fielding_routes.py / test_quota_routes.py.
"""

from datetime import datetime

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


def test_materialize_reviewer_403(client):
    test_client, sf, state = client
    _seed(sf)
    link_resp = test_client.post("/api/studies/study_m1/fielding/links")
    link_id = link_resp.json()["id"]

    state["role"] = "reviewer"
    resp = test_client.post(f"/api/studies/study_m1/fielding/links/{link_id}/materialize")
    assert resp.status_code == 403

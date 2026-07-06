"""GET /studies/{id}/profile: data-quality profile endpoint.

Uses FastAPI's TestClient against the real app, with get_db/get_current_user
overridden to a temp sqlite session and a fake authenticated user — no real
login flow needed since this endpoint only requires *some* authenticated user.
"""

from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.auth import get_current_user
from api.db import Base, StudyDB, UserDB, get_db
from api.main import app
from src.core.models import DataMap, Variable, VarType


@pytest.fixture
def client(tmp_path):
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

    def override_get_current_user():
        return UserDB(
            id="u1", email="test@x.com", name="Test", role="owner", is_active=True
        )

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    yield TestClient(app), TestSessionLocal
    app.dependency_overrides.clear()


def _make_study(session_factory, tmp_path: Path, study_id: str, with_data: bool = True):
    """Insert a study row and, optionally, a data.parquet + datamap.json on disk."""
    db = session_factory()
    study_dir = tmp_path / study_id
    study_dir.mkdir(parents=True, exist_ok=True)
    excel_path = study_dir / "deck.xlsx"
    excel_path.touch()

    study = StudyDB(
        id=study_id,
        client_name="Test Study",
        status="rendered",
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat(),
        study_metadata={},
        approval_chain={},
        excel_path=str(excel_path),
    )
    db.add(study)
    db.commit()
    db.close()

    if with_data:
        df = pd.DataFrame(
            {
                "resp_id": [1, 2, 3, 4],
                "age": [25, 30, None, 40],
                "gender": ["male", "female", "male", "male"],
                "constant_col": [1, 1, 1, 1],
            }
        )
        df.to_parquet(study_dir / "data.parquet")

        datamap = DataMap()
        datamap.add(Variable("resp_id", label="Respondent ID", type=VarType.NUMERIC))
        datamap.add(Variable("age", label="Age", type=VarType.NUMERIC))
        datamap.add(
            Variable(
                "gender",
                label="Gender",
                type=VarType.SINGLE,
                values={1: "Male", 2: "Female"},
            )
        )
        datamap.add(Variable("constant_col", label="Always one", type=VarType.NUMERIC))
        (study_dir / "datamap.json").write_text(datamap.to_json())

    return study_dir


def test_profile_returns_quality_report_shape(client, tmp_path):
    test_client, session_factory = client
    _make_study(session_factory, tmp_path, "study_profile1")

    resp = test_client.get("/api/studies/study_profile1/profile")
    assert resp.status_code == 200
    body = resp.json()

    assert body["ready"] is True
    summary = body["summary"]
    assert summary["n_variables"] == 4
    assert summary["n_respondents"] == 4
    assert summary["n_constant"] == 1  # constant_col
    assert "n_high_missing" in summary
    assert "n_likely_duplicate_id" in summary

    variables = {v["name"]: v for v in body["variables"]}
    assert "age" in variables
    age = variables["age"]
    assert age["label"] == "Age"
    assert "missing_pct" in age
    assert "distinct_count" in age
    assert "min" in age and "max" in age  # numeric variable

    gender = variables["gender"]
    assert "top_values" in gender  # categorical variable


def test_profile_missing_data_returns_placeholder_not_500(client, tmp_path):
    test_client, session_factory = client
    _make_study(session_factory, tmp_path, "study_no_data", with_data=False)

    resp = test_client.get("/api/studies/study_no_data/profile")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ready"] is False
    assert body["variables"] == []
    assert body["summary"]["n_variables"] == 0
    assert body["summary"]["n_respondents"] == 0


def test_profile_404_for_unknown_study(client):
    test_client, _ = client
    resp = test_client.get("/api/studies/does_not_exist/profile")
    assert resp.status_code == 404

"""POST /studies/{id}/crosscheck — the step that joins the two halves of the tool.

The case pinned hardest here is the one a reviewer actually hits on the free
tier: Render's disk is wiped by every restart, so the respondent rows can be
gone while the durable copies (the approved questionnaire, the data map) are
perfectly intact. That used to return 409 — throwing away the linking, the
reconciliation and the condition audit that had already been computed and need
no rows at all, and leaving a yellow banner where a real report belongs.
"""

from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import api.routes.studies as sroutes
from api.auth import get_current_user
from api.db import Base, QuestionnaireDB, StudyDB, UserDB, get_db
from api.main import app
from src.core.models import DataMap, Variable, VarType
from src.core.survey_definition import (
    Block,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
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

    def override_get_current_user():
        return UserDB(
            id="u1", email="t@x.com", name="Test", role="owner", is_active=True
        )

    monkeypatch.setattr(sroutes, "STUDIES_DIR", tmp_path)
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    yield TestClient(app), TestSessionLocal, tmp_path
    app.dependency_overrides.clear()


def _survey() -> SurveyDefinition:
    q = Question(
        qid="Q1",
        text="Which brands do you know?",
        type=QuestionType.SINGLE,
        options=[Option(code=1, label="Alpha"), Option(code=2, label="Beta")],
    )
    return SurveyDefinition(
        study_id="s1",
        title="t",
        source="extracted",
        status="approved",
        blocks=[Block(block_id="B1", question_ids=["Q1"])],
        questions=[q],
    )


def _datamap() -> DataMap:
    dm = DataMap()
    dm.add(
        Variable(
            name="Q1",
            label="Which brands do you know?",
            type=VarType.SINGLE,
            values={1: "Alpha", 2: "Beta"},
            provenance="spss",
        )
    )
    return dm


def _seed(session_factory, *, with_questionnaire=True, with_datamap=True) -> None:
    db = session_factory()
    db.add(
        StudyDB(
            id="s1",
            client_name="Test",
            status="rendered",
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
            study_metadata={},
            approval_chain={},
            datamap_json=_datamap().to_json() if with_datamap else None,
        )
    )
    if with_questionnaire:
        db.add(
            QuestionnaireDB(
                id="quest_1",
                study_id="s1",
                status="approved",
                source_filename="Wave 2 QRE.docx",
                version=2,
                definition_json=_survey().to_json(),
                approved_qids=["Q1"],
                created_at=datetime.now().isoformat(),
                updated_at=datetime.now().isoformat(),
            )
        )
    db.commit()
    db.close()


def test_a_wiped_disk_still_returns_the_questionnaire_side_checks(client) -> None:
    test_client, SessionLocal, tmp_path = client
    _seed(SessionLocal)
    assert not (Path(tmp_path) / "s1" / "data.parquet").exists()

    resp = test_client.post("/api/studies/s1/crosscheck")

    assert resp.status_code == 200
    body = resp.json()
    # The half that needs no respondent rows was still done.
    assert body["data_file_available"] is False
    assert body["linked_questions"] == 1
    assert "conditions" in body and "variable_roles" in body
    # And the half that could not be done says so, in words a reviewer can act on.
    assert any("Re-process" in s for s in body["skipped"])
    assert body["n_rows"] == 0


def test_no_questionnaire_is_still_refused_with_a_reason(client) -> None:
    test_client, SessionLocal, _ = client
    _seed(SessionLocal, with_questionnaire=False)

    resp = test_client.post("/api/studies/s1/crosscheck")

    assert resp.status_code == 409
    assert "questionnaire" in resp.json()["detail"].lower()


def test_no_data_map_is_still_refused_with_a_reason(client) -> None:
    test_client, SessionLocal, _ = client
    _seed(SessionLocal, with_datamap=False)

    resp = test_client.post("/api/studies/s1/crosscheck")

    assert resp.status_code == 409
    assert "data file" in resp.json()["detail"].lower()


def test_the_result_is_stored_and_read_back(client) -> None:
    """Leaving the project and returning must not lose the run."""
    test_client, SessionLocal, _ = client
    _seed(SessionLocal)
    test_client.post("/api/studies/s1/crosscheck")

    stored = test_client.get("/api/studies/s1/crosscheck").json()
    assert stored["report"] is not None
    assert stored["report"]["linked_questions"] == 1


def test_the_base_of_every_question_is_reported(client) -> None:
    """Bases were compiled from the document and then never compared to anyone.

    `cross_validate_routing` has existed since the linker was written and the
    cross-check route never called it, so every "[IF S2 = 2]" was read, turned
    into a real condition, and then checked against nobody.
    """
    test_client, SessionLocal, _ = client
    _seed(SessionLocal)

    body = test_client.post("/api/studies/s1/crosscheck").json()

    assert "routing" in body
    assert set(body["routing"]) == {"n_checked", "n_unchecked", "issues"}


def test_rows_from_an_uploaded_file_are_read_from_the_run_folder(client) -> None:
    """Processing writes the rows beside the deck, not in the upload folder; the
    check reported "the server restarted" and skipped every row-level check."""
    import polars as pl

    test_client, session_factory, tmp_path = client
    _seed(session_factory)
    run_dir = Path(tmp_path) / "study_20261017_abc"
    run_dir.mkdir()
    pl.DataFrame({"Q1": [1, 2, 7]}).write_parquet(run_dir / "data.parquet")
    db = session_factory()
    db.query(StudyDB).filter(StudyDB.id == "s1").update(
        {"excel_path": str(run_dir / "deck.xlsx")}
    )
    db.commit()
    db.close()

    body = test_client.post("/api/studies/s1/crosscheck").json()
    assert body["data_file_available"] is True
    assert body["n_rows"] == 3

"""The logic certificate: three queued steps, one PDF, an honest verdict."""

import fitz
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import src.workflows.qa_certificate as qa
from api.auth import get_current_user
from api.db import Base, QuestionnaireDB, UserDB, create_study, get_db
from api.main import app
from src.core.survey_definition import (
    Block,
    Expression,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
)
from src.platform.worker import drain


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setattr(qa, "STUDIES_DIR", tmp_path)
    app.dependency_overrides[get_db] = override
    app.dependency_overrides[get_current_user] = lambda: UserDB(
        id="u1", email="o@x.com", name="Owner", role="owner", is_active=True)
    yield TestClient(app), factory
    app.dependency_overrides.clear()


def _gender() -> Question:
    return Question(qid="S2", text="Gender", type=QuestionType.SINGLE,
                    options=[Option(code=1, label="Male"), Option(code=2, label="Female")])


def _approve(factory, *questions: Question) -> None:
    survey = SurveyDefinition(study_id="s1", title="Coffee", questions=list(questions),
                              blocks=[Block(block_id="B1",
                                            question_ids=[q.qid for q in questions])])
    with factory() as db:
        create_study(db, "s1", "Acme")
        db.add(QuestionnaireDB(id="q1", study_id="s1", status="approved", approved_qids=[],
                               definition_json=survey.to_json(), created_at="1"))
        db.commit()


def _issue(client, factory) -> tuple[dict, str]:
    r = client.post("/api/studies/s1/certificate")
    assert r.status_code == 202, r.text
    drain(factory)
    run = client.get(f"/api/workflow-runs/{r.json()['run_id']}").json()
    pdf = client.get("/api/studies/s1/certificate.pdf")
    text = "".join(p.get_text() for p in fitz.open(stream=pdf.content, filetype="pdf")) \
        if pdf.status_code == 200 else ""
    return run, text


def test_a_clean_survey_is_issued(env):
    client, factory = env
    assert client.get("/api/studies/s1/certificate.pdf").status_code == 404
    _approve(factory, _gender())
    run, text = _issue(client, factory)
    assert run["state"] == "done"
    assert [(s["name"], s["state"]) for s in run["steps"]] == [
        ("expert_checks", "done"), ("dry_run", "done"), ("certificate", "done")]
    assert "Issued" in text and "Not issued" not in text
    with factory() as db:
        from api.db import get_study

        cert = get_study(db, "s1").study_metadata["certificate"]
    assert cert["issued"] is True and cert["run_id"] == run["id"] and len(cert["sha256"]) == 64


def test_an_unreachable_question_is_not_issued_and_named(env):
    client, factory = env
    dead = Question(qid="Q9", text="Never asked", type=QuestionType.SINGLE,
                    options=[Option(code=1, label="a")],
                    show_if=Expression.from_dict({"op": "in", "qid": "S2", "values": [9]}))
    _approve(factory, _gender(), dead)
    run, text = _issue(client, factory)
    assert run["state"] == "done"
    assert "Not issued" in text and "Q9" in text
    with factory() as db:
        from api.db import get_study

        assert get_study(db, "s1").study_metadata["certificate"]["issued"] is False


def test_no_approved_questionnaire_fails_with_a_plain_sentence(env):
    from api.db import JobDB

    client, factory = env
    with factory() as db:
        create_study(db, "s1", "Acme")
    client.post("/api/studies/s1/certificate")
    drain(factory)  # fails once; the retry is queued with a delay
    with factory() as db:
        job = db.query(JobDB).one()
    assert job.error == "This study has no approved questionnaire to check."

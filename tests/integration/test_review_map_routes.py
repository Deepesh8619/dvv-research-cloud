"""The review map, editing links, and the check that runs before a link exists."""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.auth import get_current_user
from api.db import Base, QuestionnaireDB, StudyDB, UserDB, get_db
from api.main import app
from src.core.survey_definition import (
    Block,
    Expression,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
)

BRANDS = [Option(code=i, label=b) for i, b in enumerate(["Bubble", "CeraVe", "Rhode"], 1)]


def _survey(*, empty_q3: bool = False) -> SurveyDefinition:
    questions = [
        Question(qid="S1", text="Do you buy skincare?",
                 options=[Option(code=1, label="Yes"), Option(code=2, label="No")],
                 terminate_if=Expression(op="eq", qid="S1", value=2)),
        Question(qid="Q2a", text="Which brands have you heard of?", type=QuestionType.MULTI,
                 options=list(BRANDS)),
        Question(qid="Q2b", text="Which have you bought?", type=QuestionType.MULTI,
                 options=list(BRANDS)),
        Question(qid="Q3", text="Which is your favourite?",
                 options=[] if empty_q3 else list(BRANDS)),
    ]
    return SurveyDefinition(
        study_id="s1", title="t", source="extracted", status="approved",
        blocks=[Block(block_id="B1", question_ids=[q.qid for q in questions])],
        questions=questions,
    )


@pytest.fixture
def client(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def db():
        s = session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = db
    app.dependency_overrides[get_current_user] = lambda: UserDB(
        id="u1", email="a@x.com", name="Rev", role="owner", is_active=True
    )
    yield TestClient(app), session
    app.dependency_overrides.clear()


def _seed(session, survey: SurveyDefinition, status: str = "approved") -> None:
    db = session()
    now = datetime.now().isoformat()
    db.add(StudyDB(id="s1", client_name="T", status="classifying", created_at=now,
                   updated_at=now, study_metadata={}, approval_chain={}))
    db.add(QuestionnaireDB(
        id="qn1", study_id="s1", status=status, source_filename="q.docx", version=1,
        definition_json=survey.to_json(), definition_path=None,
        approved_qids=[q.qid for q in survey.questions], created_at=now, updated_at=now,
    ))
    db.commit()
    db.close()


def test_the_map_gives_every_question_its_bucket_and_links(client):
    test_client, session = client
    _seed(session, _survey())
    body = test_client.get("/api/studies/s1/questionnaire/map").json()["questions"]
    assert body["S1"]["bucket"] == "screener"
    assert body["Q2a"]["bucket"] == "core"


def test_a_reviewer_links_options_to_an_earlier_question_and_takes_it_back(client):
    test_client, session = client
    _seed(session, _survey())
    set_it = test_client.put(
        "/api/studies/s1/questionnaire/questions/Q2b/links",
        json={"options_from": [{"source_qid": "Q2a", "mode": "selected"}]},
    )
    assert set_it.status_code == 200
    graph = test_client.get("/api/studies/s1/questionnaire/map").json()["questions"]
    assert graph["Q2b"]["links"]["options_from"] == ["Q2a"]
    assert graph["Q2a"]["links"]["used_by"] == ["Q2b"]

    test_client.put("/api/studies/s1/questionnaire/questions/Q2b/links", json={"options_from": []})
    graph = test_client.get("/api/studies/s1/questionnaire/map").json()["questions"]
    assert graph["Q2b"]["links"]["options_from"] == []


def test_a_link_to_a_later_question_is_refused(client):
    """A respondent cannot be shown options from a question they have not reached."""
    test_client, session = client
    _seed(session, _survey())
    resp = test_client.put(
        "/api/studies/s1/questionnaire/questions/Q2a/links",
        json={"options_from": [{"source_qid": "Q2b", "mode": "selected"}]},
    )
    assert resp.status_code == 422
    assert "comes after" in resp.json()["detail"]


def test_a_pipe_puts_its_placeholder_in_the_wording_and_removing_it_takes_it_out(client):
    test_client, session = client
    _seed(session, _survey())
    added = test_client.put(
        "/api/studies/s1/questionnaire/questions/Q3/links", json={"text_from": ["Q2a"]}
    ).json()
    assert added["text"].endswith("[INSERT Q2a]")
    removed = test_client.put(
        "/api/studies/s1/questionnaire/questions/Q3/links", json={"text_from": []}
    ).json()
    assert "[INSERT" not in removed["text"]


def test_the_pre_link_check_names_what_a_respondent_would_meet(client):
    test_client, session = client
    _seed(session, _survey(empty_q3=True))
    check = test_client.get("/api/studies/s1/fielding/preflight").json()
    assert check["ok"] is False
    answers = next(c for c in check["checks"] if c["id"] == "answers")
    assert answers["status"] == "fail" and answers["qids"] == ["Q3"]


def test_a_failing_check_refuses_the_link_before_it_exists(client):
    """The old check ran after the insert: a refused link was also saved."""
    test_client, session = client
    _seed(session, _survey(empty_q3=True))
    refused = test_client.post("/api/studies/s1/fielding/links", json={})
    assert refused.status_code == 409
    assert refused.json()["detail"]["checks"][0]["id"] == "answers"
    assert test_client.get("/api/studies/s1/fielding/links").json()["links"] == []
    # Overridden on purpose, it opens.
    forced = test_client.post("/api/studies/s1/fielding/links", json={"force": True})
    assert forced.status_code == 200


def test_a_clean_questionnaire_passes_and_opens(client):
    test_client, session = client
    _seed(session, _survey())
    check = test_client.get("/api/studies/s1/fielding/preflight").json()
    assert check["ok"] is True, [c for c in check["checks"] if c["status"] == "fail"]
    assert test_client.post("/api/studies/s1/fielding/links", json={}).status_code == 200

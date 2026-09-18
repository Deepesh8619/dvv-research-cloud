"""Re-read (current parser, no model call) and split-by-item endpoints.

Fixture style matches test_review_map_routes.py: tmp sqlite, dependency
overrides for get_db/get_current_user, a seeded StudyDB + QuestionnaireDB.
"""

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
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
)
from src.engines.questionnaire.extractor import reread_question


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


def _seed(session, survey: SurveyDefinition) -> None:
    db = session()
    now = datetime.now().isoformat()
    db.add(StudyDB(id="s1", client_name="T", status="classifying", created_at=now,
                   updated_at=now, study_metadata={}, approval_chain={}))
    db.add(QuestionnaireDB(
        id="qn1", study_id="s1", status="in_review", source_filename="q.docx", version=1,
        definition_json=survey.to_json(), definition_path=None,
        approved_qids=[], created_at=now, updated_at=now,
    ))
    db.commit()
    db.close()


# --- Re-read ------------------------------------------------------------


def test_reread_splits_a_bare_terminate_label_into_a_label_and_terminate_if(client):
    """An old parse left "Male TERMINATE" as one option label with no
    terminate_if — exactly the bug prose_directives.py's bare-TERMINATE
    handling now fixes. Re-read should replay that fix without any model call."""
    test_client, session = client
    source = "S1. Are you male or female?\nMale TERMINATE\nFemale"
    old = Question(
        qid="S1", text="Are you male or female?", type=QuestionType.SINGLE,
        options=[Option(code=1, label="Male TERMINATE"), Option(code=2, label="Female")],
        source_text=source, extraction_method="prose_directives",
    )
    _seed(session, SurveyDefinition(
        study_id="s1", title="t", source="extracted", status="in_review",
        blocks=[Block(block_id="B1", question_ids=["S1"])], questions=[old],
    ))

    resp = test_client.post("/api/studies/s1/questionnaire/questions/S1/reread")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "options" in body["changed"]
    assert "terminate_if" in body["changed"]
    assert body["qid"] == "S1"
    labels = [o["label"] for o in body["options"]]
    assert labels == ["Male", "Female"]
    assert body["terminate_if"]["values"] == [1]


def test_reread_with_no_improvement_is_refused_and_leaves_the_question_unchanged(client):
    """A question the current parser reads exactly the same way it was stored
    has nothing to gain from a re-read — refused, not silently re-saved."""
    test_client, session = client
    source = "Q9. Do you smoke?\nYes\nNo"
    # Seed with exactly what `reread_question` itself would produce, so a
    # second pass over the same source_text is guaranteed to find no diff —
    # the parser is deterministic, so this needs no hand-matching of fields.
    already_current = reread_question(
        Question(qid="Q9", text="", source_text=source)
    )
    assert already_current is not None  # sanity: the fixture text does parse
    _seed(session, SurveyDefinition(
        study_id="s1", title="t", source="extracted", status="in_review",
        blocks=[Block(block_id="B1", question_ids=["Q9"])], questions=[already_current],
    ))

    resp = test_client.post("/api/studies/s1/questionnaire/questions/Q9/reread")
    assert resp.status_code == 422

    still = test_client.get("/api/studies/s1/questionnaire/questions/Q9").json()
    assert still["text"] == already_current.text
    assert [o["label"] for o in still["options"]] == [o.label for o in already_current.options]


def test_reread_all_updates_only_the_questions_with_something_to_gain(client):
    test_client, session = client
    stale = Question(
        qid="S1", text="Are you male or female?", type=QuestionType.SINGLE,
        options=[Option(code=1, label="Male TERMINATE"), Option(code=2, label="Female")],
        source_text="S1. Are you male or female?\nMale TERMINATE\nFemale",
    )
    current = reread_question(Question(qid="Q9", text="", source_text="Q9. Do you smoke?\nYes\nNo"))
    _seed(session, SurveyDefinition(
        study_id="s1", title="t", source="extracted", status="in_review",
        blocks=[Block(block_id="B1", question_ids=["S1", "Q9"])], questions=[stale, current],
    ))

    resp = test_client.post("/api/studies/s1/questionnaire/questions/reread-all")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["n_updated"] == 1
    assert body["updated"][0]["qid"] == "S1"


# --- Split ----------------------------------------------------------------


def _carousel_survey() -> SurveyDefinition:
    brands = [Option(code=1, label="Bubble"), Option(code=2, label="CeraVe")]
    q1 = Question(qid="Q1", text="Do you use skincare?",
                  options=[Option(code=1, label="Yes"), Option(code=2, label="No")])
    q6 = Question(qid="Q6", text="For each category, which brands have you used?",
                  type=QuestionType.MULTI, options=list(brands))
    q7 = Question(qid="Q7", text="Anything else?", type=QuestionType.OPEN_TEXT)
    return SurveyDefinition(
        study_id="s1", title="t", source="extracted", status="in_review",
        blocks=[Block(block_id="B1", question_ids=["Q1", "Q6", "Q7"])],
        questions=[q1, q6, q7],
    )


def test_split_creates_one_copy_per_item_with_show_if_and_preserves_order(client):
    test_client, session = client
    _seed(session, _carousel_survey())

    resp = test_client.post(
        "/api/studies/s1/questionnaire/questions/Q6/split",
        json={
            "items": [
                {"label": "Cleansers", "show_if": {"op": "eq", "qid": "Q1", "value": 1}},
                {"label": "Moisturizers"},
            ]
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["into"] == ["Q6_1", "Q6_2"]
    assert body["questions"][0]["show_if"]["qid"] == "Q1"
    assert body["questions"][0]["text"] == (
        "For each category, which brands have you used? (Cleansers)"
    )
    # The second item gave no options of its own — it keeps the template's.
    assert [o["label"] for o in body["questions"][1]["options"]] == ["Bubble", "CeraVe"]
    assert body["questions"][1]["show_if"] is None

    listed = test_client.get("/api/studies/s1/questionnaire/questions").json()["questions"]
    qids = [q["qid"] for q in listed]
    # Q6 is gone, its two copies sit exactly where it was — block/list order
    # preserved around the split, not appended at the end.
    assert qids == ["Q1", "Q6_1", "Q6_2", "Q7"]


def test_splitting_the_same_question_twice_is_refused(client):
    test_client, session = client
    _seed(session, _carousel_survey())
    items = {"items": [{"label": "A"}, {"label": "B"}]}

    first = test_client.post("/api/studies/s1/questionnaire/questions/Q6/split", json=items)
    assert first.status_code == 200

    second = test_client.post("/api/studies/s1/questionnaire/questions/Q6/split", json=items)
    assert second.status_code == 422


def test_split_needs_at_least_two_items(client):
    test_client, session = client
    _seed(session, _carousel_survey())
    resp = test_client.post(
        "/api/studies/s1/questionnaire/questions/Q6/split",
        json={"items": [{"label": "Only one"}]},
    )
    assert resp.status_code == 422

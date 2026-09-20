"""Finding a question by what it MEANS, not by the words it happens to use.

Every test here runs with no model installed — the lexical fallback — because
that is the production state today and the feature has to be useful in it.
"""

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.db import Base, ProjectChunkDB
from src.core.survey_definition import (
    Block,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
)
from src.engines.rag import embeddings
from src.engines.rag.store import (
    chunks_for_survey,
    ensure_index,
    ensure_verbatim_index,
    search,
)


@pytest.fixture(autouse=True)
def no_local_model(monkeypatch):
    """No Ollama: every test measures what a bare server can do."""
    monkeypatch.setattr(embeddings, "_ollama", lambda texts, timeout: None)


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _survey(pay_text: str = "How much would you be willing to pay for a moisturiser?"):
    questions = [
        Question(qid="Q1", text="What is your age?", type=QuestionType.NUMERIC),
        Question(
            qid="Q2",
            text="Which of these skincare brands have you heard of?",
            type=QuestionType.MULTI,
            options=[Option(code=1, label="Bubble"), Option(code=5, label="CeraVe")],
        ),
        Question(qid="Q3", text=pay_text, type=QuestionType.NUMERIC),
    ]
    return SurveyDefinition(
        study_id="S1", source="extracted", status="approved",
        blocks=[Block(block_id="B", question_ids=[q.qid for q in questions])],
        questions=questions,
        metadata={"study_overview": {"sample_size": "n=400 skincare buyers"}},
    )


def test_a_question_is_indexed_with_its_wording_and_its_answers():
    chunks = {c["ref"]: c["text"] for c in chunks_for_survey(_survey())}
    assert "Which of these skincare brands" in chunks["Q2"]
    assert "Bubble; CeraVe" in chunks["Q2"]
    assert "n=400" in chunks["study_overview"]


def test_the_project_goes_into_the_database_once_and_is_not_rebuilt_unchanged(db):
    first = ensure_index(db, "S1", _survey())
    assert first == {"indexed": 4, "model": embeddings.LEXICAL, "rebuilt": True}
    assert db.query(ProjectChunkDB).count() == 4
    assert json.loads(db.query(ProjectChunkDB).first().vector_json)

    assert ensure_index(db, "S1", _survey())["rebuilt"] is False


def test_wording_that_changed_is_re_indexed_not_answered_from_the_old_text(db):
    ensure_index(db, "S1", _survey())
    ensure_index(db, "S1", _survey("How much would you pay for a night cream?"))
    texts = [row.text for row in db.query(ProjectChunkDB).all()]
    assert any("night cream" in t for t in texts)
    assert not any("moisturiser" in t for t in texts)
    assert db.query(ProjectChunkDB).count() == 4  # replaced, not added to


def test_a_search_finds_the_question_a_client_described_in_their_own_words(db):
    survey = _survey()
    ensure_index(db, "S1", survey)
    found = search(db, "S1", "brands people know", survey=survey)
    assert found["matches"][0]["ref"] == "Q2"
    assert found["model"] == embeddings.LEXICAL


def test_a_search_on_a_project_that_was_never_indexed_indexes_it(db):
    survey = _survey()
    found = search(db, "S1", "what is your age", survey=survey)
    assert found["matches"][0]["ref"] == "Q1"
    assert db.query(ProjectChunkDB).count() == 4


def test_vectors_from_two_different_makers_are_never_compared(db):
    """A model appearing (or disappearing) must rebuild, not silently score
    one embedding space against another — that returns confident nonsense."""
    ensure_index(db, "S1", _survey())
    assert search(db, "S1", "age", survey=None)["matches"]  # same maker: fine

    for row in db.query(ProjectChunkDB).all():
        row.model = "ollama:nomic-embed-text"
    db.commit()

    # No survey to rebuild from: it says so rather than answering.
    blocked = search(db, "S1", "age", survey=None)
    assert blocked["matches"] == [] and "re-index" in blocked["note"]
    # With the questionnaire in hand it rebuilds and answers.
    assert search(db, "S1", "what is your age", survey=_survey())["matches"]


def test_one_project_never_sees_another_projects_content(db):
    ensure_index(db, "S1", _survey())
    other = _survey()
    other.study_id = "S2"
    ensure_index(db, "S2", other)
    assert {row.study_id for row in db.query(ProjectChunkDB).all()} == {"S1", "S2"}
    assert len(search(db, "S1", "age", survey=_survey())["matches"]) <= 4


def test_an_empty_query_returns_nothing_rather_than_everything(db):
    ensure_index(db, "S1", _survey())
    assert search(db, "S1", "   ")["matches"] == []


def test_a_lexical_vector_tolerates_spelling_and_word_order():
    close = embeddings.similarity(
        embeddings.lexical_vector("skincare brands you have heard of"),
        embeddings.lexical_vector("brands of skincare you've herd of"),
    )
    far = embeddings.similarity(
        embeddings.lexical_vector("skincare brands you have heard of"),
        embeddings.lexical_vector("what is your annual household income"),
    )
    assert close > far
    # It knows no synonyms, and says so by scoring them apart. Being honest
    # about that is why the maker is stored beside every vector.
    assert embeddings.similarity(
        embeddings.lexical_vector("automobile"), embeddings.lexical_vector("car")
    ) < close


def test_the_local_model_is_used_when_it_answers_and_never_blocks_when_it_does_not(
    monkeypatch,
):
    monkeypatch.setattr(embeddings, "_ollama", lambda texts, timeout: [[1.0, 0.0]] * len(texts))
    vectors, model = embeddings.embed(["a", "b"])
    assert model == "ollama:nomic-embed-text" and len(vectors) == 2

    monkeypatch.setattr(embeddings, "_ollama", lambda texts, timeout: None)
    vectors, model = embeddings.embed(["a"])
    assert model == embeddings.LEXICAL and len(vectors[0]) == embeddings.LEXICAL_DIM


def test_a_query_and_a_document_are_embedded_as_what_they_are(monkeypatch):
    """nomic-embed-text is trained with a task prefix. Without it, "net promoter
    score" ranked an age question above "how likely are you to recommend" on the
    real model — so the prefix is behaviour, not decoration."""
    seen: list[str] = []

    def record(texts, timeout):
        seen.extend(texts)
        return [[1.0, 0.0]] * len(texts)

    monkeypatch.setattr(embeddings, "_ollama", record)
    embeddings.embed(["How much would you pay?"], kind="document")
    embeddings.embed(["price sensitivity"], kind="query")
    assert seen == ["search_document: How much would you pay?", "search_query: price sensitivity"]

    monkeypatch.setenv("DVV_EMBED_MODEL", "all-minilm")  # a model with no prefixes
    seen.clear()
    embeddings.embed(["plain"], kind="query")
    assert seen == ["plain"]


def _fielded(db, study_id="S1", statuses=(10, 10, 20)):
    """A link with three interviews: two completes and one screened out."""
    from api.db import FieldingLinkDB, RespondentAnswerDB, RespondentSessionDB

    db.add(FieldingLinkDB(id="L1", study_id=study_id, token="t", status="open"))
    said = [
        "it left my skin really greasy and shiny by lunchtime",
        "the smell was lovely but the pump kept jamming",
        "i was screened out before i could say anything useful",
    ]
    for i, (status, text) in enumerate(zip(statuses, said, strict=True)):
        db.add(RespondentSessionDB(id=f"s{i}", fielding_link_id="L1", status_code=status))
        db.add(RespondentAnswerDB(id=f"a{i}", session_id=f"s{i}", qid="Q4", value=text))
        db.add(RespondentAnswerDB(id=f"n{i}", session_id=f"s{i}", qid="Q1", value=30))
    db.commit()


def _survey_with_open_end():
    survey = _survey()
    survey.questions.append(
        Question(qid="Q4", text="What did you dislike about it?", type=QuestionType.OPEN_TEXT)
    )
    survey.blocks[0].question_ids.append("Q4")
    return survey


def test_only_completed_interviews_are_indexed(db):
    """A screened-out respondent's answer is not part of what the study found;
    quoting one as "what people said" would report somebody it excluded."""
    from api.db import ProjectChunkDB

    _fielded(db)
    result = ensure_verbatim_index(db, "S1", _survey_with_open_end())
    assert result["indexed"] == 2 and result["remaining"] == 0
    texts = [r.text for r in db.query(ProjectChunkDB).filter_by(kind="verbatim").all()]
    assert not any("screened out" in t for t in texts)
    assert all("What did you dislike" in t for t in texts)  # the question travels along


def test_verbatims_are_added_as_they_arrive_and_never_re_embedded(db):
    from api.db import ProjectChunkDB, RespondentAnswerDB, RespondentSessionDB

    survey = _survey_with_open_end()
    _fielded(db)
    ensure_verbatim_index(db, "S1", survey)
    assert ensure_verbatim_index(db, "S1", survey)["indexed"] == 0  # nothing new: no work

    db.add(RespondentSessionDB(id="s9", fielding_link_id="L1", status_code=10))
    db.add(RespondentAnswerDB(id="a9", session_id="s9", qid="Q4",
                              value="the texture is far too thick for summer"))
    db.commit()
    assert ensure_verbatim_index(db, "S1", survey)["indexed"] == 1
    assert db.query(ProjectChunkDB).filter_by(kind="verbatim").count() == 3


def test_re_reading_the_questionnaire_does_not_throw_the_verbatims_away(db):
    from api.db import ProjectChunkDB

    _fielded(db)
    ensure_index(db, "S1", _survey_with_open_end())
    ensure_verbatim_index(db, "S1", _survey_with_open_end())
    changed = _survey_with_open_end()
    changed["Q1"].text = "How old are you?"
    ensure_index(db, "S1", changed)
    assert db.query(ProjectChunkDB).filter_by(kind="verbatim").count() == 2


def test_a_verbatim_search_returns_what_people_said_and_not_the_questionnaire(db):
    survey = _survey_with_open_end()
    _fielded(db)
    ensure_verbatim_index(db, "S1", survey)
    ensure_index(db, "S1", survey)
    found = search(db, "S1", "greasy skin", survey=survey, kinds=("verbatim",))
    assert found["matches"], found
    assert all(m["kind"] == "verbatim" for m in found["matches"])
    assert "greasy" in found["matches"][0]["text"]
    # And the questionnaire search still returns questions, not answers.
    assert all(m["kind"] != "verbatim" for m in search(db, "S1", "age", survey=survey)["matches"])


def test_a_one_word_answer_is_not_indexed(db):
    from api.db import ProjectChunkDB

    _fielded(db)
    from api.db import RespondentAnswerDB

    for row in db.query(RespondentAnswerDB).filter_by(qid="Q4"):
        row.value = "n/a"
    db.commit()
    ensure_verbatim_index(db, "S1", _survey_with_open_end())
    assert db.query(ProjectChunkDB).filter_by(kind="verbatim").count() == 0


def test_a_questionnaire_with_no_open_ends_says_so(db):
    result = ensure_verbatim_index(db, "S1", _survey())
    assert result["indexed"] == 0 and "no open-ended" in result["note"]

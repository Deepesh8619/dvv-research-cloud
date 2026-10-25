"""G1: the questionnaire writes its graph."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.db import Base, GraphEdgeDB, GraphNodeDB
from src.core.survey_definition import (
    Block,
    Expression,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
)
from src.engines.questionnaire.graph_sync import sync_questionnaire
from src.platform import graph


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/g.db")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _survey() -> SurveyDefinition:
    q1 = Question(qid="Q1", text="Age?", type=QuestionType.NUMERIC,
                  terminate_if=Expression(op="lt", qid="Q1", value=18))
    q2 = Question(qid="Q2", text="Coffee?", type=QuestionType.SINGLE,
                  options=[Option(code=1, label="Yes"), Option(code=2, label="No")])
    q3 = Question(qid="Q3", text="Brand?", show_if=Expression(op="eq", qid="Q2", value=1))
    q4 = Question(qid="Q4", text="Pick brands", type=QuestionType.MULTI,
                  options=[Option(code=1, label="A")])
    q5 = Question(qid="Q5", text="x" * 300, type=QuestionType.MULTI,
                  options=[Option(code=1, label="A")],
                  validation={"techniques": {"masks": [
                      {"source_qid": "Q4", "mode": "selected", "exclude_qid": "Q2"}]}})
    q6 = Question(qid="Q6", text="Rate", validation={"techniques": {"pipes": [
        {"source_qid": "Q5"}]}})
    q7 = Question(qid="Q7", text="Segment", options=[
        Option(code=1, label="Heavy", derive_if=Expression(op="eq", qid="Q3", value=1))])
    qs = [q1, q2, q3, q4, q5, q6, q7]
    return SurveyDefinition(study_id="s1", title="t", source="authored", status="approved",
                            version=1, questions=qs,
                            blocks=[Block(block_id="B1", question_ids=[q.qid for q in qs])])


def _edges(db):
    return {(e.src.split(":")[-1], e.kind, e.dst.split(":")[-1]) for e in db.query(GraphEdgeDB)}


def test_edges_and_nodes(db):
    out = sync_questionnaire(db, "s1", _survey())
    db.commit()
    assert out["nodes"] == 7
    assert _edges(db) == {
        ("Q1", "terminates_on", "Q1"), ("Q3", "shown_if", "Q2"),
        ("Q5", "masked_by", "Q4"), ("Q5", "masked_by", "Q2"),
        ("Q6", "piped_from", "Q5"), ("Q7", "derived_from", "Q3")}
    assert out["edges"] == 6
    n5 = db.get(GraphNodeDB, graph.node_id("s1", "question", "Q5"))
    assert len(n5.label) == 200 and '"block": "B1"' in n5.props


def test_impact_and_resync_drops_removed(db):
    s = _survey()
    sync_questionnaire(db, "s1", s)
    assert {n["ref"] for n in graph.impact(db, "s1", graph.node_id("s1", "question", "Q4"))} \
        == {"Q5", "Q6"}
    s.questions = [q for q in s.questions if q.qid != "Q6"]
    s.blocks[0].question_ids.remove("Q6")
    out = sync_questionnaire(db, "s1", s)
    db.commit()
    assert db.get(GraphNodeDB, graph.node_id("s1", "question", "Q6")) is None
    assert not any(k == "piped_from" for _, k, _ in _edges(db))
    assert out["nodes"] == 6
    sync_questionnaire(db, "s1", s)  # idempotent
    assert db.query(GraphEdgeDB).count() == out["edges"]

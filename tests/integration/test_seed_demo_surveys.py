"""Seed script idempotency: running seed() twice yields exactly 6 of each row.

Exercises the seeder's core function against a throwaway SQLite DB (no HTTP), the
same way an operator would run scripts/seed_demo_surveys.py. Uses the real
scripts/demo_questionnaires.json fixture so the 20-questions-per-snapshot check is
meaningful.
"""

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.db import (
    Base,
    FieldingLinkDB,
    QuestionnaireDB,
    StudyDB,
)
from scripts.seed_demo_surveys import FIXTURE, seed
from src.core.survey_definition import SurveyDefinition


@pytest.fixture
def db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path}/seed.db", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    yield session
    session.close()


def test_seed_is_idempotent(db):
    fixtures = json.loads(FIXTURE.read_text())
    n = len(fixtures)
    assert n == 6

    seed(db, fixtures)
    seed(db, fixtures)  # re-run must not duplicate anything

    assert db.query(StudyDB).count() == n
    assert db.query(QuestionnaireDB).count() == n
    links = db.query(FieldingLinkDB).all()
    assert len(links) == n

    for q in db.query(QuestionnaireDB).all():
        assert q.status == "approved"
        assert len(q.approved_qids) == 20

    for link in links:
        assert link.status == "open"
        snapshot = SurveyDefinition.from_json(link.survey_json)
        assert len(snapshot.questions) == 20

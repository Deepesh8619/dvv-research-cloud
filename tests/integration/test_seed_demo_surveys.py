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
    UserDB,
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


def test_seed_never_creates_a_user_row(db):
    """Regression for the production-admin bug: seeding must NEVER touch users.

    The dangerous version ran init_db() -> seed_admin_user(), minting a
    default-password owner on prod. seed() itself must leave the users table empty.
    """
    fixtures = json.loads(FIXTURE.read_text())
    assert db.query(UserDB).count() == 0
    seed(db, fixtures)
    assert db.query(UserDB).count() == 0


def test_refresh_definitions_updates_pinned_snapshot(db):
    """--refresh-definitions overwrites the definition AND the open link snapshot
    in place; without it, an already-pinned snapshot is left untouched."""
    fixtures = json.loads(FIXTURE.read_text())
    seed(db, fixtures)

    seg = fixtures[0]["segment_slug"]
    study_id = f"demo-{seg}"
    original_theme = fixtures[0]["survey"]["metadata"]["theme"]

    def link_theme() -> str:
        link = (
            db.query(FieldingLinkDB)
            .filter(FieldingLinkDB.study_id == study_id, FieldingLinkDB.status == "open")
            .first()
        )
        return SurveyDefinition.from_json(link.survey_json).metadata.get("theme")

    def quest_theme() -> str:
        q = (
            db.query(QuestionnaireDB)
            .filter(QuestionnaireDB.study_id == study_id, QuestionnaireDB.status == "approved")
            .first()
        )
        return SurveyDefinition.from_json(q.definition_json).metadata.get("theme")

    assert link_theme() == original_theme

    # Mutate the fixture and re-seed WITHOUT refresh: pinned snapshot unchanged.
    fixtures[0]["survey"]["metadata"]["theme"] = "CHANGED"
    seed(db, fixtures, refresh=False)
    assert link_theme() == original_theme
    assert quest_theme() == original_theme

    # Re-seed WITH refresh: both the definition and the link snapshot update.
    seed(db, fixtures, refresh=True)
    assert link_theme() == "CHANGED"
    assert quest_theme() == "CHANGED"

    # Still idempotent — no duplicate studies/links from the refresh runs.
    assert db.query(StudyDB).count() == len(fixtures)
    assert db.query(FieldingLinkDB).count() == len(fixtures)

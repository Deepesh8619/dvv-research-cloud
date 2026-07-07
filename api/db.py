"""Database setup and models."""

import logging
import secrets
from datetime import datetime

from sqlalchemy import JSON, Boolean, Column, Integer, String, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session, sessionmaker

from api.config import DATABASE_URL

logger = logging.getLogger(__name__)

# check_same_thread=False is a SQLite-only pysqlite argument (needed because
# FastAPI's request-per-thread model would otherwise reject a connection
# created on a different thread). Passing it to psycopg2 (Postgres) raises
# TypeError: connect() got an unexpected keyword argument — so it's applied
# only when DATABASE_URL is actually SQLite, letting the same code run against
# either backend (SQLite locally, Postgres once deployed).
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=_connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class UserDB(Base):
    """User database model for internal accounts."""

    __tablename__ = "users"

    id = Column(String, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    name = Column(String)
    password_hash = Column(String)
    role = Column(String, default="analyst")  # owner, analyst, reviewer
    created_at = Column(String)
    is_active = Column(Boolean, default=True)


class ShareLinkDB(Base):
    """Share link database model for public access."""

    __tablename__ = "share_links"

    token = Column(String, primary_key=True, index=True)
    study_id = Column(String, index=True)
    created_by = Column(String)  # user email
    created_at = Column(String)
    revoked = Column(Boolean, default=False)


class StudyDB(Base):
    """Study database model."""

    __tablename__ = "studies"

    id = Column(String, primary_key=True, index=True)
    client_name = Column(String, index=True)
    status = Column(String, default="classifying", index=True)
    created_at = Column(String)
    updated_at = Column(String)
    study_metadata = Column(JSON, default={})
    approval_chain = Column(JSON, default={})
    excel_path = Column(String, nullable=True)
    pptx_path = Column(String, nullable=True)


class ChatMessageDB(Base):
    """Chat message history for a study."""

    __tablename__ = "chat_messages"

    id = Column(String, primary_key=True, index=True)
    study_id = Column(String, index=True)
    role = Column(String)  # "user" or "assistant"
    content = Column(String)
    tools = Column(JSON, default=None)  # structured tool calls
    created_at = Column(String)


class QuestionnaireDB(Base):
    """Metadata + review state for one extracted questionnaire.

    The full nested `SurveyDefinition` lives as JSON on disk (in the study's
    directory, at `definition_path`), matching the datamap.json / crosstabs.json
    convention in api/routes/chat.py — the DataMap and computed deck are
    likewise structured JSON on disk with a DB row tracking only metadata. The
    SurveyDefinition dataclass (src/core/survey_definition.py) is the source of
    truth for its own nested structure; re-modelling it as SQL columns would
    fork that schema. So this row carries only what a listing / review dashboard
    queries by: status, source file, version, timestamps, the on-disk path, and
    the set of per-question approvals.

    `study_id` is a plain string reference (not a DB-level FK constraint),
    matching how ShareLinkDB / ChatMessageDB reference studies in this codebase.
    """

    __tablename__ = "questionnaires"

    id = Column(String, primary_key=True, index=True)
    study_id = Column(String, index=True)
    status = Column(String, default="processing", index=True)  # processing|draft|in_review|approved
    source_filename = Column(String)
    version = Column(Integer, default=1)
    definition_path = Column(String, nullable=True)  # on-disk SurveyDefinition JSON
    # Per-question approval is tracked here (API-layer review state) rather than
    # on the Question dataclass: adding a field to src/core/survey_definition.py
    # would touch that module's existing tests, and approval is a review-workflow
    # concern that belongs alongside the questionnaire's status, not baked into
    # the persisted instrument. A list of approved qids keeps it additive.
    approved_qids = Column(JSON, default=list)
    created_at = Column(String)
    updated_at = Column(String)


def init_db():
    """Initialize database tables."""
    Base.metadata.create_all(bind=engine)
    seed_admin_user()


def get_db():
    """Get database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_study(
    db: Session,
    study_id: str,
    client_name: str,
) -> StudyDB:
    """Create a new study in database."""
    study = StudyDB(
        id=study_id,
        client_name=client_name,
        status="classifying",
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat(),
        study_metadata={},
        approval_chain={},
    )
    db.add(study)
    db.commit()
    db.refresh(study)
    return study


def get_study(db: Session, study_id: str) -> StudyDB:
    """Get study by ID."""
    return db.query(StudyDB).filter(StudyDB.id == study_id).first()


def list_studies(db: Session, skip: int = 0, limit: int = 20) -> tuple[list[StudyDB], int]:
    """List all studies with pagination."""
    query = db.query(StudyDB).order_by(StudyDB.created_at.desc())
    total = query.count()
    studies = query.offset(skip).limit(limit).all()
    return studies, total


def update_study_status(db: Session, study_id: str, status: str, metadata: dict = None):
    """Update study status and metadata."""
    study = get_study(db, study_id)
    if study:
        study.status = status
        study.updated_at = datetime.now().isoformat()
        if metadata:
            study.study_metadata = {**(study.study_metadata or {}), **metadata}
        db.commit()
        db.refresh(study)
    return study


def approve_gate(db: Session, study_id: str, gate: str, approved_by: str):
    """Mark a gate as approved."""
    study = get_study(db, study_id)
    if study:
        # Copy: SQLAlchemy only persists JSON columns on new-object assignment
        approval_chain = dict(study.approval_chain or {})
        approval_chain[gate] = {
            "approved_by": approved_by,
            "approved_at": datetime.now().isoformat(),
        }
        study.approval_chain = approval_chain
        study.updated_at = datetime.now().isoformat()
        db.commit()
        db.refresh(study)
    return study


def update_study_paths(db: Session, study_id: str, excel_path: str = None, pptx_path: str = None):
    """Update study output file paths."""
    study = get_study(db, study_id)
    if study:
        if excel_path:
            study.excel_path = excel_path
        if pptx_path:
            study.pptx_path = pptx_path
        study.updated_at = datetime.now().isoformat()
        db.commit()
        db.refresh(study)
    return study


def get_user_by_email(db: Session, email: str) -> UserDB:
    """Get user by email."""
    return db.query(UserDB).filter(UserDB.email == email).first()


def create_user(
    db: Session,
    email: str,
    name: str,
    password_hash: str,
    role: str = "analyst",
) -> UserDB:
    """Create a new user."""
    from uuid import uuid4

    user = UserDB(
        id=f"user_{uuid4().hex[:8]}",
        email=email,
        name=name,
        password_hash=password_hash,
        role=role,
        created_at=datetime.now().isoformat(),
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def list_users(db: Session) -> list[UserDB]:
    """List all active users."""
    return db.query(UserDB).filter(UserDB.is_active).all()


def create_share_link(db: Session, study_id: str, created_by: str) -> ShareLinkDB:
    """Create a new share link for a study."""
    token = secrets.token_urlsafe(24)
    link = ShareLinkDB(
        token=token,
        study_id=study_id,
        created_by=created_by,
        created_at=datetime.now().isoformat(),
        revoked=False,
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    return link


def get_share_link(db: Session, token: str) -> ShareLinkDB:
    """Get a share link by token."""
    return db.query(ShareLinkDB).filter(ShareLinkDB.token == token).first()


def get_active_share_link_for_study(db: Session, study_id: str) -> ShareLinkDB:
    """Get the active (not revoked) share link for a study."""
    return (
        db.query(ShareLinkDB)
        .filter(ShareLinkDB.study_id == study_id, ~ShareLinkDB.revoked)
        .first()
    )


def revoke_share_link(db: Session, token: str) -> ShareLinkDB:
    """Revoke a share link."""
    link = get_share_link(db, token)
    if link:
        link.revoked = True
        db.commit()
        db.refresh(link)
    return link


def list_chat_messages(db: Session, study_id: str, limit: int = 50) -> list[ChatMessageDB]:
    """List chat messages for a study, most recent last."""
    from sqlalchemy import desc
    return (
        db.query(ChatMessageDB)
        .filter(ChatMessageDB.study_id == study_id)
        .order_by(desc(ChatMessageDB.created_at))
        .limit(limit)
        .all()[::-1]  # reverse back to oldest-first
    )


def create_chat_message(
    db: Session,
    study_id: str,
    role: str,
    content: str,
    tools: dict | None = None,
) -> ChatMessageDB:
    """Create a new chat message."""
    from uuid import uuid4
    msg = ChatMessageDB(
        id=f"msg_{uuid4().hex[:8]}",
        study_id=study_id,
        role=role,
        content=content,
        tools=tools,
        created_at=datetime.now().isoformat(),
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg


def create_questionnaire(
    db: Session,
    study_id: str,
    source_filename: str,
    version: int = 1,
) -> QuestionnaireDB:
    """Create a questionnaire row in the initial 'processing' state."""
    from uuid import uuid4

    questionnaire = QuestionnaireDB(
        id=f"quest_{uuid4().hex[:8]}",
        study_id=study_id,
        status="processing",
        source_filename=source_filename,
        version=version,
        definition_path=None,
        approved_qids=[],
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat(),
    )
    db.add(questionnaire)
    db.commit()
    db.refresh(questionnaire)
    return questionnaire


def get_questionnaire(db: Session, questionnaire_id: str) -> QuestionnaireDB:
    """Get a questionnaire by ID."""
    return db.query(QuestionnaireDB).filter(QuestionnaireDB.id == questionnaire_id).first()


def list_questionnaires_for_study(db: Session, study_id: str) -> list[QuestionnaireDB]:
    """List questionnaires for a study, most recent first."""
    from sqlalchemy import desc

    return (
        db.query(QuestionnaireDB)
        .filter(QuestionnaireDB.study_id == study_id)
        .order_by(desc(QuestionnaireDB.created_at))
        .all()
    )


def update_questionnaire_status(
    db: Session,
    questionnaire_id: str,
    status: str,
    definition_path: str = None,
) -> QuestionnaireDB:
    """Update a questionnaire's status and, optionally, its on-disk path."""
    questionnaire = get_questionnaire(db, questionnaire_id)
    if questionnaire:
        questionnaire.status = status
        if definition_path is not None:
            questionnaire.definition_path = definition_path
        questionnaire.updated_at = datetime.now().isoformat()
        db.commit()
        db.refresh(questionnaire)
    return questionnaire


def set_questionnaire_approved_qids(
    db: Session, questionnaire_id: str, approved_qids: list[str]
) -> QuestionnaireDB:
    """Replace the set of approved qids (JSON column needs whole-value reassign)."""
    questionnaire = get_questionnaire(db, questionnaire_id)
    if questionnaire:
        # SQLAlchemy only persists a JSON column on new-object assignment, not
        # in-place mutation — assign a fresh list (mirrors approve_gate above).
        questionnaire.approved_qids = list(approved_qids)
        questionnaire.updated_at = datetime.now().isoformat()
        db.commit()
        db.refresh(questionnaire)
    return questionnaire


def seed_admin_user():
    """Ensure ADMIN_EMAIL exists with ADMIN_PASSWORD's current hash.

    Deliberately an upsert, not create-once: on a persistent database
    (Postgres in production), a user created on the container's first boot
    survives every later redeploy. If ADMIN_PASSWORD is only read at
    creation time, changing it in the hosting dashboard and redeploying
    silently does nothing — the login still checks the original hash, and
    "I typed the right password" is genuinely true while still failing.
    Env vars are meant to be the source of truth for admin credentials, so
    every startup re-syncs the existing admin's password/name to match them
    rather than assuming the first boot got it right.
    """
    from api.auth import hash_password
    from api.config import ADMIN_EMAIL, ADMIN_NAME, ADMIN_PASSWORD

    db = SessionLocal()
    try:
        existing = db.query(UserDB).filter(UserDB.email == ADMIN_EMAIL).first()
        if existing:
            existing.password_hash = hash_password(ADMIN_PASSWORD)
            existing.name = ADMIN_NAME
            db.commit()
            logger.info(f"Admin user {ADMIN_EMAIL} password synced to current ADMIN_PASSWORD")
            return

        if db.query(UserDB).first():
            # Other users already exist (e.g. invited by the admin) but not
            # one at ADMIN_EMAIL — create it alongside them rather than
            # skipping, so changing ADMIN_EMAIL still produces a working login.
            pass

        # Create admin user
        admin = create_user(
            db,
            email=ADMIN_EMAIL,
            name=ADMIN_NAME,
            password_hash=hash_password(ADMIN_PASSWORD),
            role="owner",
        )
        logger.warning(
            f"⚠️  ADMIN USER CREATED: {ADMIN_EMAIL} / {ADMIN_PASSWORD}\n"
            f"   Change this password immediately in /admin or via API!\n"
            f"   User ID: {admin.id}"
        )
    finally:
        db.close()

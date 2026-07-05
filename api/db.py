"""Database setup and models."""

import logging
import secrets
from datetime import datetime

from sqlalchemy import JSON, Boolean, Column, String, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session, sessionmaker

from api.config import DATABASE_URL

logger = logging.getLogger(__name__)

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
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


def seed_admin_user():
    from api.auth import hash_password
    from api.config import ADMIN_EMAIL, ADMIN_NAME, ADMIN_PASSWORD

    db = SessionLocal()
    try:
        # Check if any users exist
        if db.query(UserDB).first():
            return

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

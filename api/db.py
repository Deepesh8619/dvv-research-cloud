"""Database setup and models."""

import logging
import secrets
from datetime import datetime

from sqlalchemy import JSON, Boolean, Column, Integer, String, Text, create_engine
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
    # True while the password_hash is whatever seed_admin_user() last synced
    # from the ADMIN_PASSWORD env var. Set to False the moment the password is
    # deliberately changed through the app (emergency-reset), which tells
    # seed_admin_user() to STOP re-syncing on the next cold start — otherwise
    # Render's free-tier idle spin-down + restart silently reverts a
    # just-set password back to the env var value and locks the user out.
    # Defaults True so pre-existing rows (whose hash genuinely WAS last synced
    # from the env var) keep the original auto-sync behavior until manually
    # changed. NULL on a legacy row is treated as True (see seed_admin_user).
    password_synced_from_env = Column(Boolean, default=True)


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

    The full nested `SurveyDefinition` is persisted as a JSON *string* in the
    `definition_json` TEXT column (T17) — durable across deploys/restarts, since
    Render's free tier wipes the on-disk `/app/studies` where it previously
    lived. An approved 80-question questionnaire embodies hours of human review
    labor, which must survive a restart; a DB column is the durable home.

    Column type is TEXT, not SQLAlchemy JSON: `SurveyDefinition.to_json()`/
    `from_json()` already own the (de)serialization contract as string-in/
    string-out, and TEXT behaves identically on SQLite (tests) and Postgres
    (prod). SQLAlchemy's JSON type would double-parse and can differ subtly
    between the two backends — TEXT keeps the dataclass the single source of
    truth for its own nested structure without re-modelling it as SQL columns.

    `definition_path` is kept but is now vestigial/optional local-dev metadata
    (the ephemeral on-disk copy), no longer authoritative. On read, if
    `definition_json` is empty but a still-present file exists at
    `definition_path`, the column is backfilled from it (see routes helper).

    `study_id` is a plain string reference (not a DB-level FK constraint),
    matching how ShareLinkDB / ChatMessageDB reference studies in this codebase.
    """

    __tablename__ = "questionnaires"

    id = Column(String, primary_key=True, index=True)
    study_id = Column(String, index=True)
    status = Column(String, default="processing", index=True)  # processing|draft|in_review|approved
    source_filename = Column(String)
    version = Column(Integer, default=1)
    definition_json = Column(Text, nullable=True)  # authoritative SurveyDefinition JSON string
    definition_path = Column(String, nullable=True)  # vestigial: ephemeral on-disk copy
    # Human-readable reason set when status flips to 'error' (e.g. the extractor's
    # circuit-breaker message naming a missing ANTHROPIC_API_KEY). Surfaced by the
    # status endpoint so a reviewer sees the root cause, not a bare 'error' (T16).
    error_detail = Column(String, nullable=True)
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
    _ensure_new_columns()
    seed_admin_user()


def _ensure_new_columns():
    """Add columns that were introduced after their table was first created.

    `Base.metadata.create_all` only creates *missing tables*; it never adds a
    newly-declared column to a table that already exists. On the already-
    provisioned Render Postgres instance, `users` and `questionnaires` were
    both created on earlier deploys — so any model column added after a
    table's first deploy (`users.password_synced_from_env`;
    `questionnaires.definition_json` and `.error_detail`, added in later
    commits on the same day `questionnaires` itself first shipped) is present
    in the ORM but ABSENT from the real table until physically added. Every
    query naming a missing column raises UndefinedColumn. There is no
    Alembic/migration tooling in this codebase, so we add these defensively
    at startup — table-by-table, so the next column added to either table (or
    a new one) is a one-line addition here, not a new copy of this function.

    Approach: reflect each live table's columns via SQLAlchemy's inspector and
    emit a plain `ALTER TABLE ... ADD COLUMN` only when the column is genuinely
    missing. Plain ADD COLUMN (no `IF NOT EXISTS`) is used deliberately because
    SQLite does NOT support the `IF NOT EXISTS` clause on ADD COLUMN (Postgres
    does, but we need one statement that works on both backends) — the
    existence check makes it idempotent without relying on that clause. A
    `DEFAULT` in the DDL fragment backfills pre-existing rows in the same
    statement. Each ALTER is wrapped so a race or an unexpected backend quirk
    can't block startup — a column that fails to add here just means that
    specific feature degrades (e.g. definition_json missing -> falls back to
    the on-disk backfill path) rather than the whole app failing to boot.
    """
    from sqlalchemy import inspect, text

    # table name -> {column name -> DDL fragment to append after "ADD COLUMN <name>"}
    required_by_table = {
        "users": {
            "password_synced_from_env": "BOOLEAN DEFAULT TRUE",
        },
        "questionnaires": {
            # Durable SurveyDefinition storage (T17) and failure diagnostics
            # (T16) — both added after `questionnaires` itself first deployed.
            "definition_json": "TEXT",
            "error_detail": "VARCHAR",
        },
    }
    inspector = inspect(engine)
    for table, required in required_by_table.items():
        if not inspector.has_table(table):
            continue  # fresh DB: create_all already made the column with the model
        existing_cols = {c["name"] for c in inspector.get_columns(table)}
        with engine.begin() as conn:
            for col, ddl in required.items():
                if col in existing_cols:
                    continue
                stmt = f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"
                try:
                    conn.execute(text(stmt))
                    logger.info(f"_ensure_new_columns: added {table}.{col}")
                except Exception as exc:  # pragma: no cover - defensive, backend-specific
                    logger.warning(
                        f"_ensure_new_columns: could not add {table}.{col} ({exc})"
                    )


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
    password_synced_from_env: bool = True,
) -> UserDB:
    """Create a new user.

    `password_synced_from_env` defaults True: a normally-created user's hash was
    set once and is not an env-var sync target, so the flag is inert for
    non-admin accounts. seed_admin_user() relies on the True default when it
    creates the admin on first boot (see that function).
    """
    from uuid import uuid4

    user = UserDB(
        id=f"user_{uuid4().hex[:8]}",
        email=email,
        name=name,
        password_hash=password_hash,
        role=role,
        created_at=datetime.now().isoformat(),
        is_active=True,
        password_synced_from_env=password_synced_from_env,
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
        definition_json=None,
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
    definition_json: str = None,
    definition_path: str = None,
    error_detail: str = None,
) -> QuestionnaireDB:
    """Update a questionnaire's status and, optionally, its definition/error.

    `definition_json` is the authoritative persisted SurveyDefinition (TEXT,
    durable across restarts). `definition_path` is kept for the optional
    ephemeral on-disk copy (vestigial local-dev metadata). `error_detail` stores
    the human-readable failure reason when `status == "error"` (T16) so the
    status endpoint can name the root cause; it is cleared on any non-error
    status so a stale reason never lingers after a successful re-run.
    """
    questionnaire = get_questionnaire(db, questionnaire_id)
    if questionnaire:
        questionnaire.status = status
        if definition_json is not None:
            questionnaire.definition_json = definition_json
        if definition_path is not None:
            questionnaire.definition_path = definition_path
        # Store the reason on error; clear it otherwise so it can't go stale.
        questionnaire.error_detail = error_detail if status == "error" else None
        questionnaire.updated_at = datetime.now().isoformat()
        db.commit()
        db.refresh(questionnaire)
    return questionnaire


def set_questionnaire_definition(
    db: Session, questionnaire_id: str, definition_json: str
) -> QuestionnaireDB:
    """Persist the authoritative SurveyDefinition JSON string on the row.

    Used by both edit/approve writes and the on-read backfill from a still-
    present ephemeral file (T17 backward-compat migration).
    """
    questionnaire = get_questionnaire(db, questionnaire_id)
    if questionnaire:
        questionnaire.definition_json = definition_json
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

    ONE exception (the fix for the "it forgets my password after logout" bug):
    if the admin's password was deliberately changed through the app
    (emergency-reset sets `password_synced_from_env=False`), the sync is
    SKIPPED so a cold start after logout doesn't silently revert that change
    back to ADMIN_PASSWORD. `name` is still synced unconditionally — that was
    never the problem. A legacy row with NULL for the flag is treated as True
    (we don't know it was manually changed, so keep the original auto-sync).
    """
    from api.auth import hash_password
    from api.config import ADMIN_EMAIL, ADMIN_NAME, ADMIN_PASSWORD

    db = SessionLocal()
    try:
        existing = db.query(UserDB).filter(UserDB.email == ADMIN_EMAIL).first()
        if existing:
            # NULL (legacy row) => treat as True, i.e. keep auto-syncing.
            synced_from_env = existing.password_synced_from_env is not False
            if synced_from_env:
                existing.password_hash = hash_password(ADMIN_PASSWORD)
                logger.info(
                    f"Admin user {ADMIN_EMAIL} password synced to current ADMIN_PASSWORD"
                )
            else:
                # Password was set manually via emergency-reset — do NOT clobber
                # it. If a later ADMIN_PASSWORD change appears not to "take", this
                # log line is the answer to "why didn't my env var change apply".
                logger.info(
                    f"Admin user {ADMIN_EMAIL} password sync SKIPPED — it was set "
                    f"manually (password_synced_from_env=False); ADMIN_PASSWORD "
                    f"changes will not apply until an emergency-reset flips it back"
                )
            existing.name = ADMIN_NAME
            db.commit()
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

"""Database setup and models."""

import logging
import secrets
from datetime import UTC, datetime

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
    # Durable DataMap JSON (same rationale as questionnaires.definition_json,
    # T17): the on-disk datamap.json lives on Render's ephemeral disk and is
    # wiped by every deploy/restart, taking the navigator's study context with
    # it. The DB copy keeps variable metadata alive across restarts even when
    # the parquet itself is gone (tables can't recompute, but the chat can
    # still answer structure questions).
    datamap_json = Column(String, nullable=True)
    # Free-tier survival kit: every small artifact a restart used to destroy
    # now has a durable copy here. crosstabs/coding are KB-scale JSON; the
    # original upload is stored gzip+base64 (TEXT for cross-dialect DDL —
    # SQLite has no BYTEA) up to a size cap, so any study can be re-processed
    # from the DB alone after the disk is wiped.
    crosstabs_json = Column(String, nullable=True)
    coding_json = Column(String, nullable=True)
    source_filename = Column(String, nullable=True)
    source_file_b64 = Column(String, nullable=True)  # gzip + base64 of the upload


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


class FieldingLinkDB(Base):
    """A public link that serves ONE approved questionnaire to respondents (T9').

    Created (authenticated) from a study's approved questionnaire, then handed to
    a panel/vendor as a public URL. Respondents reach it with no JWT — the
    unguessable `token` is the only credential, mirroring ShareLinkDB.

    Survey-version pinning: the approved `SurveyDefinition` JSON is *snapshotted*
    into `survey_json` at link-creation time, and `survey_version` records the
    definition's version int. A later re-approval of the questionnaire (a new
    edit + approve cycle that bumps the definition) therefore does NOT silently
    change a survey already in the field — in-flight respondents keep answering
    the exact instrument the link was opened with, and server-authoritative
    terminate re-checks run against that same pinned copy. Snapshotting the whole
    JSON (rather than referencing the live QuestionnaireDB row) is the simplest
    correct approach: the questionnaire row is mutable through the review API,
    so a reference would not actually pin anything.

    `study_id`/`questionnaire_id` are plain string references (no FK), matching
    the rest of this codebase.
    """

    __tablename__ = "fielding_links"

    id = Column(String, primary_key=True, index=True)
    questionnaire_id = Column(String, index=True)
    study_id = Column(String, index=True)
    token = Column(String, unique=True, index=True)
    status = Column(String, default="open", index=True)  # open | closed
    survey_version = Column(Integer, default=1)
    survey_json = Column(Text)  # pinned SurveyDefinition JSON snapshot
    created_by = Column(String)  # user email
    created_at = Column(String)
    closed_at = Column(String, nullable=True)
    # Vendor redirect URL templates (T10). Nullable: a link with no template
    # keeps the built-in thank-you/screen-out screens. `{pid}`/`{rid}`/any
    # `{param}` placeholder is substituted from the session's entry_params on a
    # terminal status. Added after fielding_links first shipped (T9') -> see
    # _ensure_new_columns.
    redirect_complete = Column(String, nullable=True)
    redirect_terminate = Column(String, nullable=True)
    redirect_overquota = Column(String, nullable=True)
    redirect_security = Column(String, nullable=True)


class RespondentSessionDB(Base):
    """One respondent's attempt at a fielded survey (T9').

    A Postgres row from the first entry hit — never JSON-on-disk — so live
    responses survive Render free-tier restarts (which wipe the disk).

    `status_code` follows Lucid/Cint convention exactly (key decision #7): NULL
    while in progress, then 10 complete / 20 terminate / 30 security /
    40 overquota. 40 is not produced until T10's quota engine, but the column
    contract includes it now so vendor redirect URLs need no translation later.

    `entry_params` captures arbitrary URL query params at entry (e.g. a panel's
    `pid`/`rid`) — T10's redirect protocol reads these back. `current_page`
    supports resume.
    """

    __tablename__ = "respondent_sessions"

    id = Column(String, primary_key=True, index=True)
    fielding_link_id = Column(String, index=True)
    status_code = Column(Integer, nullable=True)  # None|10|20|30|40
    entry_params = Column(JSON, default=dict)
    current_page = Column(Integer, default=0)
    started_at = Column(String)
    last_seen_at = Column(String)
    completed_at = Column(String, nullable=True)


class RespondentAnswerDB(Base):
    """One answer to one question within a respondent session (T9').

    Chosen over a single JSONB blob on the session (both were allowed by the
    plan) because per-answer rows carry a per-question `answered_at` timestamp,
    which T14's speeder/quality flags need, and because writing one row per
    answered question is the natural incremental-capture unit for resume/partial
    data. Re-answering a question upserts the existing (session_id, qid) row in
    place rather than appending, so the row set always reflects the respondent's
    latest answers — T11 materializes these straight into a wide DataFrame.

    `value` is JSON so it uniformly holds a scalar code (single-select), a list
    (multi-select), a number, free text, or a grid dict.
    """

    __tablename__ = "respondent_answers"

    id = Column(String, primary_key=True, index=True)
    session_id = Column(String, index=True)
    qid = Column(String, index=True)
    value = Column(JSON)
    answered_at = Column(String)


class QuotaCellDB(Base):
    """One quota cell for a fielding link (T10).

    A cell is a named target: `condition_json` is a serialized `Expression`
    (same AST as routing — key decision #4, one condition language everywhere)
    evaluated against a session's accumulated answers, `cell_limit` is how many
    completes it accepts, `count` is how many it has. Enforcement is
    server-authoritative (key decision #5: quota cells cost real money if gamed
    client-side) via an atomic conditional UPDATE — see
    `increment_quota_cell_if_under_limit`.

    New table -> `Base.metadata.create_all` provisions it in full; no
    `_ensure_new_columns` entry is needed (that's only for columns added to a
    pre-existing table). `fielding_link_id` is a plain string reference (no FK),
    matching the rest of this codebase. Column is `cell_limit`, not `limit`, to
    dodge the SQL reserved word in the raw atomic UPDATE.
    """

    __tablename__ = "quota_cells"

    id = Column(String, primary_key=True, index=True)
    fielding_link_id = Column(String, index=True)
    name = Column(String)
    condition_json = Column(Text)  # serialized Expression.to_dict()
    cell_limit = Column(Integer, default=0)
    count = Column(Integer, default=0)


class CorrectionDB(Base):
    """One plain-language correction written by a founder against extraction.

    The correction is what a person *said* ("terminates live in the TERM row"),
    kept separate from any rule drafted out of it (`ProfileRuleDB`) so one
    instruction can be re-resolved several times without losing the original
    words. `question_id` is nullable on purpose: a correction anchored on Q12 is
    very often a statement about the whole document.

    New table -> `Base.metadata.create_all` provisions it; no
    `_ensure_new_columns` entry needed.
    """

    __tablename__ = "corrections"

    id = Column(String, primary_key=True, index=True)
    study_id = Column(String, index=True)
    question_id = Column(String, nullable=True)
    author_id = Column(String, index=True)
    instruction = Column(Text)
    # Where the suggestion came from: flagged | common | learned | freeform.
    # Tracked so we can see which suggestion sources actually get used.
    suggested_from = Column(String, default="freeform")
    # question | document | profile | global — confirmed by the author, not
    # silently inferred. Drives who may approve (global => OWNER only).
    scope = Column(String, default="document")
    profile_slug = Column(String, index=True, nullable=True)
    status = Column(String, default="drafted")
    # live | proposal — persisted rather than inferred from status, so the
    # owner's proposal queue is a plain filter and stays correct if the status
    # vocabulary ever grows.
    lane = Column(String, default="proposal", index=True)
    # The model's verdict: rule_text, rationale, confidence, degraded. Needed by
    # the proposal queue, which otherwise shows the raw instruction and loses
    # the structured resolution the model actually produced.
    resolution_json = Column(Text, nullable=True)
    # Reviewer's note when a correction is sent back, carried into the next
    # classification attempt so revising is a loop, not a fresh start.
    revise_note = Column(Text, nullable=True)
    attempt_no = Column(Integer, default=1)
    created_at = Column(String)


class ProfileRuleDB(Base):
    """A learned extraction rule attached to one questionnaire format profile.

    This is the table that makes corrections compound: an active rule is
    appended to the extraction prompt for EVERY document detected as
    `profile_slug`, so a rule approved once keeps applying to future client
    documents. Ordering matters (later rules win on conflict), hence `ordinal`.

    Rules are retired, never deleted — "why did we stop doing that" is a
    question that gets asked. `active=False` takes a rule out of the prompt
    while leaving it readable.
    """

    __tablename__ = "profile_rules"

    id = Column(String, primary_key=True, index=True)
    profile_slug = Column(String, index=True)
    rule_text = Column(Text)
    source_correction_id = Column(String, nullable=True)
    scope = Column(String, default="profile")
    ordinal = Column(Integer, default=0)
    active = Column(Boolean, default=True)
    approved_by = Column(String, nullable=True)
    approved_at = Column(String, nullable=True)
    created_at = Column(String)


class CodeframeLibraryDB(Base):
    """A codeframe kept for reuse on the next question like it.

    Trackers ask the same questions wave after wave. Regenerating a codeframe
    each time costs an LLM call AND produces a different set of codes, so wave 2
    cannot be compared to wave 1 without someone reconciling by hand. Keeping
    approved codeframes makes the second wave both cheaper and comparable.

    `question_text` is stored VERBATIM, not normalised: matching normalises at
    read time (see engines/coding/library.py), and keeping the original means a
    researcher browsing the library sees the question as it was actually asked.

    Only APPROVED codeframes land here — reusing something nobody signed off
    would spread one person's guess across every future wave.
    """

    __tablename__ = "codeframe_library"

    id = Column(String, primary_key=True, index=True)
    question_text = Column(Text)
    codeframe_json = Column(Text)
    # Provenance: which study this came from, so a researcher can go and look
    # at how it actually performed before reusing it.
    study_id = Column(String, index=True, nullable=True)
    client_name = Column(String, index=True, nullable=True)
    # Reuse count doubles as a quality signal — a codeframe the team keeps
    # choosing is the house standard, and breaks ties in the ranking.
    times_reused = Column(Integer, default=0)
    created_by = Column(String, nullable=True)
    created_at = Column(String)
    last_used_at = Column(String, nullable=True)


class UIOverrideDB(Base):
    """A founder-edited piece of UI text, keyed by a stable label id.

    This is Tier A of the founders' console: changing how a feature READS
    without touching code. Every instrumented string in the frontend is
    rendered through `useLabel(key, default)`, so an override is a lookup that
    falls back to the hardcoded default whenever it is missing, inactive, or
    the fetch failed. That fallback is the whole safety property — the worst a
    bad override can do is make one label read wrong, and reverting is a
    single row flip.

    v1 stores STRINGS only. Visibility, ordering and layout each need
    per-key semantics and their own validation; keeping the blast radius at
    "a word is wrong" is deliberate for the first version.
    """

    __tablename__ = "ui_overrides"

    id = Column(String, primary_key=True, index=True)
    key = Column(String, index=True)  # e.g. "questionnaire.review.title"
    value = Column(Text)
    active = Column(Boolean, default=True, index=True)
    note = Column(Text, nullable=True)  # why it was changed, in their words
    updated_by = Column(String, nullable=True)
    created_at = Column(String)
    updated_at = Column(String, nullable=True)


def active_ui_overrides(db) -> dict[str, str]:
    """All live overrides as {key: value}. Empty dict is always a safe answer."""
    rows = (
        db.query(UIOverrideDB)
        .filter(UIOverrideDB.active.is_(True))
        .order_by(UIOverrideDB.key)
        .all()
    )
    return {r.key: r.value for r in rows if r.key and r.value is not None}


class AuditLogDB(Base):
    """Append-only record of every state change in the correction loop.

    The spec claims "every transition is in the audit log" as a safety property
    — this is what makes that true. Without it there is no answer to "who
    approved the rule that broke extraction, and when", which is exactly the
    question asked after something goes wrong.

    Deliberately generic (entity + entity_id + action) rather than one table per
    thing, so recording a new kind of event is a call site, not a migration.
    Never updated or deleted: an audit trail that can be edited is not one.
    """

    __tablename__ = "audit_log"

    id = Column(String, primary_key=True, index=True)
    entity = Column(String, index=True)  # correction | profile_rule | preview
    entity_id = Column(String, index=True)
    action = Column(String, index=True)  # created | applied | toggled | approved | revised
    actor_id = Column(String, index=True)
    study_id = Column(String, index=True, nullable=True)
    summary = Column(Text, nullable=True)  # human-readable, for the UI
    before_json = Column(Text, nullable=True)
    after_json = Column(Text, nullable=True)
    at = Column(String, index=True)


def record_audit(
    db,
    *,
    entity: str,
    entity_id: str,
    action: str,
    actor_id: str,
    study_id: str | None = None,
    summary: str | None = None,
    before: dict | None = None,
    after: dict | None = None,
) -> None:
    """Append one audit entry. Never raises — auditing must not break the action.

    A failure to record is logged and swallowed: losing an audit line is bad,
    but failing a founder's approval because the audit insert hiccuped is worse,
    and would make the whole loop feel unreliable.
    """
    import json as _json
    import logging as _logging
    from datetime import datetime as _dt
    from uuid import uuid4 as _uuid

    try:
        db.add(
            AuditLogDB(
                id=f"aud_{_uuid().hex[:12]}",
                entity=entity,
                entity_id=entity_id,
                action=action,
                actor_id=actor_id,
                study_id=study_id,
                summary=summary,
                before_json=_json.dumps(before) if before is not None else None,
                after_json=_json.dumps(after) if after is not None else None,
                at=_dt.now(UTC).isoformat(),
            )
        )
    except Exception as exc:  # noqa: BLE001
        _logging.getLogger(__name__).warning(f"audit record failed: {exc}")


class CorrectionPreviewDB(Base):
    """A candidate re-extraction run for one correction, plus its diff.

    Separate table rather than columns on `corrections` because
    `Base.metadata.create_all` only creates MISSING TABLES — it never adds a
    column to a table that already exists (see `_ensure_new_columns`), and
    `corrections` is already provisioned wherever this has run once.

    Holds the candidate extraction WITHOUT applying it: a preview is a
    proposal, and the questionnaire's live definition is untouched until a
    human approves. That separation is the whole safety property of the live
    lane.
    """

    __tablename__ = "correction_previews"

    id = Column(String, primary_key=True, index=True)
    correction_id = Column(String, index=True)
    study_id = Column(String, index=True)
    rule_text = Column(Text)
    # running | ready | error
    status = Column(String, default="running", index=True)
    error_detail = Column(Text, nullable=True)
    candidate_json = Column(Text, nullable=True)  # the re-extracted definition
    diff_json = Column(Text, nullable=True)  # diffing.ExtractionDiff.to_dict()
    model_calls = Column(Integer, default=0)
    created_at = Column(String)
    completed_at = Column(String, nullable=True)


def active_rules_for_profile(db, profile_slug: str) -> list[str]:
    """Ordered active rule texts for one profile — the prompt-ready rule set.

    Returns `[]` for an unknown slug or a profile nobody has taught yet, which
    is what keeps the T32 guarantee intact: no rules == byte-identical prompt
    to today. Callers pass this into the extractor as `rules_for_profile`.
    """
    rows = (
        db.query(ProfileRuleDB)
        .filter(ProfileRuleDB.profile_slug == profile_slug)
        .filter(ProfileRuleDB.active.is_(True))
        .order_by(ProfileRuleDB.ordinal)
        .all()
    )
    return [r.rule_text for r in rows if r.rule_text]


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
        "studies": {
            # Durable DataMap storage — added after `studies` first deployed.
            "datamap_json": "TEXT",
            # Free-tier survival kit: artifacts + original upload in the DB.
            "crosstabs_json": "TEXT",
            "coding_json": "TEXT",
            "source_filename": "VARCHAR",
            "source_file_b64": "TEXT",
        },
        "corrections": {
            # Added after `corrections` first shipped (same-day follow-up):
            # the proposal queue and the revise loop both need these.
            "lane": "VARCHAR",
            "resolution_json": "TEXT",
            "revise_note": "TEXT",
            "attempt_no": "INTEGER DEFAULT 1",
        },
        "fielding_links": {
            # Vendor redirect templates (T10) — added after fielding_links
            # first shipped (T9'), so the live Postgres table needs them ALTERed
            # in even though the ORM model now declares them.
            "redirect_complete": "VARCHAR",
            "redirect_terminate": "VARCHAR",
            "redirect_overquota": "VARCHAR",
            "redirect_security": "VARCHAR",
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
    status: str = "classifying",
) -> StudyDB:
    """Create a new study in database.

    `status` defaults to the upload path's "classifying"; the survey-first path
    (create-empty-study, no data/orchestration) passes "authoring".
    """
    study = StudyDB(
        id=study_id,
        client_name=client_name,
        status=status,
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


def update_study_paths(
    db: Session,
    study_id: str,
    excel_path: str = None,
    pptx_path: str = None,
    datamap_json: str = None,
):
    """Update study output file paths (and the durable DataMap copy)."""
    study = get_study(db, study_id)
    if study:
        if excel_path:
            study.excel_path = excel_path
        if pptx_path:
            study.pptx_path = pptx_path
        if datamap_json:
            study.datamap_json = datamap_json
        study.updated_at = datetime.now().isoformat()
        db.commit()
        db.refresh(study)
    return study


def set_study_artifacts(
    db: Session,
    study_id: str,
    crosstabs_json: str = None,
    coding_json: str = None,
    source_filename: str = None,
    source_file_b64: str = None,
):
    """Persist durable copies of a study's artifacts (free-tier survival kit).

    Only fields passed non-None are written, so callers can update one artifact
    without clobbering the others."""
    study = get_study(db, study_id)
    if study:
        if crosstabs_json is not None:
            study.crosstabs_json = crosstabs_json
        if coding_json is not None:
            study.coding_json = coding_json
        if source_filename is not None:
            study.source_filename = source_filename
        if source_file_b64 is not None:
            study.source_file_b64 = source_file_b64
        study.updated_at = datetime.now().isoformat()
        db.commit()
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


# --- Fielding (T9') ---------------------------------------------------------


def create_fielding_link(
    db: Session,
    study_id: str,
    questionnaire_id: str,
    survey_version: int,
    survey_json: str,
    created_by: str,
    redirects: dict | None = None,
) -> "FieldingLinkDB":
    """Open a fielding link, pinning the approved SurveyDefinition JSON snapshot.

    `redirects` optionally seeds the vendor redirect templates (T10); only the
    keys present (redirect_complete/terminate/overquota/security) are set.
    """
    from uuid import uuid4

    redirects = redirects or {}
    link = FieldingLinkDB(
        id=f"flink_{uuid4().hex[:8]}",
        questionnaire_id=questionnaire_id,
        study_id=study_id,
        token=secrets.token_urlsafe(24),
        status="open",
        survey_version=survey_version,
        survey_json=survey_json,
        created_by=created_by,
        created_at=datetime.now().isoformat(),
        closed_at=None,
        redirect_complete=redirects.get("redirect_complete"),
        redirect_terminate=redirects.get("redirect_terminate"),
        redirect_overquota=redirects.get("redirect_overquota"),
        redirect_security=redirects.get("redirect_security"),
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    return link


def get_fielding_link(db: Session, link_id: str) -> "FieldingLinkDB":
    """Get a fielding link by id."""
    return db.query(FieldingLinkDB).filter(FieldingLinkDB.id == link_id).first()


def get_fielding_link_by_token(db: Session, token: str) -> "FieldingLinkDB":
    """Get a fielding link by its public token."""
    return db.query(FieldingLinkDB).filter(FieldingLinkDB.token == token).first()


def list_fielding_links_for_study(db: Session, study_id: str) -> list["FieldingLinkDB"]:
    """List a study's fielding links, most recent first."""
    from sqlalchemy import desc

    return (
        db.query(FieldingLinkDB)
        .filter(FieldingLinkDB.study_id == study_id)
        .order_by(desc(FieldingLinkDB.created_at))
        .all()
    )


def close_fielding_link(db: Session, link_id: str) -> "FieldingLinkDB":
    """Close a link so it stops accepting NEW sessions (idempotent)."""
    link = get_fielding_link(db, link_id)
    if link and link.status != "closed":
        link.status = "closed"
        link.closed_at = datetime.now().isoformat()
        db.commit()
        db.refresh(link)
    return link


def create_respondent_session(
    db: Session, fielding_link_id: str, entry_params: dict | None = None
) -> "RespondentSessionDB":
    """Start a respondent session (status_code stays NULL until terminal)."""
    from uuid import uuid4

    now = datetime.now().isoformat()
    session = RespondentSessionDB(
        id=f"rsess_{uuid4().hex[:8]}",
        fielding_link_id=fielding_link_id,
        status_code=None,
        entry_params=entry_params or {},
        current_page=0,
        started_at=now,
        last_seen_at=now,
        completed_at=None,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def get_respondent_session(db: Session, session_id: str) -> "RespondentSessionDB":
    """Get a respondent session by id."""
    return (
        db.query(RespondentSessionDB)
        .filter(RespondentSessionDB.id == session_id)
        .first()
    )


def touch_respondent_session(
    db: Session, session_id: str, current_page: int | None = None
) -> "RespondentSessionDB":
    """Bump last_seen_at (and optionally current_page) on progress."""
    session = get_respondent_session(db, session_id)
    if session:
        session.last_seen_at = datetime.now().isoformat()
        if current_page is not None:
            session.current_page = current_page
        db.commit()
        db.refresh(session)
    return session


def set_respondent_session_status(
    db: Session, session_id: str, status_code: int
) -> "RespondentSessionDB":
    """Set a terminal status code (10/20/30/40) and stamp completed_at.

    Terminal states are write-once: a session that already carries a status_code
    is returned unchanged, so a terminated respondent resubmitting can neither
    un-terminate nor overwrite an existing complete/terminate.
    """
    session = get_respondent_session(db, session_id)
    if session and session.status_code is None:
        session.status_code = status_code
        session.completed_at = datetime.now().isoformat()
        session.last_seen_at = session.completed_at
        db.commit()
        db.refresh(session)
    return session


def upsert_respondent_answer(
    db: Session, session_id: str, qid: str, value
) -> "RespondentAnswerDB":
    """Insert or update one (session, qid) answer, refreshing answered_at."""
    from uuid import uuid4

    answer = (
        db.query(RespondentAnswerDB)
        .filter(
            RespondentAnswerDB.session_id == session_id,
            RespondentAnswerDB.qid == qid,
        )
        .first()
    )
    if answer:
        answer.value = value
        answer.answered_at = datetime.now().isoformat()
    else:
        answer = RespondentAnswerDB(
            id=f"rans_{uuid4().hex[:8]}",
            session_id=session_id,
            qid=qid,
            value=value,
            answered_at=datetime.now().isoformat(),
        )
        db.add(answer)
    db.commit()
    db.refresh(answer)
    return answer


def list_respondent_answers(db: Session, session_id: str) -> list["RespondentAnswerDB"]:
    """All answer rows for a session (order stable by qid)."""
    return (
        db.query(RespondentAnswerDB)
        .filter(RespondentAnswerDB.session_id == session_id)
        .order_by(RespondentAnswerDB.qid)
        .all()
    )


def get_session_answers_map(db: Session, session_id: str) -> dict:
    """Accumulated answers as a {qid: value} dict (for evaluate() + resume)."""
    return {a.qid: a.value for a in list_respondent_answers(db, session_id)}


def fielding_link_counts(db: Session, link_id: str) -> dict:
    """Per-link session tallies by Lucid/Cint status code: starts, completes (10),
    terminates (20), security (30), overquota (40) — the field-monitor buckets."""
    sessions = (
        db.query(RespondentSessionDB)
        .filter(RespondentSessionDB.fielding_link_id == link_id)
        .all()
    )
    return {
        "starts": len(sessions),
        "completes": sum(1 for s in sessions if s.status_code == 10),
        "terminates": sum(1 for s in sessions if s.status_code == 20),
        "security": sum(1 for s in sessions if s.status_code == 30),
        "overquota": sum(1 for s in sessions if s.status_code == 40),
    }


def list_respondent_sessions(db: Session, fielding_link_id: str) -> list["RespondentSessionDB"]:
    """All sessions for a link (used by the duplicate-pid security check)."""
    return (
        db.query(RespondentSessionDB)
        .filter(RespondentSessionDB.fielding_link_id == fielding_link_id)
        .all()
    )


def set_fielding_link_redirects(db: Session, link_id: str, redirects: dict) -> "FieldingLinkDB":
    """Set/overwrite vendor redirect templates on a link (T10 PATCH).

    Only keys present in `redirects` are written, so a PATCH can update one
    template without clobbering the others. A key mapped to None clears it.
    """
    link = get_fielding_link(db, link_id)
    if link:
        for col in (
            "redirect_complete",
            "redirect_terminate",
            "redirect_overquota",
            "redirect_security",
        ):
            if col in redirects:
                setattr(link, col, redirects[col])
        db.commit()
        db.refresh(link)
    return link


# --- Quota cells (T10) ------------------------------------------------------


def create_quota_cell(
    db: Session, fielding_link_id: str, name: str, condition_json: str, limit: int
) -> "QuotaCellDB":
    """Create one quota cell (count starts at 0)."""
    from uuid import uuid4

    cell = QuotaCellDB(
        id=f"quota_{uuid4().hex[:8]}",
        fielding_link_id=fielding_link_id,
        name=name,
        condition_json=condition_json,
        cell_limit=limit,
        count=0,
    )
    db.add(cell)
    db.commit()
    db.refresh(cell)
    return cell


def list_quota_cells(db: Session, fielding_link_id: str) -> list["QuotaCellDB"]:
    """All quota cells for a link (live counts)."""
    return (
        db.query(QuotaCellDB)
        .filter(QuotaCellDB.fielding_link_id == fielding_link_id)
        .order_by(QuotaCellDB.id)
        .all()
    )


def get_quota_cell(db: Session, quota_id: str) -> "QuotaCellDB":
    """Get one quota cell by id."""
    return db.query(QuotaCellDB).filter(QuotaCellDB.id == quota_id).first()


def update_quota_cell_limit(db: Session, quota_id: str, limit: int) -> "QuotaCellDB":
    """Change a cell's limit (raising mid-field is routine MR practice)."""
    cell = get_quota_cell(db, quota_id)
    if cell:
        cell.cell_limit = limit
        db.commit()
        db.refresh(cell)
    return cell


def set_quota_cell_condition(db: Session, quota_id: str, condition_json: str) -> "QuotaCellDB":
    """Rewrite a cell's condition (only allowed by the route before any session
    exists — changing it mid-field would invalidate already-counted completes)."""
    cell = get_quota_cell(db, quota_id)
    if cell:
        cell.condition_json = condition_json
        db.commit()
        db.refresh(cell)
    return cell


def increment_quota_cell_if_under_limit(db: Session, quota_id: str) -> bool:
    """Atomically claim a slot in a cell; return True iff a slot was taken.

    Single conditional UPDATE — `count = count + 1 WHERE count < cell_limit` —
    so two respondents completing simultaneously can never both take the last
    slot: the DB serializes the two UPDATEs and the second sees count already at
    the limit and matches zero rows. This is atomic on BOTH Postgres and SQLite
    with NO explicit locking (`SELECT ... FOR UPDATE` is a silent no-op on the
    SQLite the tests use), which is exactly why the pattern is a conditional
    UPDATE + rowcount check rather than read-modify-write.
    """
    from sqlalchemy import text

    result = db.execute(
        text(
            "UPDATE quota_cells SET count = count + 1 "
            "WHERE id = :id AND count < cell_limit"
        ),
        {"id": quota_id},
    )
    db.commit()
    return result.rowcount > 0


def decrement_quota_cell(db: Session, quota_id: str) -> None:
    """Release a slot (roll back a speculative increment when a sibling cell was
    full — see the multi-cell policy in the complete handler)."""
    from sqlalchemy import text

    db.execute(
        text("UPDATE quota_cells SET count = count - 1 WHERE id = :id"),
        {"id": quota_id},
    )
    db.commit()


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


def touch_questionnaire(db: Session, questionnaire_id: str) -> None:
    """Mark a questionnaire as still being worked on.

    The staleness guard treats a row that has not moved for a while as dead.
    Extraction is legitimately slow on a rate-limited tier — minutes of waiting
    between calls — so without a heartbeat a healthy long run is indistinguishable
    from a killed worker, and gets reported as an error while it is still going.
    """
    questionnaire = db.query(QuestionnaireDB).filter(
        QuestionnaireDB.id == questionnaire_id
    ).first()
    if questionnaire:
        questionnaire.updated_at = datetime.utcnow().isoformat()
        db.commit()

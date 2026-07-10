"""Fielding runtime — serve an approved survey to real respondents (T9').

Two audiences, two auth models:

* **Management** (`/api/studies/{study_id}/fielding/...`) is authenticated
  (owner/analyst create+close; reviewer read-only) and mirrors the study/share
  route conventions.
* **Respondent** (`/api/r/{token}/...`) is fully public — respondents have no
  account and no JWT, exactly like `public.py`'s share-link endpoints. The
  unguessable `token` is the only credential.

Storage is Postgres rows from the first entry hit (T9′ re-scope): sessions and
answers are never written to disk, so live responses survive Render free-tier
restarts. Answers are captured incrementally (one upserted row per question),
which is what enables mid-interview resume + partial capture.

Server-authoritative terminates: the SurveyJS payload we serve deliberately
carries NO `terminate_if` logic (see surveyjs_compiler.py's docstring — it drops
it by design). THIS module is the enforcement layer it defers to: after every
answer page, we re-evaluate every question's `terminate_if` with the pure
`evaluate()` from survey_definition against ALL accumulated answers, and screen
the respondent out server-side if any fires. A client cannot bypass a terminate
by tampering with its local survey model.

Status codes are the Lucid/Cint convention (key decision #7): 10 complete,
20 terminate, 30 security, 40 overquota. T9' produces 10 and 20; 30/40 arrive
with later tasks (quota engine / vendor redirect protocol are explicitly T10).
"""

import logging
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from api.auth import UserRole, get_current_user, require_role
from api.db import (
    UserDB,
    close_fielding_link,
    create_fielding_link,
    create_respondent_session,
    fielding_link_counts,
    get_db,
    get_fielding_link,
    get_fielding_link_by_token,
    get_respondent_session,
    get_session_answers_map,
    get_study,
    list_fielding_links_for_study,
    list_questionnaires_for_study,
    set_respondent_session_status,
    touch_respondent_session,
    upsert_respondent_answer,
)
from api.models import (
    FieldingLinkListResponse,
    FieldingLinkResponse,
    RespondentAnswersRequest,
    RespondentEntryResponse,
    RespondentSessionResponse,
)
from api.routes.questionnaire import _read_definition_json
from src.core.survey_definition import SurveyDefinition, evaluate
from src.renderers.surveyjs_compiler import compile_to_surveyjs

logger = logging.getLogger(__name__)
router = APIRouter()

# Lucid/Cint status codes (key decision #7).
STATUS_COMPLETE = 10
STATUS_TERMINATE = 20
STATUS_SECURITY = 30  # noqa: F841 - reserved; produced by later tasks
STATUS_OVERQUOTA = 40  # noqa: F841 - reserved for T10's quota engine


def _respondent_path(token: str) -> str:
    """Public URL path a respondent visits (front-end route mirrors this)."""
    return f"/r/{token}"


def _link_response(link, counts: dict | None = None) -> FieldingLinkResponse:
    return FieldingLinkResponse(
        id=link.id,
        study_id=link.study_id,
        questionnaire_id=link.questionnaire_id,
        token=link.token,
        status=link.status,
        survey_version=link.survey_version,
        created_by=link.created_by,
        created_at=link.created_at,
        closed_at=link.closed_at,
        respondent_path=_respondent_path(link.token),
        counts=counts or {},
    )


# --- Management endpoints (authenticated) -----------------------------------


def _approved_questionnaire(db: Session, study_id: str):
    """Resolve the study's most-recent APPROVED questionnaire, or raise.

    404 if the study doesn't exist; 409 if it has a questionnaire but none is
    approved yet (fielding an unreviewed instrument is the thing we're guarding
    against). This is what makes "create a link" fail cleanly on a draft.
    """
    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")
    questionnaires = list_questionnaires_for_study(db, study_id)
    approved = [q for q in questionnaires if q.status == "approved"]
    if not approved:
        raise HTTPException(
            status_code=409,
            detail="Study has no approved questionnaire to field",
        )
    return approved[0]  # list is most-recent-first


@router.post(
    "/studies/{study_id}/fielding/links",
    response_model=FieldingLinkResponse,
)
async def create_link(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Open a fielding link for the study's approved questionnaire. (Owner/Analyst)

    The approved `SurveyDefinition` JSON is snapshotted onto the link so a later
    re-approval can't change a survey already in the field."""
    questionnaire = _approved_questionnaire(db, study_id)
    payload = _read_definition_json(db, questionnaire)
    if payload is None:
        # Approved but no persisted definition is a server fault, not user error.
        raise HTTPException(status_code=500, detail="Approved questionnaire has no definition")
    survey = SurveyDefinition.from_json(payload)

    link = create_fielding_link(
        db,
        study_id=study_id,
        questionnaire_id=questionnaire.id,
        survey_version=survey.version,
        survey_json=payload,
        created_by=user.email,
    )
    logger.info(
        f"[{study_id}] Fielding link {link.id} opened by {user.name} "
        f"(questionnaire {questionnaire.id} v{survey.version})"
    )
    return _link_response(link, fielding_link_counts(db, link.id))


@router.get(
    "/studies/{study_id}/fielding/links",
    response_model=FieldingLinkListResponse,
)
async def list_links(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """List a study's fielding links with per-link session counts. (Authenticated)"""
    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")
    links = list_fielding_links_for_study(db, study_id)
    return FieldingLinkListResponse(
        study_id=study_id,
        links=[_link_response(ln, fielding_link_counts(db, ln.id)) for ln in links],
    )


@router.post(
    "/studies/{study_id}/fielding/links/{link_id}/close",
    response_model=FieldingLinkResponse,
)
async def close_link(
    study_id: str,
    link_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Close a link: stop accepting NEW sessions. (Owner/Analyst)

    In-flight sessions (already created before the close) may finish — they can
    still POST answers and complete. This is deliberate: cutting off a respondent
    mid-interview would strand a partial that panel vendors still bill for and
    that T11 can still materialize. "Closed" gates *entry*, not continuation.
    """
    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")
    link = get_fielding_link(db, link_id)
    if not link or link.study_id != study_id:
        raise HTTPException(status_code=404, detail="Fielding link not found")
    link = close_fielding_link(db, link_id)
    logger.info(f"[{study_id}] Fielding link {link_id} closed by {user.name}")
    return _link_response(link, fielding_link_counts(db, link.id))


# --- Respondent endpoints (PUBLIC, token-based, no JWT) ---------------------


def _resolve_link(db: Session, token: str):
    """Return the link for a token, or 404. No info leak on an unknown token."""
    link = get_fielding_link_by_token(db, token)
    if not link:
        raise HTTPException(status_code=404, detail="Survey not found")
    return link


def _resolve_session(db: Session, link, session_id: str):
    """Return a session that belongs to `link`, or 404 (no info leak).

    A session id that is unknown OR belongs to a different link both 404 the
    same way — a respondent can't probe other links' sessions.
    """
    session = get_respondent_session(db, session_id)
    if not session or session.fielding_link_id != link.id:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.get("/r/{token}", response_model=RespondentEntryResponse)
async def enter_survey(token: str, request: Request, db: Session = Depends(get_db)):
    """Respondent entry: create a session and return the compiled SurveyJS JSON.

    A closed link returns a clean `status="closed"` payload (no session, empty
    survey) — not a 500 — so the front-end can show a "survey closed" screen.
    All URL query params are captured into the session's `entry_params` (e.g. a
    panel's `pid`/`rid`), which T10's redirect protocol reads back.
    """
    link = _resolve_link(db, token)
    if link.status != "open":
        return RespondentEntryResponse(session_id="", status="closed", surveyjs_json={})

    entry_params = dict(request.query_params)
    session = create_respondent_session(db, link.id, entry_params=entry_params)

    survey = SurveyDefinition.from_json(link.survey_json)
    surveyjs_json = compile_to_surveyjs(survey)
    return RespondentEntryResponse(
        session_id=session.id,
        status="open",
        surveyjs_json=surveyjs_json,
        current_page=session.current_page or 0,
    )


def _terminal_payload(session) -> dict:
    """Uniform response for a session that already reached a terminal status.

    Returned instead of writing when a terminated/completed respondent
    resubmits, so a terminate can never be un-done and no further answers land.
    """
    return {
        "already_final": True,
        "status_code": session.status_code,
        "terminated": session.status_code == STATUS_TERMINATE,
    }


@router.post("/r/{token}/sessions/{session_id}/answers")
async def submit_answers(
    token: str,
    session_id: str,
    body: RespondentAnswersRequest,
    db: Session = Depends(get_db),
):
    """Persist a page of answers, then enforce terminates server-side.

    Flow: reject if the session is already terminal → upsert each answer
    incrementally → re-evaluate every question's `terminate_if` against ALL
    accumulated answers (server-authoritative) → screen out with status 20 if
    any fires, else 200 `{ok: true}`.
    """
    link = _resolve_link(db, token)
    session = _resolve_session(db, link, session_id)

    if session.status_code is not None:
        return _terminal_payload(session)

    for qid, value in (body.answers or {}).items():
        upsert_respondent_answer(db, session_id, qid, value)
    touch_respondent_session(db, session_id, current_page=body.current_page)

    # Server-authoritative terminate check against the PINNED survey copy.
    survey = SurveyDefinition.from_json(link.survey_json)
    answers = get_session_answers_map(db, session_id)
    for question in survey.questions:
        if question.terminate_if is not None and evaluate(question.terminate_if, answers):
            set_respondent_session_status(db, session_id, STATUS_TERMINATE)
            logger.info(
                f"[{link.study_id}] Session {session_id} terminated by "
                f"{question.qid}.terminate_if"
            )
            return {"terminated": True, "status_code": STATUS_TERMINATE}

    return {"ok": True, "terminated": False}


@router.post("/r/{token}/sessions/{session_id}/complete")
async def complete_survey(
    token: str,
    session_id: str,
    db: Session = Depends(get_db),
):
    """Mark the session complete (status 10). Idempotent + terminal-safe.

    `set_respondent_session_status` is write-once, so calling this twice returns
    10 both times, and a session already terminated (20) is NOT flipped to
    complete — the first terminal status wins.
    """
    link = _resolve_link(db, token)
    session = _resolve_session(db, link, session_id)
    if session.status_code is None:
        set_respondent_session_status(db, session_id, STATUS_COMPLETE)
        session = get_respondent_session(db, session_id)
    return {
        "status_code": session.status_code,
        "complete": session.status_code == STATUS_COMPLETE,
    }


@router.get(
    "/r/{token}/sessions/{session_id}",
    response_model=RespondentSessionResponse,
)
async def resume_session(
    token: str,
    session_id: str,
    db: Session = Depends(get_db),
):
    """Resume support: accumulated answers + current_page + any terminal status.

    Lets a respondent who dropped off continue where they left off (the survey
    JSON is re-fetched via GET /r/{token}; this restores their answers/progress).
    """
    link = _resolve_link(db, token)
    session = _resolve_session(db, link, session_id)
    return RespondentSessionResponse(
        session_id=session.id,
        status_code=session.status_code,
        current_page=session.current_page or 0,
        answers=get_session_answers_map(db, session_id),
        completed_at=session.completed_at,
    )

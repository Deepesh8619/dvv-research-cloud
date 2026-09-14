"""Codeframe reuse: offer a codeframe you already built for a question like this.

Saves an LLM call, but the reason this matters commercially is consistency —
wave 2 coded with wave 1's codeframe can actually be compared to wave 1. When
every wave regenerates its own codes, someone reconciles them by hand before
the tracker means anything.

Nothing here auto-applies. A match is offered with a score and a plain-English
reason, and a person chooses it. Silently reusing the wrong codeframe would
miscode real respondent data in a way nobody notices until the tables look
strange — a missed match only costs one LLM call, so the asymmetry says
suggest, never assume.
"""

import json
import logging
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.auth import UserRole, get_current_user, require_role
from api.db import CodeframeLibraryDB, UserDB, get_db, record_audit
from api.models import SaveCodeframeRequest
from src.engines.coding.library import find_matches

logger = logging.getLogger(__name__)
router = APIRouter()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _library_rows(db: Session, client_name: str | None = None) -> list[dict]:
    """The library as plain dicts, so the matcher stays free of the ORM."""
    q = db.query(CodeframeLibraryDB)
    if client_name:
        q = q.filter(CodeframeLibraryDB.client_name == client_name)
    return [
        {
            "id": r.id,
            "question_text": r.question_text,
            "study_id": r.study_id,
            "times_reused": r.times_reused or 0,
        }
        for r in q.all()
    ]


@router.get("/codeframes/suggest")
def suggest_codeframes(
    question: str,
    client_name: str | None = None,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """Codeframes already built for a question like this one, best first.

    `client_name` narrows to one client's history, which is usually what a
    researcher wants: the same wording can mean different things to different
    clients, and their codeframes reflect their own reporting conventions.

    An empty list is a completely normal answer and means "generate a new one".
    """
    matches = find_matches(question, _library_rows(db, client_name))
    return {
        "question": question,
        "matches": [m.to_dict() for m in matches],
        "count": len(matches),
    }


@router.get("/codeframes/{entry_id}")
def get_codeframe(
    entry_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """The full codeframe behind a suggestion, so it can be reviewed before use."""
    row = db.query(CodeframeLibraryDB).filter(CodeframeLibraryDB.id == entry_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Codeframe not found")
    try:
        codeframe = json.loads(row.codeframe_json or "{}")
    except json.JSONDecodeError:
        codeframe = {}
    return {
        "id": row.id,
        "question_text": row.question_text,
        "codeframe": codeframe,
        "study_id": row.study_id,
        "client_name": row.client_name,
        "times_reused": row.times_reused or 0,
        "created_at": row.created_at,
    }


@router.post("/codeframes")
def save_codeframe(
    request: SaveCodeframeRequest,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Add an APPROVED codeframe to the library for future reuse.

    Deliberately an explicit action rather than something that happens on every
    generation: an unreviewed codeframe saved automatically would spread one
    person's first guess across every future wave.
    """
    question = (request.question_text or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="A codeframe needs its question.")
    if not request.codeframe:
        raise HTTPException(status_code=400, detail="No codeframe to save.")

    row = CodeframeLibraryDB(
        id=f"cf_{uuid4().hex[:12]}",
        question_text=question,
        codeframe_json=json.dumps(request.codeframe),
        study_id=request.study_id,
        client_name=request.client_name,
        times_reused=0,
        created_by=user.id,
        created_at=_now(),
    )
    db.add(row)
    record_audit(
        db, entity="codeframe", entity_id=row.id, action="saved", actor_id=user.id,
        study_id=request.study_id,
        summary=f"codeframe saved for reuse: {question[:120]}",
    )
    db.commit()
    logger.info(f"Codeframe {row.id} saved to library by {user.email}")
    return {"id": row.id, "question_text": question, "saved": True}


@router.post("/codeframes/{entry_id}/reuse")
def mark_reused(
    entry_id: str,
    study_id: str | None = None,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Record that this codeframe was actually chosen.

    The count is not vanity: it breaks ties in the ranking, so a codeframe the
    team keeps choosing surfaces first as the house standard.
    """
    row = db.query(CodeframeLibraryDB).filter(CodeframeLibraryDB.id == entry_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Codeframe not found")
    row.times_reused = (row.times_reused or 0) + 1
    row.last_used_at = _now()
    record_audit(
        db, entity="codeframe", entity_id=entry_id, action="reused", actor_id=user.id,
        study_id=study_id,
        summary=f"reused (now {row.times_reused}x): {(row.question_text or '')[:100]}",
    )
    db.commit()
    return {"id": entry_id, "times_reused": row.times_reused}


@router.get("/codeframes")
def list_codeframes(
    client_name: str | None = None,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """Browse the library — most-reused first."""
    q = db.query(CodeframeLibraryDB)
    if client_name:
        q = q.filter(CodeframeLibraryDB.client_name == client_name)
    rows = q.order_by(CodeframeLibraryDB.times_reused.desc()).limit(200).all()
    return {
        "codeframes": [
            {
                "id": r.id,
                "question_text": r.question_text,
                "client_name": r.client_name,
                "study_id": r.study_id,
                "times_reused": r.times_reused or 0,
                "created_at": r.created_at,
            }
            for r in rows
        ],
        "count": len(rows),
    }

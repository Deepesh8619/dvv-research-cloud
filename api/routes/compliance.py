"""Compliance exports. The consent ledger: who agreed to what text, and when."""

import csv
import hashlib
import io
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.auth import UserRole, require_role
from api.db import (
    RespondentAnswerDB,
    RespondentSessionDB,
    UserDB,
    erase_respondent_session,
    get_db,
    get_study,
    list_fielding_links_for_study,
    list_respondent_sessions,
    record_audit,
)
from api.routes.materialize import _display, data_access
from src.core.survey_definition import SurveyDefinition

router = APIRouter()

_HEADER = [
    "session_id", "link_id", "consent_question_id", "answer_label",
    "answered_at", "survey_version", "consent_text_sha256",
]


@router.get("/studies/{study_id}/compliance/consent.csv")
def consent_ledger(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """One row per (session, consent question), read from each link's PINNED survey.

    A consent question has `validation.consent: true`. Sessions that never
    answered still get a row (empty answer) so gaps are visible. Test links are
    skipped: their data is never real. (Owner/Analyst, gated like a data download.)
    """
    if not get_study(db, study_id):
        raise HTTPException(status_code=404, detail="Study not found")
    decision = data_access(db, study_id, user)
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=decision.reason)

    rows, qids = [], set()
    for link in list_fielding_links_for_study(db, study_id):
        if link.is_test:
            continue
        survey = SurveyDefinition.from_json(link.survey_json)
        consent_qs = [q for q in survey.questions if (q.validation or {}).get("consent") is True]
        qids.update(q.qid for q in consent_qs)
        for q in consent_qs:
            sha = hashlib.sha256(q.text.encode("utf-8")).hexdigest()
            for s in list_respondent_sessions(db, link.id):
                a = (
                    db.query(RespondentAnswerDB)
                    .filter(RespondentAnswerDB.session_id == s.id, RespondentAnswerDB.qid == q.qid)
                    .first()
                )  # ponytail: one query per session, batch if ledgers get big
                rows.append([
                    s.id, link.id, q.qid, _display(q, a.value) if a else "",
                    a.answered_at if a else "", link.survey_version, sha,
                ])

    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(_HEADER)
    w.writerows(rows)
    return Response(
        out.getvalue(), media_type="text/csv",
        headers={"X-Consent-Questions": str(len(qids))},
    )


@router.delete("/studies/{study_id}/respondents/{session_ref}")
def erase_respondent(
    study_id: str,
    session_ref: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER)),
):
    """Erase one fielded respondent (session id or pid) from THIS study. (Owner)

    Answers are deleted and pid/entry params blanked; the session row stays so
    field counts and incidence are unchanged.
    """
    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")
    link_ids = [lk.id for lk in list_fielding_links_for_study(db, study_id)]
    session = None
    if link_ids:
        base = db.query(RespondentSessionDB).filter(
            RespondentSessionDB.fielding_link_id.in_(link_ids)
        )
        session = base.filter(RespondentSessionDB.id == session_ref).first() or (
            base.filter(RespondentSessionDB.pid == session_ref).first()
        )
    if not session:
        raise HTTPException(status_code=404, detail="Respondent not found in this study")

    removed = erase_respondent_session(db, session)
    record_audit(
        db, entity="compliance.erasure", entity_id=session.id, action="erase",
        actor_id=user.id, study_id=study_id,
        summary=f"Erased respondent session {session.id} ({removed} answers removed)",
    )
    meta = {**(study.study_metadata or {}), "needs_rematerialize": True}
    study.study_metadata = meta
    db.commit()
    locked = bool((meta.get("cleaning") or {}).get("locked"))
    return {
        "session_id": session.id,
        "answers_removed": removed,
        "needs_rematerialize": True,
        "cleaning_locked": locked,
        "cleaning_note": (
            "Cleaning is locked: the cleaned version still contains this person until "
            "cleaning is unlocked, re-materialized and re-locked." if locked else None
        ),
        "limits": [
            "Uploaded-file studies are NOT covered: the archived source file still holds the row.",
            "The delivered data file still contains this person until re-materialized.",
            "Earlier exports and backups still contain the data until regenerated or expired.",
        ],
    }


class RetentionBody(BaseModel):
    retain_until: date | None = None


@router.patch("/studies/{study_id}/retention")
def set_retention(
    study_id: str,
    body: RetentionBody,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER)),
):
    """Set or clear the date after which this study's data may be purged. (Owner)

    Purging itself is a CLI step (scripts/purge_expired.py); there is no endpoint.
    """
    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")
    if body.retain_until and body.retain_until < date.today():
        raise HTTPException(status_code=422, detail="retain_until must not be in the past")
    value = body.retain_until.isoformat() if body.retain_until else None
    old = (study.study_metadata or {}).get("retain_until")
    study.study_metadata = {**(study.study_metadata or {}), "retain_until": value}
    record_audit(
        db, entity="compliance.retention", entity_id=study_id, action="set",
        actor_id=user.id, study_id=study_id, summary=f"retain_until {old} -> {value}",
    )
    db.commit()
    return {"retain_until": value}

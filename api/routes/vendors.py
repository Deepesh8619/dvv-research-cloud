"""Vendor scorecard: one row per real (non-test) fielding link."""

import csv
import io
import json
import os
from types import SimpleNamespace

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from sqlalchemy.orm import Session

from api.auth import UserRole, require_role
from api.db import (
    AuditLogDB,
    UserDB,
    answers_by_session,
    get_db,
    get_fielding_link,
    get_study,
    list_fielding_links_for_study,
    list_quota_cells,
    list_respondent_sessions,
)
from src.core.survey_definition import Expression, SurveyDefinition
from src.engines.fielding.decisions import STATUS_COMPLETE, QuotaCell
from src.engines.fielding.vendors import (
    CONTACT_COLUMNS,
    MAX_INVITE_ROWS,
    flag_rates,
    pct_removed,
    quota_balance,
    row_pid,
    scorecard_row,
    unfinished,
)

router = APIRouter()


def _report_and_quality(survey, sessions, answers, cells):
    """Heavy part, top-level so it can run in the worker pool. Nothing is written."""
    import polars as pl

    from src.engines.fielding.field_report import build_field_report
    from src.engines.fielding.materialize import materialize_fielding
    from src.engines.qc.cleaning import collect_flags

    report = build_field_report(survey, sessions, answers, cells).to_dict()
    completes = [s for s in sessions if s.status_code == STATUS_COMPLETE]
    if not completes:
        return report, flag_rates(0, {}), []
    datamap, frame = materialize_fielding(survey, completes, answers)
    flags, _ = collect_flags(pl.from_pandas(frame, nan_to_null=True), datamap)
    return report, flag_rates(len(completes), flags), [s.id for s in completes]


@router.get("/studies/{study_id}/fielding/vendors")
def vendor_scorecard(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.FOUNDER, UserRole.ANALYST)),
):
    """Per-vendor starts, incidence, cost against target and quality. (Owner/Founder/Analyst)

    # ponytail: O(links x sessions) - a full field report plus QC per link on every
    # call. Must not be polled; cache or precompute if a dashboard ever needs it.
    """
    from api.heavy import plain_sessions, run_heavy_detached
    from src.engines.qc.cleaning import current_decisions

    if not get_study(db, study_id):
        raise HTTPException(status_code=404, detail="Study not found")
    jobs = []
    for link in list_fielding_links_for_study(db, study_id):
        if link.is_test:
            continue
        cells = [
            QuotaCell(id=c.id, condition=Expression.from_dict(json.loads(c.condition_json)),
                      limit=c.cell_limit, count=c.count)
            for c in list_quota_cells(db, link.id)
        ]
        jobs.append((
            SimpleNamespace(id=link.id, vendor_name=link.vendor_name, cpi=link.cpi,
                            target_completes=link.target_completes),
            SurveyDefinition.from_json(link.survey_json),
            plain_sessions(list_respondent_sessions(db, link.id)),
            answers_by_session(db, link.id), cells,
        ))
    audit = (
        db.query(AuditLogDB)
        .filter(AuditLogDB.entity == "cleaning.respondent", AuditLogDB.study_id == study_id)
        .order_by(AuditLogDB.at.asc()).all()
    )
    decisions = current_decisions([
        {"entity_id": r.entity_id, "action": r.action,
         "after": json.loads(r.after_json) if r.after_json else None}
        for r in audit
    ])

    rows = []
    for job in jobs:
        # The first call closes the session; everything is already loaded.
        report, quality, ids = run_heavy_detached(db, _report_and_quality, *job[1:])
        rows.append(scorecard_row(job[0], report, quality, pct_removed(ids, decisions)))
    return {"study_id": study_id, "vendors": rows, "run_by": user.name}


@router.get("/studies/{study_id}/fielding/quota-balance")
def quota_balance_route(
    study_id: str,
    target: int = Query(..., ge=0, description="Combined target per cell across vendors"),
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.FOUNDER, UserRole.ANALYST)),
):
    """Cross-vendor quota balance: suggested limits only, nothing is applied.
    (Owner/Founder/Analyst)"""
    if not get_study(db, study_id):
        raise HTTPException(status_code=404, detail="Study not found")
    links = {k.id: k.vendor_name for k in list_fielding_links_for_study(db, study_id)
             if not k.is_test}
    cells = [c for lid in links for c in list_quota_cells(db, lid)]
    return {"study_id": study_id, **quota_balance(links, cells, target), "run_by": user.name}


MAX_INVITE_BYTES = 5 * 1024 * 1024


async def _invite_rows(db, study_id, link_id, file):
    """Parse the upload into (link, header, rows-with-pid). Nothing is stored or logged."""
    from api.config import JWT_SECRET, PUBLIC_BASE_URL

    link = get_fielding_link(db, link_id)
    if not link or link.study_id != study_id:
        raise HTTPException(status_code=404, detail="Link not found")
    raw = await file.read(MAX_INVITE_BYTES + 1)
    if len(raw) > MAX_INVITE_BYTES:
        raise HTTPException(status_code=413, detail="CSV larger than 5 MB")
    try:
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
        header = [h.strip() for h in (reader.fieldnames or [])]
        if not {h.lower() for h in header} & set(CONTACT_COLUMNS[:2]):
            raise HTTPException(status_code=422, detail="CSV needs an email or phone column")
        reader.fieldnames = header
        secret = os.getenv("DVV_IP_SALT") or JWT_SECRET
        base = f"{PUBLIC_BASE_URL.rstrip('/')}/r/{link.token}"
        rows = []
        for raw_row in reader:
            if len(rows) >= MAX_INVITE_ROWS:
                raise HTTPException(status_code=422, detail="More than 100,000 rows")
            row = {k: (v or "") for k, v in raw_row.items() if k is not None}
            pid = row_pid(secret, {k.lower(): v for k, v in row.items()})
            rows.append({**row, "pid": pid, "url": f"{base}?pid={pid}" if pid else ""})
    except UnicodeDecodeError:
        raise HTTPException(status_code=422, detail="CSV must be UTF-8") from None
    except csv.Error:
        raise HTTPException(status_code=422, detail="Unreadable CSV") from None
    return link, header, rows


def _csv_response(header, rows):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=[*header, "url"], extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="invitations.csv"'})


@router.post("/studies/{study_id}/fielding/links/{link_id}/invitations")
async def invitations(
    study_id: str, link_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """CSV (email and/or phone and/or name) in, same CSV plus `url` out. (Owner/Analyst)

    url = the link's public respondent URL with ?pid=HMAC-SHA256(secret, contact)[:16]
    (lowercased email, else digits-only phone). The secret is DVV_IP_SALT, else the JWT
    secret. The client sends the mail from their own tool. Nothing from the upload is
    stored or logged. Rotating the salt (or JWT secret) breaks reminders for links
    already sent: their pids no longer match.
    """
    _, header, rows = await _invite_rows(db, study_id, link_id, file)
    return _csv_response(header, rows)


@router.post("/studies/{study_id}/fielding/links/{link_id}/reminders")
async def reminders(
    study_id: str, link_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Same CSV as invitations, but only rows whose pid has no terminal session on this
    link (same rule as pid_already_finished: status_code set). (Owner/Analyst)

    Rotating the salt breaks reminders for links already sent: every pid changes, so
    finished respondents would be reminded again.
    """
    from api.db import RespondentSessionDB

    link, header, rows = await _invite_rows(db, study_id, link_id, file)
    done = {
        p for (p,) in db.query(RespondentSessionDB.pid).filter(
            RespondentSessionDB.fielding_link_id == link.id,
            RespondentSessionDB.pid.isnot(None),
            RespondentSessionDB.status_code.isnot(None),
        )
    }
    return _csv_response(header, unfinished(rows, done))

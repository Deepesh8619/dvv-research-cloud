"""Tier A of the founders' console: editing how the app READS, without code.

Vaibhav and Vishal know the language their clients and their researchers use;
I do not. Today changing "Questionnaire review" to whatever an MR team actually
calls it needs an engineer, a deploy, and a round trip through me. This makes it
a text box.

The safety property is the fallback, not a permission check. Every instrumented
string renders through `useLabel(key, default)` in the frontend: when there is
no override, when it is switched off, when this endpoint 404s, or when the
network is down, the hardcoded default is used. So the worst outcome of a bad
override is one label reading wrong — never a broken screen — and reverting is
a single row flip.

Deliberately NOT in v1: visibility, ordering, layout. Each needs its own
per-key semantics and validation, and each can genuinely break a screen. Text
first, where the blast radius is a word.
"""

import logging
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.auth import get_current_user
from api.db import (
    UIOverrideDB,
    UserDB,
    active_ui_overrides,
    get_db,
    record_audit,
)
from api.models import UIOverrideRequest
from api.routes.corrections import require_correction_access

logger = logging.getLogger(__name__)
router = APIRouter()

# The registry of editable strings. A key is only editable if it is declared
# here, so a founder browsing the console sees a curated list of real UI copy
# rather than a free-text field that can write anything into any key.
#
# `default` MUST match the fallback passed to useLabel() in the component —
# it is what the console shows as "currently", and what revert returns to.
# Adding a new editable string = one entry here + one useLabel() call.
UI_KEYS: list[dict] = [
    # --- Questionnaire review -------------------------------------------
    {"key": "questionnaire.review.title", "default": "Questionnaire review",
     "where": "Questionnaire review", "note": "Page heading"},
    {"key": "questionnaire.review.empty", "default": "Select a question to review its details.",
     "where": "Questionnaire review", "note": "Shown before a question is picked"},
    {"key": "questionnaire.review.approve", "default": "Approve",
     "where": "Questionnaire review", "note": "Per-question approve button"},
    {"key": "questionnaire.review.approved", "default": "Approved",
     "where": "Questionnaire review", "note": "State after approving"},
    {"key": "questionnaire.review.approveAll", "default": "Approve all",
     "where": "Questionnaire review", "note": "Bulk approve"},
    {"key": "questionnaire.upload.title", "default": "Upload questionnaire",
     "where": "Questionnaire review", "note": "Upload card heading"},
    {"key": "questionnaire.upload.help",
     "default": "Upload a .docx or .pdf questionnaire. It will be extracted into structured "
                "questions for review.",
     "where": "Questionnaire review", "note": "Upload card help text"},
    {"key": "questionnaire.upload.choose", "default": "Choose file",
     "where": "Questionnaire review", "note": "File picker button"},
    {"key": "questionnaire.detectedFormat", "default": "Detected format",
     "where": "Questionnaire review", "note": "Prefix before the profile name"},

    # --- Correction loop (the founders' own surface) ---------------------
    {"key": "correction.trigger", "default": "Correct this",
     "where": "Questionnaire review", "note": "Opens the correction box"},
    {"key": "correction.heading", "default": "What did we read wrong?",
     "where": "Correction box", "note": "Correction box heading"},
    {"key": "correction.help",
     "default": "Describe the mistake the way you'd explain it to a colleague. If it's about "
                "how this client writes questionnaires, say so — that teaches the parser for "
                "every future document.",
     "where": "Correction box", "note": "Guidance above the text area"},
    {"key": "correction.submit", "default": "Send correction",
     "where": "Correction box", "note": "Submit button"},
    {"key": "correction.scopeLabel", "default": "This applies to",
     "where": "Correction box", "note": "Label above the scope chips"},
    {"key": "correction.suggestLabel", "default": "Suggested — click to fill, then edit",
     "where": "Correction box", "note": "Label above suggestions"},
    {"key": "preview.trigger", "default": "See what this changes",
     "where": "Correction box", "note": "Starts the before/after re-read"},
    {"key": "preview.approve", "default": "Approve & teach the parser",
     "where": "Before/after", "note": "Applies the rule"},
    {"key": "preview.improved", "default": "Improved",
     "where": "Before/after", "note": "Counts row"},
    {"key": "preview.regressed", "default": "Regressed",
     "where": "Before/after", "note": "Counts row"},
    {"key": "preview.changed", "default": "Changed",
     "where": "Before/after", "note": "Counts row"},

    # --- Projects / dashboard --------------------------------------------
    {"key": "projects.title", "default": "Projects",
     "where": "Projects", "note": "Page heading"},
    {"key": "projects.new", "default": "New Project",
     "where": "Navigation", "note": "Sidebar + button"},
    {"key": "dashboard.title", "default": "Dashboard",
     "where": "Dashboard", "note": "Page heading"},
    {"key": "dashboard.subtitle", "default": "Overview of your research projects",
     "where": "Dashboard", "note": "Under the heading"},
    {"key": "dashboard.total", "default": "Total projects",
     "where": "Dashboard", "note": "KPI label"},
    {"key": "dashboard.inProgress", "default": "In progress",
     "where": "Dashboard", "note": "KPI label"},
    {"key": "dashboard.awaiting", "default": "Awaiting sign-off",
     "where": "Dashboard", "note": "KPI label"},
    {"key": "dashboard.delivered", "default": "Delivered",
     "where": "Dashboard", "note": "KPI label"},

    # --- Fielding ---------------------------------------------------------
    {"key": "fielding.title", "default": "Fielding",
     "where": "Fielding", "note": "Page heading"},
    {"key": "fielding.complete", "default": "Complete",
     "where": "Fielding", "note": "Respondent status"},
    {"key": "fielding.terminate", "default": "Screened out",
     "where": "Fielding", "note": "Respondent status"},
    {"key": "fielding.overquota", "default": "Over quota",
     "where": "Fielding", "note": "Respondent status"},
]

_KEYS_BY_ID = {k["key"]: k for k in UI_KEYS}

# A value long enough to break a button is a value worth refusing.
MAX_OVERRIDE_CHARS = 400


def _now() -> str:
    return datetime.now(UTC).isoformat()


@router.get("/ui/overrides")
async def get_overrides(
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """Live overrides for the whole app: {key: value}.

    Read by EVERY signed-in user, not just founders — an override is a change
    to the product, not a founders-only view. Deliberately cheap and
    fail-soft: an empty dict is a completely valid answer and simply means the
    app renders its built-in copy.
    """
    return {"overrides": active_ui_overrides(db)}


@router.get("/ui/keys")
async def list_keys(
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_correction_access),
):
    """The editable-string registry, with each key's default and current value.

    Founders browse this rather than typing key names: a key that is not
    declared in UI_KEYS is not editable, so the console can never write copy
    into a key nothing renders.
    """
    current = active_ui_overrides(db)
    return {
        "keys": [
            {
                **k,
                "current": current.get(k["key"], k["default"]),
                "overridden": k["key"] in current,
            }
            for k in UI_KEYS
        ],
        "count": len(UI_KEYS),
    }


@router.post("/ui/overrides")
async def set_override(
    request: UIOverrideRequest,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_correction_access),
):
    """Change one piece of UI copy. Takes effect on the next page load.

    Refuses unknown keys: the registry is the contract, and writing to a key
    no component reads would look like a silent failure to the founder.
    """
    key = (request.key or "").strip()
    if key not in _KEYS_BY_ID:
        raise HTTPException(
            status_code=400,
            detail=f"'{key}' is not an editable label. See /api/ui/keys for the list.",
        )
    value = (request.value or "").strip()
    if not value:
        raise HTTPException(
            status_code=400,
            detail="Empty text would leave a blank label — use revert to restore the default.",
        )
    if len(value) > MAX_OVERRIDE_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"Keep it under {MAX_OVERRIDE_CHARS} characters.",
        )

    row = db.query(UIOverrideDB).filter(UIOverrideDB.key == key).first()
    before = {"value": row.value, "active": bool(row.active)} if row else None
    if row:
        row.value = value
        row.note = request.note
        row.active = True
        row.updated_by = user.id
        row.updated_at = _now()
    else:
        row = UIOverrideDB(
            id=f"uio_{uuid4().hex[:12]}",
            key=key,
            value=value,
            active=True,
            note=request.note,
            updated_by=user.id,
            created_at=_now(),
            updated_at=_now(),
        )
        db.add(row)

    record_audit(
        db, entity="ui_override", entity_id=key, action="set", actor_id=user.id,
        summary=f"{key}: {value[:120]}",
        before=before, after={"value": value, "active": True},
    )
    db.commit()
    logger.info(f"UI override {key} set by {user.email}")
    return {
        "key": key,
        "value": value,
        "default": _KEYS_BY_ID[key]["default"],
        "overridden": True,
    }


@router.post("/ui/overrides/{key:path}/revert")
async def revert_override(
    key: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_correction_access),
):
    """Restore the built-in wording. The one-action undo for a bad edit.

    Deactivates rather than deletes, so "what did we change it to, and why"
    survives the revert.
    """
    if key not in _KEYS_BY_ID:
        raise HTTPException(status_code=404, detail=f"'{key}' is not an editable label.")
    row = db.query(UIOverrideDB).filter(UIOverrideDB.key == key).first()
    if not row or not row.active:
        raise HTTPException(status_code=404, detail=f"'{key}' is not currently overridden.")

    row.active = False
    row.updated_by = user.id
    row.updated_at = _now()
    record_audit(
        db, entity="ui_override", entity_id=key, action="reverted", actor_id=user.id,
        summary=f"{key} restored to the built-in wording",
        before={"value": row.value, "active": True}, after={"active": False},
    )
    db.commit()
    logger.info(f"UI override {key} reverted by {user.email}")
    return {"key": key, "value": _KEYS_BY_ID[key]["default"], "overridden": False}

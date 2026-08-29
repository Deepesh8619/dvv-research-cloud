"""The correction loop: founders teach the questionnaire parser in plain language.

A correction is a sentence a researcher writes against an extracted questionnaire
("the terminate wasn't identified — this client puts it in the TERM row"). The
value is not the sentence; it is that the sentence can become a RULE attached to
the document's format profile, so it applies to every question in the document
and to every future document of that client format.

Two lanes, decided by `_classify`:
  live      -> an extraction rule. Re-running extraction is safe because it only
               ever rewrites DRAFT questionnaire data; nothing in fielding,
               quotas, or respondent data is reachable from here.
  proposal  -> needs code (a new question type, quota logic). The model writes a
               structured spec, nobody applies anything, it queues for the owner.

Ambiguity always routes to `proposal`. A correction that should have been instant
but waits costs an afternoon; one that should have waited but was applied costs
trust in the whole system. That asymmetry is not close.
"""

import json
import logging
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.auth import UserRole, get_current_user
from api.db import (
    CorrectionDB,
    ProfileRuleDB,
    UserDB,
    get_db,
    get_study,
    list_questionnaires_for_study,
)
from api.models import (
    ApplyRuleRequest,
    CorrectionRequest,
    CorrectionResponse,
    ProfileRuleResponse,
    SuggestionResponse,
)
from src.ai.router import BackendUnavailable, ModelOutputError, run_task

logger = logging.getLogger(__name__)
router = APIRouter()

# Who may reach the correction surface at all.
_CORRECTION_ROLES = {UserRole.OWNER, UserRole.FOUNDER}

VALID_SCOPES = ("question", "document", "profile", "global")

# A `global` rule rewrites extraction for EVERY client format, so it is the one
# scope a founder cannot approve alone.
_OWNER_ONLY_SCOPES = {"global"}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def require_correction_access(user: UserDB = Depends(get_current_user)) -> UserDB:
    """Gate the founders' lane server-side.

    Enforced on every endpoint in this module rather than by hiding the button —
    a hidden button is not access control.
    """
    try:
        role = UserRole(user.role)
    except ValueError:
        role = None
    if role not in _CORRECTION_ROLES:
        raise HTTPException(
            status_code=403,
            detail="The correction loop is limited to founders.",
        )
    return user


# --------------------------------------------------------------------------
# Suggestions — the instruction box arrives knowing what is probably wrong
# --------------------------------------------------------------------------

# The correction shapes that recur across market-research questionnaires. Offered
# when the extractor itself raised no specific flag, so the box is never blank.
_COMMON_SUGGESTIONS = [
    "The terminate/screen-out condition wasn't identified for this question.",
    "The question text is wrong or cut off.",
    "Answer options are missing from this question.",
    "Extra options were invented that aren't in the document.",
    "The question type is wrong (it should be single/multi/numeric/open).",
    "The routing or skip logic wasn't captured.",
    "The scale points are wrong for this question.",
    "The base / who-gets-asked filter was misread.",
]

# Extractor flags -> the correction a reviewer most likely wants to write. Keyed
# on substring so a flag family (`missing_options`, `low_confidence_options`)
# maps without enumerating every variant.
_FLAG_SUGGESTIONS = {
    "terminate": (
        "The terminate condition wasn't identified. In this client's documents "
        "it is written in a specific place — describe where."
    ),
    "option": "The answer options are wrong or incomplete for this question.",
    "routing": "The routing / skip logic wasn't captured correctly.",
    "type": "The question type was detected incorrectly.",
    "text": "The question text is wrong or truncated.",
    "confidence": "The extractor was unsure here — describe what it got wrong.",
}


def _flagged_suggestions(question: dict) -> list[dict]:
    """Suggestions derived from the extractor's OWN uncertainty on this question.

    Highest-value source: the system already recorded where it was unsure, so
    turning those flags into opening sentences costs nothing and points the
    reviewer straight at the real defect.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for flag in question.get("flags") or []:
        low = str(flag).lower()
        for key, text in _FLAG_SUGGESTIONS.items():
            if key in low and text not in seen:
                seen.add(text)
                out.append({"source": "flagged", "text": text, "flag": flag})
    confidence = question.get("confidence")
    if isinstance(confidence, (int, float)) and confidence < 0.8:
        text = _FLAG_SUGGESTIONS["confidence"]
        if text not in seen:
            out.append({"source": "flagged", "text": text, "flag": "low_confidence"})
    return out


@router.get(
    "/studies/{study_id}/corrections/suggestions", response_model=SuggestionResponse
)
async def get_suggestions(
    study_id: str,
    question_id: str | None = None,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_correction_access),
):
    """Ranked suggestions for the instruction box: flagged, then learned, then common.

    A blank box is the wrong interface for a domain expert who is not a prompt
    engineer. Clicking a suggestion FILLS the box (the UI never auto-submits), so
    the correction still ends up in the author's own words.
    """
    questionnaire = _latest_questionnaire(db, study_id)
    profile_slug = _profile_slug(questionnaire)

    suggestions: list[dict] = []
    if question_id and questionnaire and questionnaire.definition_json:
        question = _find_question(questionnaire.definition_json, question_id)
        if question:
            suggestions.extend(_flagged_suggestions(question))

    # Precedent: rules already accepted on this format, offered so a founder can
    # reuse a phrasing that is known to work rather than reinventing it.
    if profile_slug:
        learned = (
            db.query(ProfileRuleDB)
            .filter(ProfileRuleDB.profile_slug == profile_slug)
            .filter(ProfileRuleDB.active.is_(True))
            .order_by(ProfileRuleDB.ordinal)
            .limit(3)
            .all()
        )
        suggestions.extend(
            {"source": "learned", "text": r.rule_text, "flag": None} for r in learned
        )

    suggestions.extend(
        {"source": "common", "text": t, "flag": None} for t in _COMMON_SUGGESTIONS
    )
    return SuggestionResponse(
        study_id=study_id,
        question_id=question_id,
        profile_slug=profile_slug,
        suggestions=suggestions[:12],
    )


def _latest_questionnaire(db: Session, study_id: str):
    """The study's most recent questionnaire — the one under review."""
    rows = list_questionnaires_for_study(db, study_id)
    return rows[0] if rows else None


def _profile_slug(questionnaire) -> str | None:
    """The detected format profile slug for a questionnaire, if extraction ran."""
    if not questionnaire or not questionnaire.definition_json:
        return None
    try:
        meta = json.loads(questionnaire.definition_json).get("metadata") or {}
    except (json.JSONDecodeError, TypeError):
        return None
    profile = meta.get("profile")
    if isinstance(profile, dict):
        return profile.get("slug")
    return profile if isinstance(profile, str) else None


def _find_question(definition_json: str, qid: str) -> dict | None:
    try:
        questions = json.loads(definition_json).get("questions") or []
    except (json.JSONDecodeError, TypeError):
        return None
    return next((q for q in questions if q.get("qid") == qid), None)


# --------------------------------------------------------------------------
# Classification — which lane, and is the instruction usable as a rule
# --------------------------------------------------------------------------

_CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "lane": {"type": "string", "enum": ["live", "proposal"]},
        "scope": {"type": "string", "enum": list(VALID_SCOPES)},
        "rule_text": {"type": "string"},
        "rationale": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": ["lane", "scope", "rule_text", "rationale"],
}


def _classify_prompt(instruction: str, question: dict | None, profile: str | None) -> str:
    ctx = ""
    if question:
        ctx = (
            "The correction was written against this extracted question:\n"
            f"{json.dumps(question, indent=2)[:2000]}\n\n"
        )
    return (
        "A market-research expert is correcting how our system reads client "
        "questionnaire documents. Turn their correction into a reusable EXTRACTION "
        "RULE, and decide how far it applies.\n\n"
        f"Detected document format profile: {profile or 'unknown'}\n\n"
        f"{ctx}"
        f"----- THEIR CORRECTION -----\n{instruction}\n----- END -----\n\n"
        "lane:\n"
        "  'live'     = this is about how to READ the document (where terminates, "
        "question text, options, types, routing or scales live). It can be fixed by "
        "re-reading the document with better instructions.\n"
        "  'proposal' = this needs new software (a new question type, quota logic, "
        "a new screen, schema changes). Choose this whenever you are unsure.\n\n"
        "scope:\n"
        "  'question' = a one-off defect in this question only.\n"
        "  'document' = applies to this document but is not a client convention.\n"
        "  'profile'  = a convention of how THIS CLIENT writes questionnaires. "
        "Prefer this when they describe where something is always written.\n"
        "  'global'   = a universal market-research convention we have wrong.\n\n"
        "rule_text: rewrite their correction as ONE clear instruction addressed to "
        "the extractor, in the imperative, that will still make sense on a different "
        "document of the same format. Do not mention specific question ids unless "
        "the scope is 'question'.\n"
        "rationale: one sentence on why you chose that lane and scope."
    )


def _classify(instruction: str, question: dict | None, profile: str | None) -> dict:
    """Ask the model for lane + scope + rule text. Fails safe to the proposal lane.

    Any backend/model failure returns a proposal-lane result carrying the raw
    instruction, so a correction is never silently lost and never auto-applied
    on a degraded classification.
    """
    try:
        response = run_task(
            "questionnaire.extract",
            _classify_prompt(instruction, question, profile),
            _CLASSIFY_SCHEMA,
        )
        data = response.data if hasattr(response, "data") else response
        if isinstance(data, str):
            data = json.loads(data)
        lane = data.get("lane")
        scope = data.get("scope")
        return {
            "lane": lane if lane in ("live", "proposal") else "proposal",
            "scope": scope if scope in VALID_SCOPES else "document",
            "rule_text": (data.get("rule_text") or instruction).strip(),
            "rationale": data.get("rationale") or "",
            "confidence": data.get("confidence"),
            "degraded": False,
        }
    except (BackendUnavailable, ModelOutputError, KeyError, json.JSONDecodeError) as exc:
        logger.warning(f"Correction classification unavailable, queuing: {exc}")
        return {
            "lane": "proposal",
            "scope": "document",
            "rule_text": instruction.strip(),
            "rationale": (
                "Classification was unavailable, so this was queued for review "
                "rather than applied."
            ),
            "confidence": None,
            "degraded": True,
        }


# --------------------------------------------------------------------------
# Corrections
# --------------------------------------------------------------------------


@router.post("/studies/{study_id}/corrections", response_model=CorrectionResponse)
async def create_correction(
    study_id: str,
    request: CorrectionRequest,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_correction_access),
):
    """Record a correction and draft a rule from it. Applies nothing.

    The correction is stored in the author's own words first, then classified.
    Storing before classifying means a model outage costs a slower loop, not a
    lost observation.
    """
    instruction = (request.instruction or "").strip()
    if not instruction:
        raise HTTPException(status_code=400, detail="Write what is wrong first.")
    if not get_study(db, study_id):
        raise HTTPException(status_code=404, detail="Study not found")

    questionnaire = _latest_questionnaire(db, study_id)
    profile_slug = _profile_slug(questionnaire)
    question = (
        _find_question(questionnaire.definition_json, request.question_id)
        if request.question_id and questionnaire and questionnaire.definition_json
        else None
    )

    verdict = _classify(instruction, question, profile_slug)
    # The author's confirmed scope wins over the model's guess — the UI shows the
    # inferred scope as a chip and lets them change it before sending.
    scope = request.scope if request.scope in VALID_SCOPES else verdict["scope"]

    correction = CorrectionDB(
        id=f"corr_{uuid4().hex[:12]}",
        study_id=study_id,
        question_id=request.question_id,
        author_id=user.id,
        instruction=instruction,
        suggested_from=request.suggested_from or "freeform",
        scope=scope,
        profile_slug=profile_slug,
        status="resolved" if verdict["lane"] == "live" else "queued",
        created_at=_now(),
    )
    db.add(correction)
    db.commit()
    db.refresh(correction)

    logger.info(
        f"[{study_id}] correction {correction.id} by {user.email}: "
        f"lane={verdict['lane']} scope={scope}"
    )
    return _correction_response(correction, verdict)


def _correction_response(correction: CorrectionDB, verdict: dict) -> CorrectionResponse:
    return CorrectionResponse(
        id=correction.id,
        study_id=correction.study_id,
        question_id=correction.question_id,
        instruction=correction.instruction,
        scope=correction.scope,
        profile_slug=correction.profile_slug,
        status=correction.status,
        lane=verdict["lane"],
        rule_text=verdict["rule_text"],
        rationale=verdict["rationale"],
        degraded=verdict.get("degraded", False),
        created_at=correction.created_at,
    )


@router.get("/studies/{study_id}/corrections")
async def list_corrections(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_correction_access),
):
    """Every correction written against this study, newest first."""
    rows = (
        db.query(CorrectionDB)
        .filter(CorrectionDB.study_id == study_id)
        .order_by(CorrectionDB.created_at.desc())
        .all()
    )
    return {
        "study_id": study_id,
        "corrections": [
            {
                "id": c.id,
                "question_id": c.question_id,
                "instruction": c.instruction,
                "scope": c.scope,
                "profile_slug": c.profile_slug,
                "status": c.status,
                "suggested_from": c.suggested_from,
                "created_at": c.created_at,
            }
            for c in rows
        ],
    }


# --------------------------------------------------------------------------
# Rules — the part that makes corrections compound
# --------------------------------------------------------------------------


@router.post("/profiles/{profile_slug}/rules", response_model=ProfileRuleResponse)
async def apply_rule(
    profile_slug: str,
    request: ApplyRuleRequest,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_correction_access),
):
    """Approve a drafted rule onto a format profile.

    From here the rule is appended to the extraction prompt for every document
    detected as `profile_slug` — including documents uploaded later. That is the
    whole point of the loop, so it is an explicit, attributed, reversible act.
    """
    rule_text = (request.rule_text or "").strip()
    if not rule_text:
        raise HTTPException(status_code=400, detail="A rule needs text.")
    scope = request.scope if request.scope in VALID_SCOPES else "profile"
    if scope in _OWNER_ONLY_SCOPES and UserRole(user.role) != UserRole.OWNER:
        raise HTTPException(
            status_code=403,
            detail="A global rule changes extraction for every client format, "
            "so it needs owner approval.",
        )

    highest = (
        db.query(ProfileRuleDB)
        .filter(ProfileRuleDB.profile_slug == profile_slug)
        .order_by(ProfileRuleDB.ordinal.desc())
        .first()
    )
    rule = ProfileRuleDB(
        id=f"rule_{uuid4().hex[:12]}",
        profile_slug=profile_slug,
        rule_text=rule_text,
        source_correction_id=request.correction_id,
        scope=scope,
        # Appended last so it wins over existing rules on conflict — matches
        # what build_learned_rules_block tells the model.
        ordinal=(highest.ordinal + 1) if highest else 0,
        active=True,
        approved_by=user.id,
        approved_at=_now(),
        created_at=_now(),
    )
    db.add(rule)
    if request.correction_id:
        correction = (
            db.query(CorrectionDB)
            .filter(CorrectionDB.id == request.correction_id)
            .first()
        )
        if correction:
            correction.status = "applied"
    db.commit()
    db.refresh(rule)
    logger.info(f"Rule {rule.id} applied to profile {profile_slug} by {user.email}")
    return _rule_response(rule)


@router.get("/profiles/{profile_slug}/rules")
async def list_rules(
    profile_slug: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_correction_access),
):
    """The rule registry for one format — what the parser has been taught."""
    rows = (
        db.query(ProfileRuleDB)
        .filter(ProfileRuleDB.profile_slug == profile_slug)
        .order_by(ProfileRuleDB.ordinal)
        .all()
    )
    return {
        "profile_slug": profile_slug,
        "rules": [_rule_response(r).model_dump() for r in rows],
    }


@router.post("/profiles/{profile_slug}/rules/{rule_id}/toggle")
async def toggle_rule(
    profile_slug: str,
    rule_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_correction_access),
):
    """Switch a rule off (or back on). The undo for a rule that made things worse.

    Nothing is deleted — a retired rule stays readable, because "why did we stop
    doing that" gets asked later.
    """
    rule = (
        db.query(ProfileRuleDB)
        .filter(ProfileRuleDB.id == rule_id)
        .filter(ProfileRuleDB.profile_slug == profile_slug)
        .first()
    )
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    rule.active = not bool(rule.active)
    db.commit()
    db.refresh(rule)
    logger.info(f"Rule {rule.id} active={rule.active} by {user.email}")
    return _rule_response(rule)


def _rule_response(rule: ProfileRuleDB) -> ProfileRuleResponse:
    return ProfileRuleResponse(
        id=rule.id,
        profile_slug=rule.profile_slug,
        rule_text=rule.rule_text,
        scope=rule.scope,
        ordinal=rule.ordinal,
        active=bool(rule.active),
        approved_by=rule.approved_by,
        approved_at=rule.approved_at,
        created_at=rule.created_at,
    )

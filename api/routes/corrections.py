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
import threading
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.auth import UserRole, get_current_user
from api.config import STUDIES_DIR
from api.db import (
    AuditLogDB,
    CorrectionDB,
    CorrectionPreviewDB,
    ProfileRuleDB,
    UserDB,
    get_db,
    get_questionnaire,
    get_study,
    list_questionnaires_for_study,
    record_audit,
    set_questionnaire_definition,
)
from api.models import (
    ApplyRuleRequest,
    CorrectionRequest,
    CorrectionResponse,
    ProfileRuleResponse,
    ReorderRulesRequest,
    ReviseRequest,
    SuggestionResponse,
)
from src.ai.router import ModelOutputError, run_task
from src.engines.questionnaire.diffing import diff_extractions
from src.engines.questionnaire.extractor import extract_survey_definition
from src.intake.questionnaire_loader import (
    load_docx,
    load_pdf,
    segment_into_questions,
)

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


def _classify_prompt(
    instruction: str,
    question: dict | None,
    profile: str | None,
    revise_note: str | None = None,
) -> str:
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
        + (
            "A previous attempt at this was REJECTED by the reviewer with this "
            f"note. Address it specifically:\n{revise_note}\n\n"
            if revise_note
            else ""
        )
        +
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


def _classify(
    instruction: str,
    question: dict | None,
    profile: str | None,
    revise_note: str | None = None,
) -> dict:
    """Ask the model for lane + scope + rule text. Fails safe to the proposal lane.

    Any backend/model failure returns a proposal-lane result carrying the raw
    instruction, so a correction is never silently lost and never auto-applied
    on a degraded classification.
    """
    try:
        response = run_task(
            "questionnaire.extract",
            _classify_prompt(instruction, question, profile, revise_note),
            _CLASSIFY_SCHEMA,
        )
        # run_task returns AIResponse, whose parsed payload is `.content`.
        data = response.content
        if isinstance(data, str):
            data = json.loads(data)
        if not isinstance(data, dict):
            raise ModelOutputError(f"classifier returned {type(data).__name__}, not an object")
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
    except Exception as exc:  # noqa: BLE001
        # Deliberately catch EVERYTHING. The point of this function is that a
        # correction is never lost and never auto-applied on a bad
        # classification — a 500 here throws away what a founder wrote, which
        # is worse than any degraded verdict. (An earlier narrow `except` let
        # an AttributeError through and did exactly that.)
        logger.warning(
            f"Correction classification failed ({type(exc).__name__}), queuing: {exc}"
        )
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
        lane=verdict["lane"],
        resolution_json=json.dumps(verdict),
        attempt_no=1,
        created_at=_now(),
    )
    db.add(correction)
    record_audit(
        db, entity="correction", entity_id=correction.id, action="created",
        actor_id=user.id, study_id=study_id,
        summary=f"{verdict['lane']} / {scope}: {instruction[:120]}",
        after={"lane": verdict["lane"], "scope": scope, "rule_text": verdict["rule_text"]},
    )
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
    record_audit(
        db, entity="profile_rule", entity_id=rule.id, action="applied",
        actor_id=user.id,
        summary=f"{profile_slug} ({scope}): {rule_text[:120]}",
        after={"profile_slug": profile_slug, "scope": scope, "rule_text": rule_text},
    )
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
    was_active = bool(rule.active)
    rule.active = not was_active
    record_audit(
        db, entity="profile_rule", entity_id=rule.id,
        action="enabled" if rule.active else "disabled",
        actor_id=user.id,
        summary=f"{profile_slug}: {(rule.rule_text or '')[:120]}",
        before={"active": was_active}, after={"active": bool(rule.active)},
    )
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


# --------------------------------------------------------------------------
# Preview (P4) — re-read the WHOLE document under a candidate rule and diff
# --------------------------------------------------------------------------


def _source_document(study_id: str, questionnaire_id: str) -> Path | None:
    """The uploaded .docx/.pdf, if it still exists.

    Lives on Render's EPHEMERAL disk, so it is gone after any restart even
    though the extracted definition survives in Postgres. Callers must treat
    None as "cannot preview, ask for a re-upload" rather than an error.
    """
    directory = STUDIES_DIR / study_id / "questionnaires"
    for suffix in (".docx", ".pdf"):
        candidate = directory / f"{questionnaire_id}{suffix}"
        if candidate.exists():
            return candidate
    return None


def _run_preview(preview_id: str, study_id: str, questionnaire_id: str,
                 source: Path, rule_text: str, profile_slug: str) -> None:
    """Re-extract the document under `rule_text` and store the diff.

    Runs on a daemon thread (matching questionnaire.py's upload pattern) with a
    fresh session — one LLM call per chunk is far too slow to hold a request
    open. Writes ONLY to correction_previews: the questionnaire's live
    definition is never touched here, so a bad rule cannot damage anything
    before a human has looked at the diff.
    """
    from api.db import SessionLocal, active_rules_for_profile

    job_db = SessionLocal()
    try:
        blocks = (
            load_docx(source) if source.suffix.lower() == ".docx" else load_pdf(source)
        )
        chunks = segment_into_questions(blocks)

        # Candidate rule set = what is already active, plus the one under review
        # appended last so it wins on conflict — the same ordering the prompt
        # promises the model.
        candidate_rules = [*active_rules_for_profile(job_db, profile_slug), rule_text]

        survey = extract_survey_definition(
            chunks,
            study_id=study_id,
            blocks=blocks,
            rules_for_profile=lambda _slug: candidate_rules,
        )
        after = json.loads(survey.to_json()).get("questions", [])

        questionnaire = get_questionnaire(job_db, questionnaire_id)
        before = json.loads(questionnaire.definition_json or "{}").get("questions", [])

        diff = diff_extractions(before, after)
        row = (
            job_db.query(CorrectionPreviewDB)
            .filter(CorrectionPreviewDB.id == preview_id)
            .first()
        )
        if row:
            row.status = "ready"
            row.candidate_json = survey.to_json()
            row.diff_json = json.dumps(diff.to_dict())
            row.model_calls = len(chunks)
            row.completed_at = _now()
            job_db.commit()
        counts = diff.counts()
        logger.info(
            f"[{study_id}] preview {preview_id}: {counts['improved']} improved, "
            f"{counts['regressed']} regressed over {len(chunks)} chunks"
        )
    except Exception as exc:  # noqa: BLE001 - must not kill the thread silently
        logger.error(f"[{study_id}] preview {preview_id} failed: {exc}", exc_info=True)
        row = (
            job_db.query(CorrectionPreviewDB)
            .filter(CorrectionPreviewDB.id == preview_id)
            .first()
        )
        if row:
            row.status = "error"
            row.error_detail = str(exc)
            row.completed_at = _now()
            job_db.commit()
    finally:
        job_db.close()


@router.post("/studies/{study_id}/corrections/{correction_id}/preview")
async def start_preview(
    study_id: str,
    correction_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_correction_access),
):
    """Re-read the whole document under this correction's rule. Applies nothing.

    Costs one LLM call per chunk, so it is an explicit action and the response
    reports the call count up front rather than surprising anyone with it.
    """
    correction = (
        db.query(CorrectionDB).filter(CorrectionDB.id == correction_id).first()
    )
    if not correction or correction.study_id != study_id:
        raise HTTPException(status_code=404, detail="Correction not found")

    questionnaire = _latest_questionnaire(db, study_id)
    if not questionnaire or not questionnaire.definition_json:
        raise HTTPException(
            status_code=400, detail="This study has no extracted questionnaire to compare against."
        )

    source = _source_document(study_id, questionnaire.id)
    if source is None:
        raise HTTPException(
            status_code=409,
            detail="The original document is no longer on disk (it does not survive a "
            "server restart), so it cannot be re-read. Re-upload it to preview this rule.",
        )

    existing = (
        db.query(CorrectionPreviewDB)
        .filter(CorrectionPreviewDB.correction_id == correction_id)
        .filter(CorrectionPreviewDB.status == "running")
        .first()
    )
    if existing:
        return {"preview_id": existing.id, "status": "running", "reused": True}

    preview = CorrectionPreviewDB(
        id=f"prev_{uuid4().hex[:12]}",
        correction_id=correction_id,
        study_id=study_id,
        rule_text=correction.instruction,
        status="running",
        created_at=_now(),
    )
    db.add(preview)
    db.commit()

    threading.Thread(
        target=_run_preview,
        args=(preview.id, study_id, questionnaire.id, source,
              correction.instruction, correction.profile_slug or ""),
        daemon=True,
    ).start()

    n_chunks = len(segment_into_questions(
        load_docx(source) if source.suffix.lower() == ".docx" else load_pdf(source)
    ))
    logger.info(f"[{study_id}] preview {preview.id} started by {user.email}")
    return {"preview_id": preview.id, "status": "running", "estimated_model_calls": n_chunks}


@router.get("/studies/{study_id}/corrections/{correction_id}/preview")
async def get_preview(
    study_id: str,
    correction_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_correction_access),
):
    """Latest preview for a correction: status, and the diff once ready."""
    preview = (
        db.query(CorrectionPreviewDB)
        .filter(CorrectionPreviewDB.correction_id == correction_id)
        .order_by(CorrectionPreviewDB.created_at.desc())
        .first()
    )
    if not preview:
        raise HTTPException(status_code=404, detail="No preview for this correction yet")
    return {
        "preview_id": preview.id,
        "status": preview.status,
        "error_detail": preview.error_detail,
        "model_calls": preview.model_calls,
        "diff": json.loads(preview.diff_json) if preview.diff_json else None,
    }


@router.post("/studies/{study_id}/corrections/{correction_id}/preview/approve")
async def approve_preview(
    study_id: str,
    correction_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_correction_access),
):
    """Accept the previewed re-extraction: persist the rule AND make it current.

    This is the only path where a correction changes what the study actually
    holds, and it is reachable only after a human has seen the diff — including
    the regressions, which the diff refuses to hide.
    """
    preview = (
        db.query(CorrectionPreviewDB)
        .filter(CorrectionPreviewDB.correction_id == correction_id)
        .order_by(CorrectionPreviewDB.created_at.desc())
        .first()
    )
    if not preview or preview.status != "ready":
        raise HTTPException(
            status_code=409, detail="No completed preview to approve for this correction."
        )
    correction = (
        db.query(CorrectionDB).filter(CorrectionDB.id == correction_id).first()
    )
    if not correction:
        raise HTTPException(status_code=404, detail="Correction not found")

    scope = correction.scope if correction.scope in VALID_SCOPES else "profile"
    if scope in _OWNER_ONLY_SCOPES and UserRole(user.role) != UserRole.OWNER:
        raise HTTPException(
            status_code=403,
            detail="A global rule changes extraction for every client format, "
            "so it needs owner approval.",
        )

    profile_slug = correction.profile_slug or ""
    highest = (
        db.query(ProfileRuleDB)
        .filter(ProfileRuleDB.profile_slug == profile_slug)
        .order_by(ProfileRuleDB.ordinal.desc())
        .first()
    )
    rule = ProfileRuleDB(
        id=f"rule_{uuid4().hex[:12]}",
        profile_slug=profile_slug,
        rule_text=preview.rule_text,
        source_correction_id=correction_id,
        scope=scope,
        ordinal=(highest.ordinal + 1) if highest else 0,
        active=True,
        approved_by=user.id,
        approved_at=_now(),
        created_at=_now(),
    )
    db.add(rule)

    questionnaire = _latest_questionnaire(db, study_id)
    if questionnaire and preview.candidate_json:
        set_questionnaire_definition(db, questionnaire.id, preview.candidate_json)
    correction.status = "applied"
    counts = {}
    try:
        counts = json.loads(preview.diff_json or "{}").get("counts", {})
    except json.JSONDecodeError:
        pass
    record_audit(
        db, entity="preview", entity_id=preview.id, action="approved",
        actor_id=user.id, study_id=study_id,
        summary=(
            f"applied rule {rule.id} to {profile_slug}; "
            f"{counts.get('improved', 0)} improved, {counts.get('regressed', 0)} regressed"
        ),
        after={"rule_id": rule.id, "counts": counts},
    )
    db.commit()
    db.refresh(rule)
    logger.info(
        f"[{study_id}] preview {preview.id} approved by {user.email} -> rule {rule.id}"
    )
    return {"rule": _rule_response(rule).model_dump(), "applied": True}


# --------------------------------------------------------------------------
# Revise, reorder, proposal queue, audit — closing the loop's remaining gaps
# --------------------------------------------------------------------------


@router.post("/studies/{study_id}/corrections/{correction_id}/revise")
async def revise_correction(
    study_id: str,
    correction_id: str,
    request: ReviseRequest,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_correction_access),
):
    """Send a correction back to the model with a note. Makes revising a LOOP.

    Without this, a founder whose rule broke Q31 can only rewrite from scratch,
    throwing away everything the model already understood. The note ("this broke
    Q31, that one uses a different row label") rides into the next
    classification, so the second attempt starts from the first rather than
    beside it.
    """
    note = (request.note or "").strip()
    if not note:
        raise HTTPException(status_code=400, detail="Say what was wrong with it.")

    correction = (
        db.query(CorrectionDB).filter(CorrectionDB.id == correction_id).first()
    )
    if not correction or correction.study_id != study_id:
        raise HTTPException(status_code=404, detail="Correction not found")

    questionnaire = _latest_questionnaire(db, study_id)
    question = (
        _find_question(questionnaire.definition_json, correction.question_id)
        if correction.question_id and questionnaire and questionnaire.definition_json
        else None
    )

    verdict = _classify(
        correction.instruction, question, correction.profile_slug, revise_note=note
    )
    correction.revise_note = note
    correction.attempt_no = (correction.attempt_no or 1) + 1
    correction.lane = verdict["lane"]
    correction.resolution_json = json.dumps(verdict)
    correction.status = "resolved" if verdict["lane"] == "live" else "queued"

    # A revision invalidates any preview built from the previous attempt —
    # showing a stale diff next to a new rule would be actively misleading.
    db.query(CorrectionPreviewDB).filter(
        CorrectionPreviewDB.correction_id == correction_id
    ).delete(synchronize_session=False)

    record_audit(
        db, entity="correction", entity_id=correction_id, action="revised",
        actor_id=user.id, study_id=study_id,
        summary=f"attempt {correction.attempt_no}: {note[:120]}",
        after={"note": note, "lane": verdict["lane"], "rule_text": verdict["rule_text"]},
    )
    db.commit()
    db.refresh(correction)
    return _correction_response(correction, verdict)


@router.post("/profiles/{profile_slug}/rules/reorder")
async def reorder_rules(
    profile_slug: str,
    request: ReorderRulesRequest,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_correction_access),
):
    """Set the order rules are applied in. Later rules win on conflict.

    Order is not cosmetic here — `build_learned_rules_block` tells the model
    explicitly that a later rule overrides an earlier one, so this is how two
    rules that disagree get resolved without deleting either.
    """
    rows = {
        r.id: r
        for r in db.query(ProfileRuleDB)
        .filter(ProfileRuleDB.profile_slug == profile_slug)
        .all()
    }
    unknown = [rid for rid in request.rule_ids if rid not in rows]
    if unknown:
        raise HTTPException(
            status_code=400, detail=f"Not rules of this profile: {', '.join(unknown)}"
        )
    before = {rid: rows[rid].ordinal for rid in request.rule_ids}
    for position, rule_id in enumerate(request.rule_ids):
        rows[rule_id].ordinal = position
    record_audit(
        db, entity="profile_rule", entity_id=profile_slug, action="reordered",
        actor_id=user.id, summary=f"{len(request.rule_ids)} rules reordered",
        before=before, after={rid: i for i, rid in enumerate(request.rule_ids)},
    )
    db.commit()
    return {
        "profile_slug": profile_slug,
        "rules": [
            _rule_response(rows[rid]).model_dump() for rid in request.rule_ids
        ],
    }


@router.get("/corrections/proposals")
async def list_proposals(
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_correction_access),
):
    """Corrections that need engineering, across every study — the owner's queue.

    Proposal-lane corrections previously landed in the database and were never
    surfaced anywhere, which made the "it queues for the owner" half of the
    two-lane design a claim rather than a feature.
    """
    rows = (
        db.query(CorrectionDB)
        .filter(CorrectionDB.lane == "proposal")
        .filter(CorrectionDB.status != "applied")
        .order_by(CorrectionDB.created_at.desc())
        .all()
    )
    out = []
    for c in rows:
        try:
            resolution = json.loads(c.resolution_json or "{}")
        except json.JSONDecodeError:
            resolution = {}
        out.append(
            {
                "id": c.id,
                "study_id": c.study_id,
                "question_id": c.question_id,
                "instruction": c.instruction,
                "scope": c.scope,
                "profile_slug": c.profile_slug,
                "status": c.status,
                "attempt_no": c.attempt_no,
                "rule_text": resolution.get("rule_text"),
                "rationale": resolution.get("rationale"),
                # True when the model was unreachable, so this was queued by
                # fallback rather than judged to need code.
                "degraded": resolution.get("degraded", False),
                "created_at": c.created_at,
            }
        )
    return {"proposals": out, "count": len(out)}


@router.get("/corrections/audit")
async def read_audit(
    study_id: str | None = None,
    entity_id: str | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_correction_access),
):
    """The correction loop's audit trail, newest first.

    Answers "who approved the rule that broke extraction, and when" — the
    question that only gets asked after something has already gone wrong, which
    is why it has to have been recorded all along.
    """
    # Intent telemetry (searches, views) lives in the same table but is NOT
    # change history — including it here would drown "what changed" in "what
    # was typed". Read those via GET /api/ui/activity instead.
    q = db.query(AuditLogDB).filter(~AuditLogDB.entity.like("activity.%"))
    if study_id:
        q = q.filter(AuditLogDB.study_id == study_id)
    if entity_id:
        q = q.filter(AuditLogDB.entity_id == entity_id)
    rows = q.order_by(AuditLogDB.at.desc()).limit(min(limit, 500)).all()
    return {
        "entries": [
            {
                "id": r.id,
                "entity": r.entity,
                "entity_id": r.entity_id,
                "action": r.action,
                "actor_id": r.actor_id,
                "study_id": r.study_id,
                "summary": r.summary,
                "at": r.at,
            }
            for r in rows
        ],
        "count": len(rows),
    }

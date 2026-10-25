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

import json
import logging
import os
import re
from concurrent.futures.process import BrokenProcessPool

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from api.auth import UserRole, get_current_user, require_role
from api.config import STUDIES_DIR
from api.db import (
    UserDB,
    add_synthetic_sessions,
    close_fielding_link,
    count_completes,
    create_fielding_link,
    create_quota_cell,
    create_respondent_session,
    decrement_quota_cell,
    delete_respondent_answers,
    fielding_link_counts,
    get_db,
    get_fielding_link,
    get_fielding_link_by_token,
    get_quota_cell,
    get_respondent_session,
    get_session_answers_map,
    get_study,
    increment_quota_cell_if_under_limit,
    list_fielding_links_for_study,
    list_questionnaires_for_study,
    list_quota_cells,
    list_respondent_sessions,
    pid_already_finished,
    set_fielding_link_soft_launch,
    set_fielding_link_vendor,
    set_quota_cell_condition,
    set_respondent_session_status,
    touch_respondent_session,
    update_quota_cell_limit,
    upsert_respondent_answer,
)
from api.heavy import run_heavy_detached
from api.models import (
    DryRunRequest,
    FieldingLinkCreateRequest,
    FieldingLinkListResponse,
    FieldingLinkResponse,
    JumpRequest,
    PopulateRequest,
    QuotaCellResponse,
    QuotaCellsCreateRequest,
    QuotaCellsResponse,
    QuotaCellUpdateRequest,
    RespondentAnswersRequest,
    RespondentEntryResponse,
    RespondentSessionResponse,
    SoftLaunchRequest,
    VendorRequest,
)
from api.routes.questionnaire import _read_definition_json
from src.core.survey_definition import (
    THEME_SLUGS,
    Expression,
    SurveyDefinition,
    evaluate,
)
from src.engines.fielding.decisions import QuotaCell
from src.engines.fielding.dryrun import path_to, simulate
from src.engines.fielding.maxdiff_screens import apply_version as apply_maxdiff_version
from src.engines.fielding.maxdiff_screens import design_for as maxdiff_design
from src.engines.fielding.maxdiff_screens import field_names as maxdiff_fields
from src.engines.fielding.maxdiff_screens import items_shown as maxdiff_items_shown
from src.engines.fielding.maxdiff_screens import questions_in as maxdiff_questions
from src.engines.fielding.rotation import arms as rotation_arms
from src.engines.fielding.rotation import assign as assign_arm
from src.engines.fielding.rotation import order_pages
from src.engines.fielding.translation import translated
from src.engines.questionnaire.bipolar import FLIP_ARMS, apply_flip, flipping_questions
from src.engines.questionnaire.derived import with_derived
from src.platform.events import emit
from src.renderers.surveyjs_compiler import compile_to_surveyjs, display_warnings, masked_out

logger = logging.getLogger(__name__)


def _emit_event(db, study_id: str, kind: str, session_id: str) -> None:
    """Tell the control room; a failure here must never fail the respondent."""
    try:
        emit(db, study_id, kind, {"session_id": session_id})
    except Exception:
        logger.warning("[%s] could not emit %s", study_id, kind, exc_info=True)
router = APIRouter()

# Lucid/Cint status codes (key decision #7).
STATUS_COMPLETE = 10
STATUS_TERMINATE = 20
STATUS_SECURITY = 30  # duplicate-pid screen-out (T10)
STATUS_OVERQUOTA = 40  # quota engine (T10)

# Where the extractor stashes a routing sentence it could not resolve into a
# structured condition (mirrors questionnaire.py's key).
_ROUTING_VERBATIM_KEY = "routing_verbatim"

# status_code -> the redirect-template column on FieldingLinkDB.
_REDIRECT_COL = {
    STATUS_COMPLETE: "redirect_complete",
    STATUS_TERMINATE: "redirect_terminate",
    STATUS_OVERQUOTA: "redirect_overquota",
    STATUS_SECURITY: "redirect_security",
}


def _apply_redirect_template(template: str | None, entry_params: dict) -> str | None:
    """Substitute `{param}` placeholders from entry_params (missing -> "").

    A missing param yields an empty string rather than a 500, so a vendor URL
    template that references a `{pid}` the panel didn't send still resolves.
    """
    if not template:
        return None
    return re.sub(
        r"\{(\w+)\}",
        lambda m: str((entry_params or {}).get(m.group(1), "")),
        template,
    )


def _redirect_url(link, session) -> str | None:
    """The substituted vendor redirect for this session's terminal status, or
    None when no template is configured for it."""
    col = _REDIRECT_COL.get(session.status_code)
    template = getattr(link, col, None) if col else None
    return _apply_redirect_template(template, session.entry_params or {})


def _with_redirect(payload: dict, link, session) -> dict:
    """Add `redirect_url` to a terminal response ONLY when a template resolves.

    Kept conditional so a link with no vendor redirect returns the exact same
    body shape as T9' did (the built-in screens keep working)."""
    url = _redirect_url(link, session)
    if url:
        payload["redirect_url"] = url
    return payload


def _matching_cells(cells, answers: dict, decidable_only: bool = False):
    """Quota cells whose condition matches `answers`.

    `decidable_only` keeps only cells whose every referenced qid is already
    answered — i.e. the match can't change with future answers — which is the
    correctness gate for the early (answer-time) overquota exit.
    """
    out = []
    for cell in cells:
        expr = Expression.from_dict(json.loads(cell.condition_json))
        if decidable_only and not expr.referenced_qids().issubset(answers.keys()):
            continue
        if evaluate(expr, answers):
            out.append(cell)
    return out


def _unenforced_routing(survey) -> list[str]:
    """Questions whose routing will NOT be applied to respondents.

    When extraction cannot resolve a routing sentence to a question and codes,
    it keeps the sentence verbatim so a human can act on it — but a sentence is
    not something the field engine can evaluate. Such a question is shown to
    EVERYONE, which is silent: the questionnaire looks routed, the data comes
    back wrong, and nothing in between says so.
    """
    out = []
    for question in survey.questions:
        verbatim = (question.validation or {}).get(_ROUTING_VERBATIM_KEY)
        if verbatim and question.show_if is None:
            out.append(question.qid)
    return out


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
        redirect_complete=link.redirect_complete,
        redirect_terminate=link.redirect_terminate,
        redirect_overquota=link.redirect_overquota,
        redirect_security=link.redirect_security,
        soft_launch_limit=link.soft_launch_limit,
        soft_launch_reached=(
            link.soft_launch_limit is not None
            and (counts or {}).get("completes", 0) >= link.soft_launch_limit
        ),
        is_test=bool(link.is_test),
        vendor_name=link.vendor_name,
        cpi=link.cpi,
        target_completes=link.target_completes,
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


def _preflight(db: Session, study_id: str) -> dict:
    """The whole instrument checked once more, as the respondent will meet it."""
    from api.routes.questionnaire import _BLOCKING_FLAGS
    from src.engines.fielding.preflight import preflight

    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")
    questionnaires = list_questionnaires_for_study(db, study_id)
    if not questionnaires:
        raise HTTPException(status_code=404, detail="This study has no questionnaire yet")
    approved = next((q for q in questionnaires if q.status == "approved"), None)
    current = approved or questionnaires[0]
    payload = _read_definition_json(db, current)
    if payload is None:
        raise HTTPException(status_code=409, detail="The questionnaire is still being read")
    survey = SurveyDefinition.from_json(payload)
    try:
        return run_heavy_detached(
            db, preflight, survey, approved is not None, _BLOCKING_FLAGS,
            set(current.approved_qids or []),
        )
    except BrokenProcessPool as exc:
        # The worker was killed — on a small box that means it ran out of
        # memory on a long questionnaire. An operational cause deserves an
        # operational sentence, not "Internal Server Error".
        logger.exception("[%s] the pre-link check worker died", study_id)
        raise HTTPException(
            status_code=503,
            detail="The pre-link check ran out of room on this server. Try again; "
                   "if it keeps happening the server needs more memory.",
        ) from exc
    except Exception as exc:
        # Anything else: name the KIND of failure so the next report is
        # actionable, and keep the detail in the log. A reviewer being told
        # only "could not run" has nowhere to go, and neither does the
        # engineer they bring it to.
        logger.exception("[%s] the pre-link check failed", study_id)
        raise HTTPException(
            status_code=500,
            detail=f"The pre-link check failed with {type(exc).__name__} on the server. "
                   f"The full reason is in the server log.",
        ) from exc


@router.get("/studies/{study_id}/fielding/preflight")
def fielding_preflight(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Re-check everything before a link is created. (Owner/Analyst)

    Review happens question by question over days; a link pins the instrument
    the second it is created. This reads the whole thing once more and says what
    would reach a respondent wrong — with the questions each problem is about.
    """
    return _preflight(db, study_id)


@router.post(
    "/studies/{study_id}/fielding/links",
    response_model=FieldingLinkResponse,
)
def create_link(
    study_id: str,
    body: FieldingLinkCreateRequest | None = None,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Open a fielding link for the study's approved questionnaire. (Owner/Analyst)

    The approved `SurveyDefinition` JSON is snapshotted onto the link so a later
    re-approval can't change a survey already in the field. Vendor redirect
    templates may be seeded here or set later via PATCH. An optional `theme` slug
    is stamped into THIS link's snapshot metadata only (a per-link presentation
    choice) — the entry endpoint reads it back from the snapshot, so it wins over
    whatever the questionnaire's own definition carried, with no further change."""
    questionnaire = _approved_questionnaire(db, study_id)
    payload = _read_definition_json(db, questionnaire)
    if payload is None:
        # Approved but no persisted definition is a server fault, not user error.
        raise HTTPException(status_code=500, detail="Approved questionnaire has no definition")
    survey = SurveyDefinition.from_json(payload)

    theme = body.theme if body else None
    if theme is not None:
        if theme not in THEME_SLUGS:
            raise HTTPException(status_code=422, detail=f"Unknown theme slug: {theme}")
        survey.metadata["theme"] = theme
        payload = survey.to_json()  # stamp into the pinned snapshot only

    is_test = bool(body.test) if body else False

    # Checked BEFORE the link exists. The old check ran after the insert, so a
    # refused link was refused and saved at the same time. A test link skips
    # this: it exists to try the script, including a broken one, on nobody real.
    check = {"ok": True, "checks": []}
    # A test link is still CHECKED — it is the scripter's and QA's first look at
    # the survey as a respondent meets it, and "Q2 has nothing to answer" is
    # exactly what they are there to find. It is just never refused.
    test_failures: list[dict] = []
    if is_test:
        test_failures = [c for c in _preflight(db, study_id)["checks"] if c["status"] == "fail"]
    else:
        check = _preflight(db, study_id)
        if not check["ok"] and not (body.force if body else False):
            failing = [c for c in check["checks"] if c["status"] == "fail"]
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "The pre-link check found problems a respondent would meet.",
                    "checks": failing,
                },
            )

    redirects = (
        body.model_dump(exclude_unset=True, exclude={"theme", "force", "test"})
        if body
        else None
    )
    link = create_fielding_link(
        db,
        study_id=study_id,
        questionnaire_id=questionnaire.id,
        survey_version=survey.version,
        survey_json=payload,
        created_by=user.email,
        redirects=redirects,
        # A test link never soft-launches — there's no real sample to protect.
        soft_launch_limit=(body.soft_launch_limit if body and not is_test else None),
        is_test=is_test,
        vendor=redirects,
    )
    # The quota plan written with the questionnaire becomes this link's cells.
    # A test link carries none: its respondents are not sample.
    plan = (survey.metadata or {}).get("quota_plan")
    if plan and not is_test:
        from src.engines.fielding.quota_plan import cells as plan_cells
        from src.engines.fielding.quota_plan import problems as plan_problems

        if not plan_problems(plan, survey):
            for cell in plan_cells(plan):
                create_quota_cell(db, link.id, cell["name"], json.dumps(cell["condition"]),
                                  cell["limit"])
    if not check["ok"]:
        # Opened over a failing check on purpose: say so where it can be found.
        logger.warning(
            f"[{study_id}] Link opened with force despite {check['n_fail']} failing "
            f"pre-link check(s) by {user.email}"
        )

    unenforced = _unenforced_routing(survey)
    if unenforced:
        # Not a refusal: a link is often opened deliberately for a soft launch.
        # But routing that exists only as prose is NOT applied in the field —
        # the question is shown to every respondent — and that is invisible
        # unless it is said out loud at the moment the link is created.
        logger.warning(
            f"[{study_id}] Fielding link {link.id} opened with {len(unenforced)} "
            f"question(s) whose routing is not enforced: {', '.join(unenforced[:10])}"
        )
    logger.info(
        f"[{study_id}] Fielding link {link.id} opened by {user.name} "
        f"(questionnaire {questionnaire.id} v{survey.version})"
    )
    display_issues = display_warnings(survey)
    if display_issues:
        logger.warning(
            f"[{study_id}] Fielding link {link.id} opened with {len(display_issues)} "
            "question(s) whose respondent-facing text lost an unresolvable directive: "
            + ", ".join(i["qid"] for i in display_issues[:10])
        )
    response = _link_response(link, fielding_link_counts(db, link.id))
    response.unenforced_routing = unenforced
    response.display_issues = display_issues
    response.preflight_failures = test_failures
    return response


@router.post("/studies/{study_id}/fielding/dry-run")
def dry_run(
    study_id: str,
    body: DryRunRequest | None = None,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Field the survey against synthetic respondents. (Owner/Analyst)

    The point is not to produce data — it is to find out what the script DOES
    to people before a panel is pointed at it. A screen-out that fires for
    nobody, a base that hides a question from everyone, a mask that never
    narrows anything, a quota that fills at respondent 108 of 500: each is
    invisible in the document and obvious in the run, and each costs real money
    once the link is live.

    Runs against the currently approved questionnaire, or against an existing
    link's PINNED survey and live quota counts when `link_id` is given — which
    is how you check what a link already in the field will do next.
    """
    request = body or DryRunRequest()
    if request.link_id:
        link = _owner_link(db, study_id, request.link_id)
        survey = SurveyDefinition.from_json(link.survey_json)
        cells = [
            QuotaCell(
                id=cell.id,
                condition=Expression.from_dict(json.loads(cell.condition_json)),
                limit=cell.cell_limit,
                count=cell.count,
            )
            for cell in list_quota_cells(db, link.id)
        ]
    else:
        questionnaire = _approved_questionnaire(db, study_id)
        payload = _read_definition_json(db, questionnaire)
        if payload is None:
            raise HTTPException(
                status_code=500, detail="Approved questionnaire has no definition"
            )
        survey = SurveyDefinition.from_json(payload)
        cells = []

    report = run_heavy_detached(db, simulate, survey, cells, n=request.n, seed=request.seed)
    logger.info(
        f"[{study_id}] Dry run by {user.name}: {report.n_respondents} respondents, "
        f"{report.dispositions}, {len(report.never_fired)} condition(s) never fired"
    )
    return {
        **report.to_dict(),
        "display_issues": display_warnings(survey),
        "unenforced_routing": _unenforced_routing(survey),
        "run_by": user.name,
    }


@router.get(
    "/studies/{study_id}/fielding/links",
    response_model=FieldingLinkListResponse,
)
def list_links(
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
        delivery_formats=_delivery_on_disk(study_id),
    )


def _delivery_on_disk(study_id: str) -> list[str]:
    """Which delivery files a previous materialize actually left behind.

    Asked of the filesystem rather than remembered, because the file is the
    only thing that matters: a flag saying the export ran, over a file the
    disk no longer holds, offers a download that 404s.
    """
    folder = STUDIES_DIR / study_id
    return [
        fmt
        for fmt in ("sav", "xlsx")
        if (folder / f"{study_id}_responses.{fmt}").is_file()
    ]


@router.post(
    "/studies/{study_id}/fielding/links/{link_id}/close",
    response_model=FieldingLinkResponse,
)
def close_link(
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


@router.patch(
    "/studies/{study_id}/fielding/links/{link_id}/vendor",
    response_model=FieldingLinkResponse,
)
def set_vendor(
    study_id: str,
    link_id: str,
    body: VendorRequest,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Set vendor name, CPI and target completes on a link. (Owner/Analyst)"""
    _owner_link(db, study_id, link_id)
    link = set_fielding_link_vendor(
        db, link_id, body.vendor_name, body.cpi, body.target_completes
    )
    return _link_response(link, fielding_link_counts(db, link.id))


@router.patch(
    "/studies/{study_id}/fielding/links/{link_id}/soft-launch",
    response_model=FieldingLinkResponse,
)
def set_soft_launch(
    study_id: str,
    link_id: str,
    body: SoftLaunchRequest,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Hold a link at N completes, or release it to full sample. (Owner/Analyst)

    One endpoint for both halves of the launch: `limit=50` holds, `limit=null`
    releases. Release is the moment a human takes responsibility for the data —
    run the soft-launch check first, because after this the money is spent.
    """
    _owner_link(db, study_id, link_id)
    if body.limit is not None and body.limit < 1:
        raise HTTPException(status_code=422, detail="Soft-launch limit must be at least 1")
    link = set_fielding_link_soft_launch(db, link_id, body.limit)
    logger.info(
        f"[{study_id}] Fielding link {link_id} soft launch "
        f"{'released' if body.limit is None else f'held at {body.limit}'} by {user.name}"
    )
    return _link_response(link, fielding_link_counts(db, link.id))


@router.post("/studies/{study_id}/fielding/links/{link_id}/populate")
def populate_link(
    study_id: str,
    link_id: str,
    body: PopulateRequest | None = None,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Fill a TEST link with `n` dry-run respondents. (Owner/Analyst)

    Walks the link's own pinned survey and live quota cells through `simulate`
    (the same engine the dry-run report uses) and writes the result as real
    session/answer rows — so the field monitor, field report and live-data
    views all show something the moment a test link exists, with no panel and
    no cost. Refused on a real link: this is not a shortcut for seeding fake
    completes into data anyone will analyze.
    """
    link = _owner_link(db, study_id, link_id)
    if not link.is_test:
        raise HTTPException(
            status_code=409,
            detail="Only a test link can be populated with dry-run respondents",
        )
    n = max(1, min(int(body.n if body else 200), 2000))

    survey = SurveyDefinition.from_json(link.survey_json)
    cells = [
        QuotaCell(
            id=cell.id,
            condition=Expression.from_dict(json.loads(cell.condition_json)),
            limit=cell.cell_limit,
            count=cell.count,
        )
        for cell in list_quota_cells(db, link.id)
    ]
    report = run_heavy_detached(db, simulate, survey, cells, n=n, collect=True)
    added = add_synthetic_sessions(db, link.id, report.respondents)
    logger.info(
        f"[{study_id}] Test link {link_id} populated with {added} synthetic "
        f"respondents by {user.name}: {report.dispositions}"
    )
    return {"added": added, "dispositions": report.dispositions}


# --- Quota management (owner/analyst) ---------------------------------------


def _owner_link(db: Session, study_id: str, link_id: str):
    """Resolve a link that belongs to `study_id`, or 404 (management side)."""
    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")
    link = get_fielding_link(db, link_id)
    if not link or link.study_id != study_id:
        raise HTTPException(status_code=404, detail="Fielding link not found")
    return link


def _quota_response(cell) -> QuotaCellResponse:
    return QuotaCellResponse(
        id=cell.id,
        fielding_link_id=cell.fielding_link_id,
        name=cell.name,
        condition=json.loads(cell.condition_json),
        limit=cell.cell_limit,
        count=cell.count,
    )


def _quotas_response(db: Session, link_id: str) -> QuotaCellsResponse:
    return QuotaCellsResponse(
        link_id=link_id,
        cells=[_quota_response(c) for c in list_quota_cells(db, link_id)],
    )


@router.post(
    "/studies/{study_id}/fielding/links/{link_id}/quotas",
    response_model=QuotaCellsResponse,
)
def create_quotas(
    study_id: str,
    link_id: str,
    body: QuotaCellsCreateRequest,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Add quota cells to a link. (Owner/Analyst)

    Each cell's `condition` is an `Expression.to_dict()` — the same AST as
    routing/terminates (key decision #4, one condition language everywhere).
    Returns the link's full cell list with live counts.
    """
    link = _owner_link(db, study_id, link_id)
    for cell in body.cells:
        create_quota_cell(db, link.id, cell.name, json.dumps(cell.condition), cell.limit)
    return _quotas_response(db, link.id)


@router.get(
    "/studies/{study_id}/fielding/links/{link_id}/quotas",
    response_model=QuotaCellsResponse,
)
def list_quotas(
    study_id: str,
    link_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """List a link's quota cells with live counts. (Authenticated)"""
    link = _owner_link(db, study_id, link_id)
    return _quotas_response(db, link.id)


@router.patch(
    "/studies/{study_id}/fielding/links/{link_id}/quotas/{quota_id}",
    response_model=QuotaCellResponse,
)
def update_quota(
    study_id: str,
    link_id: str,
    quota_id: str,
    body: QuotaCellUpdateRequest,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Patch a quota cell. (Owner/Analyst)

    Raising/lowering `limit` is always allowed (raising mid-field is routine MR
    practice). Changing `condition` is REJECTED (409) once ANY session exists on
    the link — a redefinition would invalidate completes already counted under
    the old definition.
    """
    link = _owner_link(db, study_id, link_id)
    cell = get_quota_cell(db, quota_id)
    if not cell or cell.fielding_link_id != link.id:
        raise HTTPException(status_code=404, detail="Quota cell not found")
    if body.condition is not None:
        if list_respondent_sessions(db, link.id):
            raise HTTPException(
                status_code=409,
                detail="Cannot change a quota condition after fielding has started",
            )
        cell = set_quota_cell_condition(db, quota_id, json.dumps(body.condition))
    if body.limit is not None:
        cell = update_quota_cell_limit(db, quota_id, body.limit)
    return _quota_response(cell)


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


def _check_stated_place(db: Session, session, survey: SurveyDefinition, answers: dict) -> None:
    """Does where they SAY they are agree with where the connection is?

    Recorded as a flag on the session and never acted on: a VPN, a work laptop
    and a neighbouring postcode are all ordinary, and silently dropping those
    interviews would be worse than the fraud it is meant to catch. Only checked
    while the answer is still "unknown", so it costs one comparison per session.
    """
    from src.engines.fielding.paradata import place_verdict, stated_place

    params = dict(session.entry_params or {})
    if params.get("place_check") in ("match", "mismatch"):
        return
    stated = stated_place(survey, answers)
    if not stated:
        return
    verdict, why = place_verdict(stated, params.get("geo") or {})
    if verdict == "unknown":
        return
    params["place_check"], params["place_check_detail"] = verdict, why
    session.entry_params = params
    flag_modified(session, "entry_params")
    db.commit()


def _paradata_at_entry(request: Request) -> dict:
    """Device and (where a location database exists) place, at the moment of entry.

    The address itself is not kept — a salted hash stands in for it, so two
    sessions from one connection can be recognised without storing where anyone
    lives. DVV_STORE_RAW_IP=1 keeps it in full for a client whose own rules
    require that.
    """
    from api.config import JWT_SECRET
    from src.engines.fielding.paradata import client_ip, device_of, hashed_ip, locate

    headers = {k.lower(): v for k, v in request.headers.items()}
    ip = client_ip(headers, getattr(request.client, "host", None))
    salt = os.getenv("DVV_IP_SALT") or JWT_SECRET
    found = {
        "device": device_of(headers.get("user-agent")),
        "ip_hash": hashed_ip(ip, salt),
        "geo": locate(ip),
    }
    if os.getenv("DVV_STORE_RAW_IP", "").strip() in ("1", "true", "yes"):
        found["ip"] = ip
    return {k: v for k, v in found.items() if v}


@router.get("/r/{token}", response_model=RespondentEntryResponse)
def enter_survey(token: str, request: Request, db: Session = Depends(get_db)):
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
    # What the interview was taken ON, and roughly where from. A grid that
    # fails on phones, or a respondent nowhere near the area the sample was
    # drawn on, are both invisible afterwards unless recorded now.
    entry_params.update(_paradata_at_entry(request))

    # Soft-launch hold: once the launch batch is complete, stop admitting NEW
    # respondents so a scripting error surfaces at n=50 instead of n=800 — the
    # whole point of a soft launch. Sessions already IN PROGRESS are deliberately
    # not killed (those are real people mid-interview), so completes can overshoot
    # the limit by the number in flight; that is correct, not a rounding bug.
    # A held link is `paused`, never `closed`: releasing it resumes the same link.
    # Arrivals are bounced on the over-quota redirect when one is set, which is
    # what a panel expects for "we can't use you right now" — and no session row
    # is written, so a hold never invents dispositions the field never had.
    limit = link.soft_launch_limit
    if limit is not None and count_completes(db, link.id) >= limit:
        return RespondentEntryResponse(
            session_id="",
            status="paused",
            surveyjs_json={},
            redirect_url=_apply_redirect_template(link.redirect_overquota, entry_params),
        )

    # Security screen-out (status 30): a panel `pid` that already has a terminal
    # session on this link is a duplicate/fraud signal — don't re-serve the
    # survey. Record the attempt with status 30 and hand back the terminal
    # payload (redirect_security substituted) instead of the questionnaire.
    pid = (entry_params.get("pid") or "").strip()
    if pid and pid_already_finished(db, link.id, pid):
        session = create_respondent_session(db, link.id, entry_params=entry_params)
        set_respondent_session_status(db, session.id, STATUS_SECURITY)
        session = get_respondent_session(db, session.id)
        return RespondentEntryResponse(
            session_id=session.id,
            status="open",
            surveyjs_json={},
            status_code=STATUS_SECURITY,
            redirect_url=_redirect_url(link, session),
        )

    survey = SurveyDefinition.from_json(link.survey_json)
    # ?lang=hi serves the Hindi translation when the link carries one; `lang`
    # is already in entry_params, so the data records which language was read.
    surveyjs_json = compile_to_surveyjs(translated(survey, entry_params.get("lang", "")))

    # Which block this respondent meets first, when the script asked for the
    # blocks to be rotated. Assigned to the arm that is BEHIND rather than by a
    # coin flip: on 500 respondents a fair coin lands 54/46 often enough to
    # bias the very brand comparison the rotation exists to protect.
    block_order = None
    available = rotation_arms(survey)
    if available:
        counts: dict[str, int] = {}
        for prior in list_respondent_sessions(db, link.id):
            arm = getattr(prior, "block_order", None)
            if arm:
                counts[arm] = counts.get(arm, 0) + 1
        block_order = assign_arm(counts, available)
        surveyjs_json = order_pages(surveyjs_json, block_order)

    # Which MaxDiff design version this respondent is dealt. Least fill again,
    # not a random draw: the design's balance is a property of the SAMPLE, and
    # a version that ends up with half the respondents of another undoes it.
    maxdiff_version = None
    exercises = maxdiff_questions(survey)
    if exercises:
        n_versions = max(
            (d.n_versions for q in exercises if (d := maxdiff_design(q))), default=0
        )
        if n_versions:
            seen: dict[str, int] = {}
            for prior in list_respondent_sessions(db, link.id):
                dealt = getattr(prior, "maxdiff_version", None)
                if dealt is not None:
                    seen[str(dealt)] = seen.get(str(dealt), 0) + 1
            maxdiff_version = int(
                assign_arm(seen, [str(v) for v in range(n_versions)])
            )
            surveyjs_json = apply_maxdiff_version(surveyjs_json, survey, maxdiff_version)

    # Bipolar grids whose document says half see the statements flipped: the
    # arm that is behind, recorded in the session like the device is.
    flipping = flipping_questions(survey)
    if flipping:
        dealt: dict[str, int] = {}
        for prior in list_respondent_sessions(db, link.id):
            arm = (getattr(prior, "entry_params", None) or {}).get("bipolar_flip")
            if arm:
                dealt[str(arm)] = dealt.get(str(arm), 0) + 1
        arm = assign_arm(dealt, FLIP_ARMS)
        entry_params = {**entry_params, "bipolar_flip": arm}
        if arm == "2":
            surveyjs_json = apply_flip(surveyjs_json, flipping)

    session = create_respondent_session(
        db, link.id, entry_params=entry_params, block_order=block_order,
        maxdiff_version=maxdiff_version,
    )
    return RespondentEntryResponse(
        session_id=session.id,
        status="open",
        surveyjs_json=surveyjs_json,
        current_page=session.current_page or 0,
        theme=survey.metadata.get("theme"),
        is_test=bool(link.is_test),
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


def _exclusive_only(survey: SurveyDefinition, qid: str, value):
    """Drop co-selections when an EXCLUSIVE option ("None of the above") is ticked.

    Enforced here, not in the browser, for the same reason terminates are: the
    client is the respondent's, and SurveyJS has no per-choice exclusivity to
    configure anyway. A multi-punch carrying both "Ulta" and "None of the above"
    is contradictory data that would otherwise land in the tabs.
    """
    if not isinstance(value, list) or len(value) < 2:
        return value
    question = next((q for q in survey.questions if q.qid == qid), None)
    if question is None:
        return value
    exclusive = [o.code for o in question.options if o.exclusive]
    picked = [code for code in exclusive if code in value]
    return [picked[-1]] if picked else value


def _over_cap(survey: SurveyDefinition, qid: str, value) -> str | None:
    """The message for a page that ticks more options than the question allows.

    Not trimmed to fit, the way a co-selected EXCLUSIVE option is. Exclusivity
    has one safe resolution — the exclusive choice wins — and a cap has none:
    keeping the first two of four picks an answer the respondent did not give,
    and it would land in the tabs indistinguishable from one they did.

    A contradictory cap is ignored here on purpose. A minimum above the maximum
    rejects every possible answer, and a respondent stuck behind one can never
    finish; preflight fails the link over it instead, which is the place where
    a person can still fix it.
    """
    if not isinstance(value, list):
        return None
    question = next((q for q in survey.questions if q.qid == qid), None)
    if question is None:
        return None
    limits = question.validation or {}
    ceiling, floor = limits.get("max_select"), limits.get("min_select")
    if not isinstance(ceiling, int) or ceiling <= 0:
        return None
    if isinstance(floor, int) and floor > ceiling:
        return None
    if len(value) > ceiling:
        return f"{qid} allows at most {ceiling} answer{'s' if ceiling > 1 else ''}."
    return None


def _maxdiff_refusals(db, survey, session, answers: dict) -> list[str]:
    """Best/worst answers that this respondent's own screens cannot justify.

    Two things are checked, and neither is reachable through the survey we
    served: an item that was not on that screen, and the same item picked as
    both best and worst.
    """
    exercises = {q.qid: q for q in maxdiff_questions(survey)}
    if not exercises:
        return []
    version = getattr(session, "maxdiff_version", None)
    if version is None:
        return []

    problems: list[str] = []
    for qid, question in exercises.items():
        design = maxdiff_design(question)
        if design is None:
            continue
        for screen in range(design.n_screens):
            best_field, worst_field = maxdiff_fields(qid, screen)
            best, worst = answers.get(best_field), answers.get(worst_field)
            if best is None and worst is None:
                continue
            shown = maxdiff_items_shown(question, version, screen)
            for field, value in ((best_field, best), (worst_field, worst)):
                if value is not None and value not in shown:
                    problems.append(f"{field}: that item was not on this screen.")
            if best is not None and best == worst:
                problems.append(
                    f"{best_field}: the same item cannot be both best and worst."
                )
    return problems


@router.post("/r/{token}/sessions/{session_id}/answers")
def submit_answers(
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

    # Server-authoritative view of what the respondent was actually asked.
    survey = SurveyDefinition.from_json(link.survey_json)

    refused = _maxdiff_refusals(db, survey, session, body.answers or {})
    if refused:
        # Nothing on the page is stored. A best/worst answer naming an item the
        # respondent was never shown cannot be a mistake they made — the browser
        # only ever offered them four — so it is a client we did not serve, and
        # a utility model fed one of these would rank an item on evidence that
        # does not exist.
        raise HTTPException(status_code=400, detail=" ".join(refused))

    over = [
        message
        for qid, value in (body.answers or {}).items()
        if (message := _over_cap(survey, qid, value))
    ]
    if over:
        # Nothing on this page is stored. A browser running the compiled survey
        # cannot get here — SurveyJS holds the cap — so this is a client that
        # is not the one we served, and accepting part of its page would leave
        # the session half-answered.
        raise HTTPException(status_code=400, detail=" ".join(over))

    so_far = {**get_session_answers_map(db, session_id), **(body.answers or {})}
    for qid, value in (body.answers or {}).items():
        hidden = masked_out(survey, qid, value, so_far)
        if hidden:
            # An option the mask hid cannot have been ticked in the survey we
            # served. Dropped, not refused: if this check and the browser ever
            # disagree, a real respondent must not be stuck on the page.
            logger.warning(f"[{link.study_id}] {session_id} {qid}: dropped masked-out {hidden}")
            value = ([v for v in value if v not in hidden] if isinstance(value, list) else None)
            if value in (None, []):
                continue
        upsert_respondent_answer(db, session_id, qid, _exclusive_only(survey, qid, value))
    touch_respondent_session(db, session_id, current_page=body.current_page)

    # Hidden variables an approved rule computes join the answers, so a
    # screen-out or quota written on one ("D2a = 4") is judged like any other.
    answers = with_derived(survey, get_session_answers_map(db, session_id))
    _check_stated_place(db, session, survey, answers)
    for question in survey.questions:
        if question.terminate_if is None:
            continue
        # Only judge a screen-out once every question it references has been
        # ANSWERED. Without this a negated terminate ("TERMINATE if 1 not
        # selected", stored as not(in ...)) fires on a respondent who has
        # answered nothing at all: the inner `in` is false against an empty
        # answer set, and `not false` is true. Every respondent was screened
        # out on their first submit. The quota check below already uses this
        # rule (`decidable_only`); the terminate check must too.
        if not question.terminate_if.referenced_qids().issubset(answers.keys()):
            continue
        if evaluate(question.terminate_if, answers):
            # A page can carry answers to questions AFTER the one that screens
            # the respondent out — they were all submitted together, before the
            # server got to judge any of them. Those answers are not data this
            # study is entitled to: the interview stopped at `question`. Keeping
            # them would put screened-out people into every downstream base and
            # would make our own screen-out check flag our own data forever.
            order = survey.question_order()
            after = set(order[order.index(question.qid) + 1 :]) if question.qid in order else set()
            dropped = delete_respondent_answers(db, session_id, sorted(after & answers.keys()))
            set_respondent_session_status(db, session_id, STATUS_TERMINATE)
            session = get_respondent_session(db, session_id)
            logger.info(
                f"[{link.study_id}] Session {session_id} terminated by "
                f"{question.qid}.terminate_if"
                + (f" (dropped {dropped} post-screen-out answer(s))" if dropped else "")
            )
            _emit_event(db, link.study_id, "respondent.terminate", session_id)
            return _with_redirect(
                {"terminated": True, "status_code": STATUS_TERMINATE}, link, session
            )

    # Early (answer-time) overquota: screen out a doomed respondent NOW instead
    # of making them answer the rest. Only cells that are already DECIDABLE from
    # the answers so far are considered (`decidable_only`) — a cell whose match
    # could still flip with a future answer must not fire early. No increment
    # happens here; this is a fast-fail, the atomic slot claim is at complete.
    for cell in _matching_cells(list_quota_cells(db, link.id), answers, decidable_only=True):
        if cell.count >= cell.cell_limit:
            set_respondent_session_status(db, session_id, STATUS_OVERQUOTA)
            session = get_respondent_session(db, session_id)
            logger.info(
                f"[{link.study_id}] Session {session_id} overquota (early) on "
                f"cell {cell.id}"
            )
            return _with_redirect(
                {"terminated": True, "status_code": STATUS_OVERQUOTA}, link, session
            )

    return {"ok": True, "terminated": False}


class ProbeRequest(BaseModel):
    qid: str = Field(max_length=200)
    answer: str = Field(max_length=2000)


@router.post("/r/{token}/sessions/{session_id}/probe")
def follow_up(token: str, session_id: str, body: ProbeRequest, db: Session = Depends(get_db)):
    """The follow-up for a thin answer to a question marked for probing, or null.

    Written once per respondent and question, then recorded (`<qid>_PROBEQ`);
    asking again returns the record, so the public link cannot run the model at will.
    """
    from src.engines.fielding.probes import ASKED, is_thin, probed, write_probe

    link = _resolve_link(db, token)
    session = _resolve_session(db, link, session_id)
    if session.status_code is not None:
        raise HTTPException(status_code=409, detail="This interview has finished")
    survey = SurveyDefinition.from_json(link.survey_json)
    question = survey[body.qid] if body.qid in survey else None
    if question is None or not probed(question):
        raise HTTPException(status_code=404, detail="No follow-up for this question")
    if not is_thin(body.answer):
        return {"probe": None}
    asked = get_session_answers_map(db, session_id).get(f"{body.qid}{ASKED}")
    if asked:
        return {"probe": asked}
    text, source = write_probe(question.text or "", body.answer)
    upsert_respondent_answer(db, session_id, f"{body.qid}{ASKED}", text)
    logger.info(f"[{link.study_id}] follow-up for {body.qid} ({source})")
    return {"probe": text}


@router.post("/r/{token}/sessions/{session_id}/complete")
def complete_survey(
    token: str,
    session_id: str,
    db: Session = Depends(get_db),
):
    """Mark the session complete (status 10) with atomic quota enforcement.

    Idempotent + terminal-safe: `set_respondent_session_status` is write-once, so
    a re-complete returns the same status without re-running enforcement (no
    double-increment), and a session already terminated (20) is not flipped.

    Quota enforcement: every cell whose condition matches the session's answers
    must have a free slot. Each slot is claimed with an atomic conditional
    UPDATE (`count = count + 1 WHERE count < cell_limit`) — see
    `increment_quota_cell_if_under_limit`. Multi-cell no-partial-increment
    policy: we claim slots one at a time and, if ANY matching cell is already
    full, we COMPENSATE by decrementing the slots already claimed and record
    status 40 — a respondent never consumes some of their target cells but not
    all. (Compensate, rather than pre-check-then-claim, because a SELECT before
    the UPDATE wouldn't be atomic under concurrency; the UPDATE+rowcount is.)
    """
    link = _resolve_link(db, token)
    session = _resolve_session(db, link, session_id)

    if session.status_code is not None:  # already terminal — idempotent
        return _with_redirect(
            {
                "status_code": session.status_code,
                "complete": session.status_code == STATUS_COMPLETE,
            },
            link,
            session,
        )

    answers = with_derived(SurveyDefinition.from_json(link.survey_json),
                           get_session_answers_map(db, session_id))
    matching = _matching_cells(list_quota_cells(db, link.id), answers)

    claimed: list[str] = []
    for cell in matching:
        if increment_quota_cell_if_under_limit(db, cell.id):
            claimed.append(cell.id)
        else:  # a matching cell is full — roll back and screen out overquota
            for cid in claimed:
                decrement_quota_cell(db, cid)
            set_respondent_session_status(db, session_id, STATUS_OVERQUOTA)
            session = get_respondent_session(db, session_id)
            return _with_redirect(
                {"status_code": STATUS_OVERQUOTA, "complete": False}, link, session
            )

    set_respondent_session_status(db, session_id, STATUS_COMPLETE)
    session = get_respondent_session(db, session_id)
    _emit_event(db, link.study_id, "respondent.complete", session_id)
    return _with_redirect(
        {"status_code": STATUS_COMPLETE, "complete": True}, link, session
    )


@router.get(
    "/r/{token}/sessions/{session_id}",
    response_model=RespondentSessionResponse,
)
def resume_session(
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
        redirect_url=_redirect_url(link, session),
    )


@router.post("/r/{token}/sessions/{session_id}/jump")
def jump_to_question(
    token: str,
    session_id: str,
    body: JumpRequest,
    db: Session = Depends(get_db),
):
    """Jump a TEST session straight to `qid`. Public, like the other /r/ routes —
    a tester has no account either — but 403 on any real link: this is a way to
    skip past a screener, and a real respondent must never be able to.

    Finds one path of answers that reaches `qid` the way a qualifying
    respondent would (`path_to`), keeping the tester's own answers where they
    still allow it. Replaces the session's answers to every question BEFORE the
    target with that path, then hands them back so the client can preload the
    model and land on `qid`.
    """
    link = _resolve_link(db, token)
    if not link.is_test:
        raise HTTPException(status_code=403, detail="Jump is only available on a test link")
    _resolve_session(db, link, session_id)  # 404 if unknown or belongs elsewhere

    survey = SurveyDefinition.from_json(link.survey_json)
    current = get_session_answers_map(db, session_id)
    result = path_to(survey, body.qid, given=current)
    if result is None:
        raise HTTPException(
            status_code=422,
            detail=f"No path of answers reaches {body.qid} — its base may never be met",
        )
    answers, kept_given = result

    order = survey.question_order()
    before = set(order[: order.index(body.qid)]) if body.qid in order else set()
    stale = [qid for qid in before if qid in current and qid not in answers]
    delete_respondent_answers(db, session_id, stale)
    for qid, value in answers.items():
        upsert_respondent_answer(db, session_id, qid, value)

    return {"target": body.qid, "answers": answers, "kept_your_answers": kept_given}

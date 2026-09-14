"""The copilot endpoint: the platform's tools, executed as the signed-in user.

READ tools run here. ACTION tools only build a proposal — see src/ai/copilot.py
for why. A proposal's title and detail are written from database rows this
module has validated (the study exists, the link belongs to it, the link is
open), never from the model's own words, so a card can't be talked into
describing something other than what Confirm will do.
"""

import json
import logging
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.auth import UserRole, get_current_user
from api.db import (
    UserDB,
    answers_by_session,
    fielding_link_counts,
    get_db,
    get_fielding_link,
    get_study,
    list_fielding_links_for_study,
    list_questionnaires_for_study,
    list_quota_cells,
    list_respondent_sessions,
    list_studies,
)
from api.heavy import plain_sessions, run_heavy
from api.models import ChatRequest, CopilotProposal, CopilotRequest, CopilotResponse
from src.ai.copilot import run_copilot
from src.ai.router import BackendUnavailable
from src.core.survey_definition import Expression
from src.engines.fielding.decisions import QuotaCell
from src.engines.fielding.dryrun import simulate
from src.engines.fielding.field_report import build_field_report
from src.engines.fielding.launch_check import launch_check
from src.engines.questionnaire.navigator_tools import (
    _describe_expression,
    explain_question_base,
    get_question_details,
    search_questionnaire,
)

logger = logging.getLogger(__name__)
router = APIRouter()

_CAN_ACT = {UserRole.OWNER, UserRole.ANALYST, UserRole.FOUNDER}
_MAX_FINDINGS = 12  # keep each tool result inside the model's context budget


class _ToolError(Exception):
    """A tool can't run for a reason worth telling the model (and the user)."""


def _cells(db: Session, link_id: str) -> list[QuotaCell]:
    return [
        QuotaCell(
            id=c.id,
            condition=Expression.from_dict(json.loads(c.condition_json)),
            limit=c.cell_limit,
            count=c.count,
        )
        for c in list_quota_cells(db, link_id)
    ]


def _screen_outs(survey, by_question: list[dict]) -> list[dict]:
    """Each screen-out with the rule that fired, in words, with option labels —
    so the model reports the reason instead of guessing one."""
    by_qid = {q.qid: q for q in survey.questions}
    out = []
    for row in by_question:
        q = by_qid.get(row["qid"])
        rule = None
        if q is not None and q.terminate_if is not None:
            rule = _describe_expression(q.terminate_if)
            labels = {o.code: o.label for o in q.options}
            if q.terminate_if.value in labels:
                rule += f" ({labels[q.terminate_if.value]})"
        out.append({**row, "question": q.text if q else None, "screen_out_rule": rule})
    return out


def make_executor(db: Session, user: UserDB):
    """(execute, proposals): execute(name, args) runs a tool; proposals collects cards."""
    from api.routes.chat import _load_approved_survey, chat_with_study

    proposals: list[CopilotProposal] = []
    can_act = UserRole(user.role) in _CAN_ACT
    checked_links: set[str] = set()  # soft_launch_check ran on these, this request

    def study(args: dict):
        found = get_study(db, str(args.get("study_id") or ""))
        if not found:
            raise _ToolError(f"no project with id {args.get('study_id')!r} — use list_studies")
        return found

    def link(args: dict):
        s = study(args)
        found = get_fielding_link(db, str(args.get("link_id") or ""))
        if not found or found.study_id != s.id:
            raise _ToolError(f"no fielding link {args.get('link_id')!r} in project {s.id}")
        return s, found

    def survey(args: dict):
        s = study(args)
        approved = _load_approved_survey(s.id, db)
        if approved is None:
            raise _ToolError(f"project {s.id} has no approved questionnaire yet")
        return approved

    def propose(kind: str, title: str, detail: str, method: str, path: str,
                body: dict | None = None) -> dict:
        if not can_act:
            raise _ToolError(f"a {user.role} can't do this; an owner or analyst can")
        card = CopilotProposal(id=uuid4().hex[:8], kind=kind, title=title, detail=detail,
                               method=method, path=path, body=body or {})
        proposals.append(card)
        return {"proposed": True, "card": title,
                "note": "Not done yet: the user must press Confirm on the card."}

    # --- read tools -----------------------------------------------------------

    def list_studies_tool(_):
        rows, _total = list_studies(db, limit=50)
        projects = []
        for r in rows:  # stage FACTS, so the model never infers a stage from a label
            qs = list_questionnaire_for(r.id)
            links = list_fielding_links_for_study(db, r.id)
            counts = [fielding_link_counts(db, ln.id) for ln in links]
            projects.append({
                "study_id": r.id, "client": r.client_name,
                "questionnaire": qs[0].status if qs else "none",
                "open_links": sum(1 for ln in links if ln.status == "open"),
                "held_links": sum(1 for ln in links if ln.soft_launch_limit is not None),
                "completes": sum(c["completes"] for c in counts),
                "qc_has_run": bool(r.crosscheck_json), "has_tables": bool(r.excel_path)})
        return {"projects": projects}

    def list_questionnaire_for(study_id: str):
        return list_questionnaires_for_study(db, study_id)

    def study_overview(args):
        s = study(args)
        qs = list_questionnaires_for_study(db, s.id)
        links = list_fielding_links_for_study(db, s.id)
        return {
            "study_id": s.id, "client": s.client_name, "status": s.status,
            "questionnaire": qs[0].status if qs else None,
            "qc_has_run": bool(s.crosscheck_json),
            "fielding_links": [
                {"link_id": ln.id, "status": ln.status,
                 "soft_launch_limit": ln.soft_launch_limit,
                 "counts": fielding_link_counts(db, ln.id)} for ln in links],
        }

    def qc_findings(args):
        s = study(args)
        if not s.crosscheck_json:
            return {"ran": False, "note": "QC has not been run on this project's data yet."}
        report = json.loads(s.crosscheck_json)
        findings = report.get("findings", [])
        return {
            "ran": True, "passed": report.get("passed"), "rows_checked": report.get("n_rows"),
            "checked": report.get("checked"), "could_not_check": report.get("skipped"),
            "n_findings": len(findings),
            "findings": [{k: f.get(k) for k in ("qid", "kind", "severity", "detail", "n_rows")}
                         for f in findings[:_MAX_FINDINGS]],
        }

    def field_report(args):
        _s, ln = link(args)
        sessions = list_respondent_sessions(db, ln.id)
        from src.core.survey_definition import SurveyDefinition
        pinned = SurveyDefinition.from_json(ln.survey_json)
        report = run_heavy(build_field_report, pinned, plain_sessions(sessions),
                           answers_by_session(db, ln.id), _cells(db, ln.id)).to_dict()
        report["terminates_by_question"] = _screen_outs(pinned, report["terminates_by_question"])
        return report

    def soft_launch_check(args):
        _s, ln = link(args)
        from src.core.survey_definition import SurveyDefinition
        pinned = SurveyDefinition.from_json(ln.survey_json)
        result = run_heavy(launch_check, pinned,
                           plain_sessions(list_respondent_sessions(db, ln.id)),
                           answers_by_session(db, ln.id), _cells(db, ln.id))
        checked_links.add(ln.id)
        cc = result["crosscheck"]
        cc["findings"] = cc["findings"][:_MAX_FINDINGS]
        return {"rows_checked": result["n_checked_rows"], "crosscheck": cc,
                "incidence_pct": result["field_report"].get("incidence_pct"),
                "screened_out_at": _screen_outs(
                    pinned, result["field_report"].get("terminates_by_question", []))}

    def dry_run(args):
        n = max(50, min(int(args.get("n") or 500), 5000))
        report = run_heavy(simulate, survey(args), [], n=n, seed=20260915).to_dict()
        return {k: report[k] for k in ("n_respondents", "passed", "dispositions",
                                       "never_fired", "impossible", "questions_never_shown")}

    def ask_data(args):
        s = study(args)
        try:
            answer = chat_with_study(s.id, ChatRequest(message=str(args.get("question", ""))),
                                     db=db, user=user)
        except HTTPException as exc:
            raise _ToolError(str(exc.detail)) from exc
        return {"answer": answer.message.content}

    # --- action tools: proposals only -----------------------------------------

    def propose_open_link(args):
        s = study(args)
        survey(args)  # refuses when nothing is approved
        limit = max(1, min(int(args.get("soft_launch_limit") or 50), 100_000))
        return propose(
            "open_link", f"Open a fielding link for {s.client_name}",
            f"Serves the approved questionnaire, held at {limit} completes so the data can "
            "be checked before the rest of the sample is fielded.",
            "POST", f"/studies/{s.id}/fielding/links", {"soft_launch_limit": limit})

    def propose_set_hold(args):
        s, ln = link(args)
        if ln.status != "open":
            raise _ToolError(f"link {ln.id} is {ln.status}; only an open link can be held")
        limit = int(args.get("limit") or 0)
        done = fielding_link_counts(db, ln.id)["completes"]
        if limit <= 0:
            # Release spends the rest of the sample. Refuse to even prepare it
            # until the launch batch has been checked in this conversation turn.
            if ln.id not in checked_links:
                raise _ToolError(
                    f"run soft_launch_check on {ln.id} first and tell the user what it "
                    "found — releasing spends the rest of the sample")
            return propose(
                "release", f"Release link {ln.id} to full sample",
                f"{done} completes so far. The link starts admitting respondents again "
                "with no hold. After this, the rest of the sample gets spent.",
                "PATCH", f"/studies/{s.id}/fielding/links/{ln.id}/soft-launch", {"limit": None})
        return propose(
            "hold", f"Hold link {ln.id} at {limit} completes",
            f"{done} completes so far. New respondents pause at {limit}; anyone already "
            "inside the survey finishes.",
            "PATCH", f"/studies/{s.id}/fielding/links/{ln.id}/soft-launch", {"limit": limit})

    def propose_close_link(args):
        s, ln = link(args)
        if ln.status != "open":
            raise _ToolError(f"link {ln.id} is already {ln.status}")
        return propose(
            "close", f"Close link {ln.id}",
            "Turns away new respondents for good. People mid-interview can still finish.",
            "POST", f"/studies/{s.id}/fielding/links/{ln.id}/close")

    def propose_materialize(args):
        s, ln = link(args)
        done = fielding_link_counts(db, ln.id)["completes"]
        if done == 0:
            raise _ToolError(f"link {ln.id} has no completes to load yet")
        return propose(
            "materialize", f"Load {done} completes from link {ln.id} into the tables",
            "Rebuilds the project's tables and the SPSS / Excel delivery files from "
            "everything captured so far.",
            "POST", f"/studies/{s.id}/fielding/links/{ln.id}/materialize")

    tools = {
        "list_studies": list_studies_tool,
        "study_overview": study_overview,
        "find_question": lambda a: search_questionnaire(survey(a), a["study_id"],
                                                        str(a.get("query", ""))),
        "question_details": lambda a: get_question_details(survey(a), a["study_id"],
                                                           str(a.get("qid", ""))),
        "who_was_asked": lambda a: explain_question_base(survey(a), a["study_id"],
                                                         str(a.get("qid", ""))),
        "qc_findings": qc_findings,
        "field_report": field_report,
        "soft_launch_check": soft_launch_check,
        "dry_run": dry_run,
        "ask_data": ask_data,
        "propose_open_link": propose_open_link,
        "propose_set_hold": propose_set_hold,
        "propose_close_link": propose_close_link,
        "propose_materialize": propose_materialize,
    }

    def execute(name: str, args: dict) -> dict:
        tool = tools.get(name)
        if tool is None:
            return {"success": False, "error": f"unknown tool {name}"}
        try:
            return {"success": True, **tool(args or {})}
        except _ToolError as exc:
            return {"success": False, "error": str(exc)}
        except Exception:  # noqa: BLE001 - the model gets a sentence, the log gets the trace
            logger.exception(f"copilot tool {name} failed")
            return {"success": False, "error": f"{name} failed on the server"}

    return execute, proposals


@router.post("/copilot", response_model=CopilotResponse)
def copilot(
    body: CopilotRequest,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """Ask the copilot anything about the platform. It may prepare actions,
    never perform them."""
    execute, proposals = make_executor(db, user)
    page = ""
    if body.study_id and (current := get_study(db, body.study_id)):
        page = f"The user is looking at project {current.id} ({current.client_name})."
    try:
        reply, results, answered_by = run_copilot(
            body.message, [t.model_dump() for t in body.history], execute,
            user=user.name or user.email, role=user.role, page=page)
    except BackendUnavailable as exc:
        logger.warning(f"copilot: no backend ({exc})")
        raise HTTPException(status_code=503,
                            detail="The copilot can't reach an AI model right now.") from exc
    # A provider that fails mid-answer is retried on the next one from the top,
    # which re-prepares the same cards: keep one card per distinct action.
    unique = list({(c.method, c.path, json.dumps(c.body, sort_keys=True)): c
                   for c in proposals}.values())
    return CopilotResponse(reply=reply, proposals=unique,
                           tools_used=[r.get("tool", "") for r in results],
                           answered_by=answered_by)

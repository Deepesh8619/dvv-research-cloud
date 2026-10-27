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
    delete_copilot_conversation,
    fielding_link_counts,
    get_copilot_conversation,
    get_db,
    get_fielding_link,
    get_study,
    list_copilot_conversations,
    list_copilot_messages,
    list_fielding_links_for_study,
    list_questionnaires_for_study,
    list_quota_cells,
    list_respondent_sessions,
    list_studies,
    save_copilot_exchange,
)
from api.heavy import plain_sessions, run_heavy_detached
from api.models import (
    ChatRequest,
    CopilotConversation,
    CopilotConversationSummary,
    CopilotProposal,
    CopilotRequest,
    CopilotResponse,
    CopilotStoredMessage,
)
from src.ai.copilot import run_copilot
from src.ai.router import BackendUnavailable
from src.core.survey_definition import (
    TEMPLATE_SLUGS,
    THEME_SLUGS,
    Expression,
    QuestionType,
    SurveyDefinition,
)
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
from src.engines.rag.store import VERBATIM, ensure_verbatim_index
from src.engines.rag.store import search as rag_search

logger = logging.getLogger(__name__)
router = APIRouter()

_CAN_ACT = {UserRole.OWNER, UserRole.ANALYST, UserRole.FOUNDER}
_MAX_FINDINGS = 12  # keep each tool result inside the model's context budget
_MAX_VERBATIMS = 8  # enough to show a pattern, small enough to read in a side panel
_HISTORY_TURNS = 12  # earlier messages sent back to the model with a new question


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
    from api.routes.materialize import data_access

    proposals: list[CopilotProposal] = []
    can_act = UserRole(user.role) in _CAN_ACT
    checked_links: set[str] = set()  # soft_launch_check ran on these, this request

    def study(args: dict):
        found = get_study(db, str(args.get("study_id") or ""))
        if not found:
            # The real ids, so the model can correct itself (or say the project
            # does not exist) in this reply instead of guessing again.
            rows, _total = list_studies(db, limit=30)
            known = "; ".join(f"{r.id} ({r.client_name})" for r in rows) or "none"
            raise _ToolError(f"no project with id {args.get('study_id')!r}. "
                             f"The projects are: {known}")
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

    def extraction_diagnosis(args):
        """Why a project holds fewer questions than its document has.

        Deliberately does NOT require an approved questionnaire: a study is
        broken here long before anybody approves it, and "no approved
        questionnaire yet" is the least useful thing to say to somebody looking
        at one question where their document has eighty.
        """
        from api.routes.questionnaire import diagnose_extraction

        s = study(args)
        return diagnose_extraction(s.id, db=db, user=user)

    def propose_reextract(args):
        s = study(args)
        report = extraction_diagnosis(args)
        if not report.get("can_reextract"):
            raise _ToolError(
                "this project's document was not archived, so it has to be uploaded again"
            )
        found = report.get("questions_the_parser_finds_now")
        stored = report.get("questions_stored")
        return propose(
            "reextract",
            f"Read {s.client_name or s.id}'s questionnaire document again",
            "Extracts the uploaded document with today's parsers"
            + (f", which read {found} questions where this version has {stored}"
               if isinstance(found, int) and isinstance(stored, int) else "")
            + ". Saved as a new version — the current one and its approvals are kept.",
            "POST", f"/studies/{s.id}/questionnaire/reextract")

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

    def study_brief(args):
        """The study overview the questionnaire opened with, if it had one.

        Read deterministically at extraction from the document's own wording, so
        the copilot answers "what is the sample size?" from the brief rather
        than from anything it composed. Many questionnaires have no overview;
        that is normal and says nothing about the study.
        """
        definition = survey(args)
        brief = (definition.metadata or {}).get("study_overview")
        understood = (definition.metadata or {}).get("study_understanding")
        if not brief and not understood and not (definition.metadata or {}).get("qa"):
            return {
                "has_brief": False,
                "note": "This questionnaire has no study overview section — it opens "
                        "at the first question. Nothing is wrong with it.",
            }
        brief = brief or {}
        return {
            "has_brief": True,
            # What an analyst understood from the instructions before reading any
            # question: purpose, groups, rules and the questions they govern —
            # every item quoting the instruction line it rests on.
            "understanding": understood,
            # The QA loop's record — the same one the review page shows.
            "qa": (definition.metadata or {}).get("qa"),
            "markets": brief.get("markets"),
            "sample_size": brief.get("sample_size"),
            "loi_minutes": brief.get("loi_minutes"),
            "screening_requirements": brief.get("screening_requirements") or [],
            # What the study is asking ABOUT, and the terms it defined up front.
            # A respondent question ("does this include X?") is usually answered
            # by one of these, and they are the document's own words.
            "scope": brief.get("scope_statements") or [],
            "definitions": brief.get("definitions") or [],
            "quotas": brief.get("quotas") or [],
            "note": "Quota groups are as written in the document. They are NOT the "
                    "quota cells this link enforces — a cell needs a condition "
                    "against a real question, which a person has to confirm.",
        }

    def find_question(args):
        """Search the questionnaire by wording AND by meaning.

        The substring search is exact and cheap: it finds "price" in a question
        that says price. It cannot find "how much would you be willing to pay"
        for "price sensitivity", which is how a client actually asks. The
        project's indexed vectors answer that; the two lists are kept apart so
        the model can tell an exact hit from a near one.
        """
        definition = survey(args)
        study_id, query = args["study_id"], str(args.get("query", ""))
        result = search_questionnaire(definition, study_id, query)
        try:
            found = rag_search(db, study_id, query, survey=definition)
        except Exception:  # an index problem must never break the exact search
            logger.exception("[%s] semantic search failed", study_id)
            return result
        result["related_by_meaning"] = [
            {"ref": m["ref"], "text": m["text"][:300], "score": m["score"]}
            for m in found["matches"]
        ]
        if found.get("note"):
            result["related_by_meaning_note"] = found["note"]
        return result

    def search_verbatims(args):
        """What respondents typed, searched by meaning.

        Behind the same door as every other route respondent answers leave by:
        owners and founders from approval, everyone else once a soft launch
        reaches its hold or a quota cell fills. A search that returned people's
        words to someone who may not download them would be that door standing
        open.
        """
        definition = survey(args)
        study_id, query = args["study_id"], str(args.get("query", ""))
        decision = data_access(db, study_id, user)
        if not decision.allowed:
            raise _ToolError(decision.reason)

        state = ensure_verbatim_index(db, study_id, definition)
        found = rag_search(db, study_id, query, survey=definition,
                           kinds=(VERBATIM,), k=_MAX_VERBATIMS)
        return {
            "answers": [
                {"qid": m["ref"].split(":")[0], "said": m["text"], "score": m["score"]}
                for m in found["matches"]
            ],
            "searched_by": found["model"],
            "newly_indexed": state.get("indexed", 0),
            # Said plainly: a partial index is not the same as nothing found.
            "not_yet_indexed": state.get("remaining", 0),
            "note": state.get("note") or found.get("note"),
        }

    def field_report(args):
        _s, ln = link(args)
        sessions = list_respondent_sessions(db, ln.id)
        pinned = SurveyDefinition.from_json(ln.survey_json)
        answers, cells = answers_by_session(db, ln.id), _cells(db, ln.id)
        report = run_heavy_detached(db, build_field_report, pinned, plain_sessions(sessions),
                                    answers, cells).to_dict()
        report["terminates_by_question"] = _screen_outs(pinned, report["terminates_by_question"])
        # The per-question cards are for the report page. Handed to the model
        # they are tokens it does not need for a field question — and past the
        # tool-result cap they arrived as truncated JSON it could not read.
        report.pop("questions", None)
        return report

    def soft_launch_check(args):
        _s, ln = link(args)
        pinned = SurveyDefinition.from_json(ln.survey_json)
        sessions = plain_sessions(list_respondent_sessions(db, ln.id))
        answers, cells = answers_by_session(db, ln.id), _cells(db, ln.id)
        result = run_heavy_detached(db, launch_check, pinned, sessions, answers, cells)
        checked_links.add(ln.id)
        cc = result["crosscheck"]
        cc["findings"] = cc["findings"][:_MAX_FINDINGS]
        return {"rows_checked": result["n_checked_rows"], "crosscheck": cc,
                "incidence_pct": result["field_report"].get("incidence_pct"),
                "screened_out_at": _screen_outs(
                    pinned, result["field_report"].get("terminates_by_question", []))}

    def dry_run(args):
        n = max(50, min(int(args.get("n") or 500), 5000))
        report = run_heavy_detached(db, simulate, survey(args), [], n=n, seed=20260915).to_dict()
        return {k: report[k] for k in ("n_respondents", "passed", "dispositions",
                                       "never_fired", "impossible", "questions_never_shown")}

    #: Cells sent back to the model for one table. Enough to read a crossbreak
    #: and small enough that a long table does not crowd out everything else in
    #: the turn.
    _MAX_TABLE_ROWS = 40

    def ask_data(args):
        s = study(args)
        try:
            answer = chat_with_study(s.id, ChatRequest(message=str(args.get("question", ""))),
                                     db=db, user=user)
        except HTTPException as exc:
            raise _ToolError(str(exc.detail)) from exc

        # The NUMBERS, not only the sentence about them. The data engine
        # computes a real table — columns, rows, counts — and this tool used to
        # return `answer.message.content` alone, so a crosstab arrived as
        # "awareness is higher among 18-34s than 35+" with the cells discarded.
        # The model then said so, correctly and uselessly: "the tool returned
        # the direction only, no cell counts or percentages."
        tables = [
            {
                "columns": result.get("columns"),
                "rows": (result.get("rows") or [])[:_MAX_TABLE_ROWS],
                "row_count": result.get("row_count"),
                "truncated": bool(result.get("truncated"))
                or len(result.get("rows") or []) > _MAX_TABLE_ROWS,
            }
            for result in (answer.tool_results or [])
            if result.get("columns") and result.get("rows")
        ]
        return {"answer": answer.message.content, "tables": tables}

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

    def propose_set_look(args):
        from api.routes.questionnaire import _latest_questionnaire

        s = study(args)
        _latest_questionnaire(db, s.id)  # 404s into a tool error below when there is none
        body, said = {}, []
        for key, allowed in (("template", TEMPLATE_SLUGS), ("theme", THEME_SLUGS)):
            value = str(args.get(key) or "").strip().lower()
            if not value:
                continue
            if value not in allowed:
                raise _ToolError(f"no {key} {value!r}; choose one of {', '.join(sorted(allowed))}")
            body[key] = value
            said.append(f"{key} {value}")
        if not body:
            raise _ToolError("name a template, a theme, or both")
        return propose(
            "look", f"Set {s.client_name or s.id}'s survey look: {' and '.join(said)}",
            "Links opened from now on, test links included, use it. Links already "
            "in the field keep the look their respondents started with.",
            "PATCH", f"/studies/{s.id}/questionnaire/settings", body)

    def propose_approve_clean(args):
        from api.routes.questionnaire import (
            _latest_questionnaire,
            _load_definition,
            clean_split,
        )

        s = study(args)
        questionnaire = _latest_questionnaire(db, s.id)
        clean, blocked = clean_split(_load_definition(db, questionnaire),
                                     set(questionnaire.approved_qids or []))
        if not clean:
            raise _ToolError(
                "no clean question is waiting: "
                + (f"{len(blocked)} flagged ones need a person" if blocked
                   else "every question is already approved"))
        shown = ", ".join(clean[:12]) + (f" and {len(clean) - 12} more" if len(clean) > 12 else "")
        return propose(
            "approve_clean", f"Approve {len(clean)} clean questions in {s.client_name or s.id}",
            f"Approves {shown}. "
            + (f"{len(blocked)} flagged questions stay for a person to look at."
               if blocked else "Nothing is flagged."),
            "POST", f"/studies/{s.id}/questionnaire/questions/approve-clean")

    def propose_add_quota(args):
        s, ln = link(args)
        if ln.status != "open":
            raise _ToolError(f"link {ln.id} is {ln.status}; quotas go on an open link")
        pinned = SurveyDefinition.from_json(ln.survey_json)
        qid = str(args.get("qid") or "")
        listable = [x for x in pinned.questions
                    if x.type in (QuestionType.SINGLE, QuestionType.MULTI) and x.options]
        q = next((x for x in listable if x.qid == qid), None)
        if q is None:
            # Every question a quota can use, with its options: the model picks
            # the right one and calls again, rather than asking the user for an
            # id it was never told.
            def offered(x):
                return ", ".join(f"{o.code}={o.label}" for o in x.options[:12])

            choices = "; ".join(f'{x.qid} "{x.text[:60]}" [{offered(x)}]' for x in listable)
            raise _ToolError(f"{qid!r} is not a question a quota can use on link {ln.id}. "
                             f"Call again with one of: {choices}")
        labels = {o.code: o.label for o in q.options}
        by_label = {o.label.strip().lower(): o.code for o in q.options}
        codes, unknown = set(), []
        for given in args.get("codes") or []:
            # An option number, or its label: "Woman" is what people say.
            text = str(given).strip()
            if text.lstrip("-").isdigit() and int(text) in labels:
                codes.add(int(text))
            elif text.lower() in by_label:
                codes.add(by_label[text.lower()])
            else:
                unknown.append(given)
        codes = sorted(codes)
        if not codes or unknown:
            raise _ToolError(f"{qid}'s options are "
                             f"{', '.join(f'{c}={t}' for c, t in labels.items())}; "
                             f"got {unknown or 'none'}")
        limit = int(args.get("limit") or 0)
        if limit < 1:
            raise _ToolError("a quota needs a limit of at least 1 complete")
        if q.type == QuestionType.MULTI:
            leaves = [{"op": "selected", "qid": qid, "value": c} for c in codes]
            condition = leaves[0] if len(leaves) == 1 else {"op": "or", "children": leaves}
        else:
            condition = ({"op": "eq", "qid": qid, "value": codes[0]} if len(codes) == 1
                         else {"op": "in", "qid": qid, "values": codes})
        words = " or ".join(labels[c] for c in codes)
        name = str(args.get("name") or "").strip()[:60] or words[:60]
        return propose(
            "quota", f"Add quota \"{name}\" to link {ln.id}: {limit} completes",
            f"Counts completes where {qid} is {words}; once {limit} are in, new "
            "respondents who match are turned away. Others keep coming.",
            "POST", f"/studies/{s.id}/fielding/links/{ln.id}/quotas",
            {"cells": [{"name": name, "condition": condition, "limit": limit}]})

    tools = {
        "list_studies": list_studies_tool,
        "study_overview": study_overview,
        "find_question": find_question,
        "question_details": lambda a: get_question_details(survey(a), a["study_id"],
                                                           str(a.get("qid", ""))),
        "who_was_asked": lambda a: explain_question_base(survey(a), a["study_id"],
                                                         str(a.get("qid", ""))),
        "search_verbatims": search_verbatims,
        "qc_findings": qc_findings,
        "extraction_diagnosis": extraction_diagnosis,
        "propose_reextract": propose_reextract,
        "study_brief": study_brief,
        "field_report": field_report,
        "soft_launch_check": soft_launch_check,
        "dry_run": dry_run,
        "ask_data": ask_data,
        "propose_open_link": propose_open_link,
        "propose_set_hold": propose_set_hold,
        "propose_close_link": propose_close_link,
        "propose_materialize": propose_materialize,
        "propose_set_look": propose_set_look,
        "propose_approve_clean": propose_approve_clean,
        "propose_add_quota": propose_add_quota,
    }

    def execute(name: str, args: dict) -> dict:
        tool = tools.get(name)
        if tool is None:
            return {"success": False, "error": f"unknown tool {name}"}
        try:
            return {"success": True, **tool(args or {})}
        except _ToolError as exc:
            return {"success": False, "error": str(exc)}
        except HTTPException as exc:  # e.g. no questionnaire yet
            return {"success": False, "error": str(exc.detail)}
        except Exception:  # noqa: BLE001 - the model gets a sentence, the log gets the trace
            logger.exception(f"copilot tool {name} failed")
            return {"success": False, "error": f"{name} failed on the server"}

    return execute, proposals


def _lookups_note(lookups: list[dict] | None) -> str:
    """What an earlier answer looked up, so a follow-up ("why?", "and the other
    link?") knows which project and link it was about. Not the results: those
    are looked up again, because they may have changed since."""
    calls = [
        f"{x.get('tool')}({', '.join(f'{k}={v}' for k, v in (x.get('args') or {}).items())})"
        for x in lookups or [] if x.get("tool")
    ]
    return f"\n[Looked up for this answer: {'; '.join(calls)}]" if calls else ""


def _history(db: Session, conversation_id: str) -> list[dict]:
    rows = list_copilot_messages(db, conversation_id)[-_HISTORY_TURNS:]
    return [{"role": m.role,
             "content": (m.content or "")[:3900] + (_lookups_note(m.lookups)
                                                    if m.role == "assistant" else "")}
            for m in rows]


@router.post("/copilot", response_model=CopilotResponse)
def copilot(
    body: CopilotRequest,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """Ask the copilot anything about the platform. It may prepare actions,
    never perform them. Each exchange is saved to the user's conversation."""
    conversation = None
    if body.conversation_id:
        conversation = get_copilot_conversation(db, body.conversation_id, user.id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="No such conversation")
        history = _history(db, conversation.id)
    else:
        history = [t.model_dump() for t in body.history]
    execute, proposals = make_executor(db, user)
    page = ""
    if body.study_id and (current := get_study(db, body.study_id)):
        page = f"The user is looking at project {current.id} ({current.client_name})."
        links = list_fielding_links_for_study(db, current.id)
        if links:  # the real IDs, so a model never has to guess one
            page += " Its fielding links: " + "; ".join(
                f"{ln.id} ({ln.status}"
                + (f", held at {ln.soft_launch_limit}" if ln.soft_launch_limit else "")
                + f", {fielding_link_counts(db, ln.id)['completes']} completes)"
                for ln in links
            ) + "."
    try:
        reply, results, answered_by = run_copilot(
            body.message, history, execute,
            user=user.name or user.email, role=user.role, page=page)
    except BackendUnavailable as exc:
        logger.warning(f"copilot: no backend ({exc})")
        raise HTTPException(status_code=503,
                            detail="The copilot can't reach an AI model right now.") from exc
    # A provider that fails mid-answer is retried on the next one from the top,
    # which re-prepares the same cards: keep one card per distinct action.
    unique = list({(c.method, c.path, json.dumps(c.body, sort_keys=True)): c
                   for c in proposals}.values())
    lookups = [{"tool": r.get("tool", ""), "args": r.get("args") or {}} for r in results]
    conversation = save_copilot_exchange(
        db, conversation, user.id, body.study_id, body.message, reply, lookups,
        [c.model_dump() for c in unique], answered_by)
    return CopilotResponse(reply=reply, proposals=unique,
                           tools_used=[r.get("tool", "") for r in results],
                           answered_by=answered_by, conversation_id=conversation.id,
                           tool_errors=[f"{r.get('tool')}: {r['error']}" for r in results
                                        if r.get("success") is False and r.get("error")])


@router.get("/copilot/conversations", response_model=list[CopilotConversationSummary])
def copilot_conversations(
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """The signed-in user's own conversations, most recent first."""
    return [CopilotConversationSummary(id=c.id, title=c.title or "Conversation",
                                       study_id=c.study_id, updated_at=c.updated_at)
            for c in list_copilot_conversations(db, user.id)]


def _own(db: Session, conversation_id: str, user: UserDB):
    found = get_copilot_conversation(db, conversation_id, user.id)
    if found is None:  # someone else's reads the same as none at all
        raise HTTPException(status_code=404, detail="No such conversation")
    return found


@router.get("/copilot/conversations/{conversation_id}", response_model=CopilotConversation)
def copilot_conversation(
    conversation_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    c = _own(db, conversation_id, user)
    return CopilotConversation(
        id=c.id, title=c.title or "Conversation", study_id=c.study_id, updated_at=c.updated_at,
        messages=[CopilotStoredMessage(
            role=m.role, content=m.content or "",
            proposals=[CopilotProposal(**p) for p in m.proposals or []],
            tools_used=[x.get("tool", "") for x in m.lookups or []],
            answered_by=m.answered_by, created_at=m.created_at,
        ) for m in list_copilot_messages(db, c.id)],
    )


@router.delete("/copilot/conversations/{conversation_id}", status_code=204)
def delete_conversation(
    conversation_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    delete_copilot_conversation(db, _own(db, conversation_id, user))

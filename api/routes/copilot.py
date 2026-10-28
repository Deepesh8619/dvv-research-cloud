"""The copilot endpoint: the platform's tools, executed as the signed-in user.

READ tools run here. ACTION tools only build a proposal — see src/ai/copilot.py
for why. A proposal's title and detail are written from database rows this
module has validated (the study exists, the link belongs to it, the link is
open), never from the model's own words, so a card can't be talked into
describing something other than what Confirm will do.
"""

import json
import logging
import re
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.auth import UserRole, get_current_user, require_role
from api.db import (
    UserDB,
    answers_by_session,
    delete_copilot_conversation,
    fielding_link_counts,
    flag_copilot_answer,
    get_copilot_conversation,
    get_db,
    get_fielding_link,
    get_study,
    list_copilot_conversations,
    list_copilot_messages,
    list_fielding_links_for_study,
    list_flagged_copilot_answers,
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
    CopilotFeedback,
    CopilotProposal,
    CopilotRequest,
    CopilotResponse,
    CopilotStoredMessage,
)
from src.ai.copilot import run_copilot
from src.ai.copilot_tables import charts_in, tables_in
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


#: Words people use for an answer the questionnaire words differently. Gender
#: is the commonest quota there is, and "Female" for an option labelled
#: "Woman" was refused on Bedrock.
_SAME_ANSWER = {"female": "woman", "females": "woman", "women": "woman",
                "male": "man", "males": "man", "men": "man"}


def _answer_key(label: object) -> str:
    text = " ".join(str(label).strip().lower().split())
    return _SAME_ANSWER.get(text, text)


#: How a graph edge reads to a researcher, from the depending question's side.
_EDGE_WORDS = {
    "shown_if": "decides who is shown it",
    "terminates_on": "screens out on its answer",
    "masked_by": "masks its options",
    "piped_from": "pipes text into it",
    "derived_from": "derives a code from it",
}


def neighbours(definition, qid: str) -> dict:
    """One hop around a question, read from the questionnaire itself."""
    from src.engines.questionnaire.graph_sync import _deps

    if qid not in definition:
        return {}
    real = {q.qid.upper(): q.qid for q in definition.questions}  # masks store qids upper-cased
    upstream = sorted({real[ref.upper()] for _kind, ref in _deps(definition[qid])
                       if ref.upper() in real})
    downstream = sorted({q.qid for q in definition.questions if q.qid != qid
                         and qid.upper() in {ref.upper() for _k, ref in _deps(q)}})
    return {k: v for k, v in (("depends_on", upstream), ("decides", downstream)) if v}


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

    def current(args: dict):
        """(study, questionnaire row, definition) — the questionnaire as it stands
        NOW, draft or approved: what the review page shows. Respondents see a
        link's pinned copy; editing and teaching work on this one."""
        from api.routes.questionnaire import _latest_questionnaire, _load_definition

        s = study(args)
        row = _latest_questionnaire(db, s.id)
        return s, row, _load_definition(db, row)

    def question(args: dict):
        s, row, definition = current(args)
        # Models name it either way; a small one wrote `question_id`.
        qid = str(args.get("qid") or args.get("question_id") or "")
        if qid not in definition:
            known = ", ".join(q.qid for q in definition.questions[:80])
            raise _ToolError(f"no question {qid!r} in {s.id}. Its questions are: {known}")
        return s, row, definition, definition[qid]

    def propose(kind: str, title: str, detail: str, method: str, path: str,
                body: dict | None = None, diff: list[dict] | None = None) -> dict:
        if not can_act:
            raise _ToolError(f"a {user.role} can't do this; an owner or analyst can")
        card = CopilotProposal(id=uuid4().hex[:8], kind=kind, title=title, detail=detail,
                               method=method, path=path, body=body or {}, diff=diff or [])
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
        _s, _row, definition = current(args)
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
        _s, _row, definition = current(args)
        study_id, query = args["study_id"], str(args.get("query", ""))
        result = search_questionnaire(definition, study_id, query)
        try:
            found = rag_search(db, study_id, query, survey=definition)
        except Exception:  # an index problem must never break the exact search
            logger.exception("[%s] semantic search failed", study_id)
            return result
        result["related_by_meaning"] = [
            {"ref": m["ref"], "text": m["text"][:300], "score": m["score"],
             # GraphRAG: a question found by meaning brings its neighbours —
             # what decides who sees it and what its answer decides — so the
             # answer can say "Q5, asked only of those aware of Alpha (Q2)".
             **neighbours(definition, m["ref"])}
            for m in found["matches"]
        ]
        if found.get("note"):
            result["related_by_meaning_note"] = found["note"]
        return result

    def search_project(args):
        """One search over everything the project knows, each hit with a citation.

        Questions and the study brief, QC findings on the data, and the lessons
        taught about its documents — hybrid-ranked (meaning, words, codes).
        Respondents' own words stay behind search_verbatims and its data rule.
        """
        from api.routes.corrections import _profile_slug
        from src.engines.rag.store import CONTEXT_KINDS, SURVEY_KINDS, ensure_context_index

        s, row, definition = current(args)
        query = str(args.get("query", "")).strip()
        ensure_context_index(db, s.id, _profile_slug(row))
        found = rag_search(db, s.id, query, survey=definition,
                           kinds=SURVEY_KINDS + CONTEXT_KINDS, k=8)
        return {
            "matches": [
                {"cite": m["cite"], "kind": m["kind"], "text": m["text"][:400],
                 "matched_by": m.get("matched_by", []),
                 **(neighbours(definition, m["ref"]) if m["kind"] == "question" else {})}
                for m in found["matches"]
            ],
            "note": found.get("note") or "Cite what you use in brackets, as given: "
                    "[question:Q5], [finding:finding-2], [lesson:rule-…].",
        }

    def question_graph(args):
        """Everything one question is tied to, from the study graph and the solver."""
        from src.engines.questionnaire.graph_sync import sync_questionnaire
        from src.engines.questionnaire.logic_proof import prove
        from src.platform import graph

        s, _row, definition, q = question(args)
        # The graph is written on approval only; refresh it from the questionnaire
        # as it stands, so a draft or a later edit is what gets walked.
        # Flushed, not committed: a commit expires the request's user row, and the
        # solver below closes the session, so the next tool read a detached user
        # (HTTP 500 on the server, where the user comes from this session).
        sync_questionnaire(db, s.id, definition)
        node = graph.node_id(s.id, "question", q.qid)

        def described(rows):
            return [{"qid": r["ref"], "how": _EDGE_WORDS.get(r["via"], r["via"]),
                     "steps_away": r["depth"], "text": (r["label"] or "")[:120]}
                    for r in rows if r["kind"] == "question"]

        depends_on = described(graph.lineage(db, s.id, node, max_depth=6))
        affects = described(graph.impact(db, s.id, node, max_depth=6))
        quotas = []
        for ln in list_fielding_links_for_study(db, s.id):
            for cell in list_quota_cells(db, ln.id):
                refs = Expression.from_dict(json.loads(cell.condition_json)).referenced_qids()
                if q.qid in refs:
                    quotas.append({"link_id": ln.id, "cell": cell.name, "limit": cell.cell_limit,
                                   "count": cell.count})
        proof = run_heavy_detached(db, prove, definition)
        why_not = next((u["why"] for u in proof["unreachable"] if u["qid"] == q.qid), None)
        unproved = next((u["reason"] for u in proof["not_provable"] if u["qid"] == q.qid), None)
        return {
            "qid": q.qid,
            "depends_on": depends_on,
            "affects": affects,
            "quota_cells": quotas,
            "reachable": (False if why_not else None if unproved
                          else True if q.qid in proof["examples"] else None),
            "why_unreachable": why_not,
            "example_path": proof["examples"].get(q.qid),
            "not_provable": unproved,
            "note": "depends_on: what decides whether and how it is asked; affects: what "
                    "would change if it changed or went. example_path is one set of answers "
                    "that reaches it, found by the logic solver.",
        }

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

    def field_projection(args):
        from api.routes.materialize import link_projection

        _s, ln = link(args)
        p = link_projection(db, ln)
        daily = {"title": f"Link {ln.id}, per day", "header": ["Day", "Starts", "Completes"],
                 "rows": [[d["day"], str(d["starts"]), str(d["completes"])] for d in p["daily"]],
                 "truncated": False, "significance_tested": False}
        return {**p, "tables": [daily] if p["daily"] else [],
                "note": "Computed from the link's sessions. Report the rate with its basis, "
                        "each goal's remaining completes, finish date and starts needed, "
                        "and every warning. Do not recompute or adjust any figure."}

    def dry_run(args):
        n = max(50, min(int(args.get("n") or 500), 5000))
        report = run_heavy_detached(db, simulate, survey(args), [], n=n, seed=20260915).to_dict()
        # Named for what they are. Asked "why is incidence low on this link?",
        # a model read these counts as the link's and reported "40% answered No"
        # where the real link had 33%: simulated figures passed off as field ones.
        return {
            "about": "SIMULATED respondents made up to test the script's logic. Not anyone "
                     "who took a link: never report these counts or percentages as field "
                     "results. What happened on a link comes from field_report.",
            "simulated_respondents": report["n_respondents"],
            "logic_passed": report["passed"],
            "simulated_dispositions": report["dispositions"],
            **{k: report[k] for k in ("never_fired", "impossible", "questions_never_shown")},
        }

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
        # Every shape the navigator returns — a crosstab with significance
        # letters arrives under `table`, and was dropped here until it wasn't.
        tables = [t for result in (answer.tool_results or []) for t in tables_in(result)]
        charts = [c for result in (answer.tool_results or []) for c in charts_in(result)]
        note = ("These tables and charts are shown to the user under your reply exactly "
                "as computed, with downloads. Do not retype their cells; say what they show.")
        # success False with an empty error is still a failure — a StopIteration
        # once arrived as "" and the model could only say "there was an issue".
        errors = [str(r.get("error") or f"{r.get('tool', 'the data tool')} failed with no message")
                  for r in answer.tool_results or []
                  if isinstance(r, dict) and (r.get("error") or r.get("success") is False)]
        # The eval caught the model saying "here is the chart" over an empty list.
        if not charts and re.search(r"\b(chart|graph|plot)", str(args.get("question", "")), re.I):
            note += (" NO CHART WAS DRAWN" + (f" ({'; '.join(errors)[:300]})" if errors else "")
                     + ". Say so plainly; do not describe a chart.")
        elif errors and not tables:
            # Seen live: "there was an issue generating the crosstab" — the
            # user can't act on that. The engine's own words, and a retry.
            note += (f" THE DATA ENGINE FAILED: {'; '.join(errors)[:300]}. If it names closest "
                     "variables, call ask_data again with the right one; otherwise quote this "
                     "error to the user.")
        return {
            "answer": answer.message.content,
            "tables": tables,
            "charts": charts,
            # Whether a test ran, so "significant" in a reply can be held to it.
            "significance_tested": any(t["significance_tested"] for t in tables),
            "note": note,
        }

    def make_tables(args):
        """Any set of tables: rows x columns, filtered, grouped, weighted — one tool
        for the whole family instead of one tool per phrasing."""
        from api.routes.chat import _load_datamap, _parse_filter, _study_dir, _unknown, by_label
        from src.engines.tabs.crosstab import Net
        from src.engines.tabs.runner import MAX_WORKBOOK_TABLES, tables_job
        from src.engines.tabs.spec import TableSpec, TabPlan, auto_nets
        from src.orchestration.orchestrator import auto_tab_plan

        s = study(args)
        d = _study_dir(s)
        dm = _load_datamap(d, s)
        if dm is None or not (d / "data.parquet").exists():
            raise _ToolError(f"{s.id} has no collected data to tabulate yet")

        def flat(names):  # a plain number (raw age, a ZIP) or open text: no categories
            return [n for n in names if not dm[n].valid_codes()]

        def what(name):
            kind = "open-text answers" if dm[name].type.value == "text" else "a plain number"
            return f"{name} ({dm[name].label[:50]}) is {kind}"

        def good_columns():
            from src.orchestration.orchestrator import suggest_banner_candidates

            return ", ".join(f"{n} ({dm[n].label[:40]})" for n in suggest_banner_candidates(dm, 8))

        def named(word: str) -> str | None:
            """The variable meant: its name, or the ONE variable with categories
            whose label says it ("gender"). Several candidates go back as a list."""
            if word in dm:
                return word
            hits = [x for x in by_label(dm, word) if dm[x].valid_codes()]
            return hits[0] if len(hits) == 1 else None

        cols = [str(b) for b in args.get("columns") or []]
        if bad := [b for b in cols if named(b) is None]:
            raise _ToolError(_unknown("column variable(s)", bad, dm))
        banner = list(dict.fromkeys(named(b) for b in cols))
        if f := flat(banner):
            # Seen live: the model chose S4 (ZIP code) by itself and was told to
            # "use an age break". Name what it is, and what would work here.
            raise _ToolError("; ".join(what(x) for x in f) + " — no answer categories to make "
                             "columns from. Variables that work as columns here: "
                             f"{good_columns()}. "
                             "If the user did not name the columns, ask them which.")

        # Rows: every question, or the names given — a grid's parent (Q5) brings its
        # rows (Q5r1, Q5r2…), so "Q5" works whichever way the data names them.
        asked = [str(r) for r in args.get("rows") or []]
        if not asked:
            auto = auto_tab_plan(dm, limit=MAX_WORKBOOK_TABLES + len(banner))
            stubs = [t.stub for t in (auto.tables if auto else [])]
        else:
            stubs, missing = [], []
            for r in asked:
                hit = [r] if r in dm else [
                    v.name for v in dm.variables
                    if v.question_ref == r
                    # Q5r1, Q5_r1, Q13_15 — never Q1 matching Q13
                    or re.fullmatch(rf"{re.escape(r)}(?:[_.]\w+|[a-zA-Z]+\d+.*)", v.name)]
                if not hit and named(r):
                    hit = [named(r)]
                (stubs.extend(hit) if hit else missing.append(r))
            if missing:
                raise _ToolError(_unknown("row variable(s)", missing, dm))
        stubs = [x for x in dict.fromkeys(stubs) if x not in banner]
        skipped = flat(stubs)
        stubs = [x for x in stubs if x not in skipped][:MAX_WORKBOOK_TABLES]
        if not stubs:
            raise _ToolError("none of those rows has answer categories to tabulate"
                             + (f": {'; '.join(what(x) for x in skipped[:5])}" if skipped else "")
                             + ". Open text is read in the Open ends tab, not tabulated.")

        filter_, err = _parse_filter(args.get("filter"), dm)
        if err:
            raise _ToolError(err)
        weight = args.get("weight") or None
        if weight and weight not in dm:
            raise _ToolError(_unknown("weight variable", [weight], dm))
        try:
            custom = [Net(label=str(n["label"]), codes=[int(c) for c in n["codes"]])
                      for n in args.get("nets") or []]
        except (KeyError, TypeError, ValueError) as exc:
            raise _ToolError('nets look like [{"label": "Any A or B", "codes": [1, 2]}]') from exc
        mean = args.get("mean")

        def table(stub):
            codes = set(dm[stub].valid_codes())
            nets = auto_nets(dm[stub]) + [n for n in custom if set(n.codes) <= codes]
            return TableSpec(stub=stub, nets=nets, filter=filter_,
                             mean=len(codes) >= 5 if mean is None else bool(mean))

        title = ("Tables" if asked else "Every question") + (
            " by " + ", ".join(f"{b} ({dm[b].label[:40]})" for b in banner) if banner else "")
        plan = TabPlan(title=title, banner=banner, tables=[table(x) for x in stubs],
                       weight=weight, confidence=float(args.get("confidence") or 0.95))
        output = str(args.get("output") or "auto")
        inline_max = 0 if output == "excel" else 5
        filename = None
        if output == "excel" or len(stubs) > inline_max:
            # Served by the chart route, which already checks the study and the path.
            safe = "_".join(re.sub(r"\W", "", b) for b in banner) or "total"
            filename = f"tables_by_{safe}_{uuid4().hex[:6]}.xlsx"
            (d / "charts").mkdir(exist_ok=True)
        shown, n = run_heavy_detached(db, tables_job, d / "data.parquet", plan, dm,
                                      d / "charts" / filename if filename else None, inline_max)
        tables = [t for x in shown for t in tables_in({"table": x})]
        return {
            "tables_made": n,
            "skipped_plain_numbers": skipped,
            "tables": tables,
            "significance_tested": any(t["significance_tested"] for t in tables) or (
                filename is not None and len(banner) > 0),
            "charts": [{"study_id": s.id, "filename": filename, "title": title,
                        "kind": "workbook"}] if filename else [],
            "note": ("The tables are shown under your reply exactly as computed" if tables else
                     "The workbook is offered as a download under your reply, one sheet per "
                     "table with significance letters") + ". Say how many tables and what the "
                     "columns are; do not retype cells or list every table."
                     + (f" Skipped (plain numbers, no categories): {', '.join(skipped)}."
                        if skipped else ""),
        }

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
        given_labels = {_answer_key(g) for g in args.get("codes") or []
                        if not str(g).strip().lstrip("-").isdigit()}
        if q is not None and not given_labels <= {_answer_key(o.label) for o in q.options}:
            # A real question, but not the one these answers belong to (Bedrock
            # put "Female" on "Do you drink coffee?"): look for the one that is.
            named, q = q, None
        else:
            named = None
        if q is None and given_labels:
            # Seen on Bedrock: the model guessed `q_gender`, was handed the real
            # list, and relayed it to the user instead of calling again. The
            # answer it named ("Woman") picks out the question when exactly one
            # offers it; the card then says which question, so the person
            # confirming sees the choice that was made.
            owners = [x for x in listable
                      if given_labels <= {_answer_key(o.label) for o in x.options}]
            if len(owners) == 1:
                q = owners[0]
                qid = q.qid
        if q is None and named is not None:
            q = named  # nothing better: refused below with this question's options
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
        by_label = {_answer_key(o.label): o.code for o in q.options}
        codes, unknown = set(), []
        for given in args.get("codes") or []:
            # An option number, or its label: "Woman" is what people say.
            text = str(given).strip()
            if text.lstrip("-").isdigit() and int(text) in labels:
                codes.add(int(text))
            elif _answer_key(text) in by_label:
                codes.add(by_label[_answer_key(text)])
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
            f"Counts completes where {qid} (\"{q.text[:80]}\") is {words}; once {limit} "
            "are in, new respondents who match are turned away. Others keep coming.",
            "POST", f"/studies/{s.id}/fielding/links/{ln.id}/quotas",
            {"cells": [{"name": name, "condition": condition, "limit": limit}]})

    # --- the questionnaire under review: read how it was read, change it, teach --

    def reading_conventions(args):
        """The document's format, what has been taught about it, and what can be."""
        from api.db import ProfileRuleDB
        from api.routes.corrections import _profile_slug
        from src.engines.questionnaire.learned import _ALLOWED_VALUES, PATTERN_KINDS

        _s, row, _definition = current(args)
        slug = _profile_slug(row)
        taught = [] if not slug else [
            {"kind": r.pattern_kind, "pattern": r.pattern, "value": r.pattern_value}
            for r in db.query(ProfileRuleDB).filter(
                ProfileRuleDB.profile_slug == slug, ProfileRuleDB.active.is_(True),
                ProfileRuleDB.pattern_kind.isnot(None))]
        return {
            "format": slug,
            "taught": taught,
            "kinds": {k: sorted(_ALLOWED_VALUES[k]) if k in _ALLOWED_VALUES else "no value"
                      for k in sorted(PATTERN_KINDS)},
            "note": None if slug else "This questionnaire has no detected document format, "
                    "so there is nothing to teach; edit its questions instead.",
        }

    def propose_edit_question(args):
        import copy

        from api.models import QuestionUpdateRequest
        from api.routes.questionnaire import _apply_update, question_fingerprint

        s, _row, definition, q = question(args)
        body: dict = {}
        text = str(args.get("text") or args.get("new_text") or "").strip()
        if text:
            body["text"] = text
        if args.get("type"):
            kind = str(args["type"]).strip().lower()
            if kind not in {t.value for t in QuestionType}:
                raise _ToolError(f"no question type {kind!r}; types are "
                                 f"{', '.join(t.value for t in QuestionType)}")
            body["type"] = kind
        if args.get("options"):
            options = []
            for i, o in enumerate(args["options"]):
                if isinstance(o, dict) and str(o.get("label") or "").strip():
                    options.append({"code": int(o.get("code") or i + 1),
                                    "label": str(o["label"]).strip()})
                elif str(o).strip():
                    options.append({"code": i + 1, "label": str(o).strip()})
            body["options"] = options
        for flag in ("required", "randomize_options"):
            if isinstance(args.get(flag), bool):
                body[flag] = args[flag]
        if not body:
            raise _ToolError("name what to change: text, type, options, required or "
                             "randomize_options")

        # Tried on a copy first, through the endpoint's own code, so a card that
        # would be refused on Confirm is never shown.
        trial = copy.deepcopy(definition)
        before = trial.validate()
        try:
            _apply_update(trial[q.qid], QuestionUpdateRequest(**body))
        except (ValueError, KeyError, TypeError) as exc:
            raise _ToolError(f"that change is not valid: {exc}") from exc
        if new := [p for p in trial.validate() if p not in before]:
            raise _ToolError("that change would break the questionnaire: " + "; ".join(new))

        def shown(field: str, question) -> str:
            value = getattr(question, field)
            if field == "options":
                return "; ".join(f"{o.code}={o.label}" for o in value or []) or "(none)"
            return getattr(value, "value", value) if value is not None else "(none)"

        diff = [{"field": field, "before": str(shown(field, q)),
                 "after": str(shown(field, trial[q.qid]))} for field in body]
        body["base_fingerprint"] = question_fingerprint(q)
        return propose(
            "edit_question", f"Change {q.qid}: {', '.join(d['field'] for d in diff)}",
            f"Saved to the questionnaire under review; {q.qid} goes back to review. If anyone "
            f"changes {q.qid} before you confirm, this card is refused.",
            "PATCH", f"/studies/{s.id}/questionnaire/questions/{q.qid}", body, diff)

    def propose_reread_question(args):
        s, _row, _definition, q = question(args)
        if not q.source_text:
            raise _ToolError(f"{q.qid} has no source text from a document to read again")
        return propose(
            "reread", f"Read {q.qid} again from the document",
            f"Re-reads {q.qid} from its source text with today's parsers and everything "
            "taught about this format. No AI model is used; a reading that is not better "
            "is not kept.",
            "POST", f"/studies/{s.id}/questionnaire/questions/{q.qid}/reread")

    def _refuse_if_used(definition, qid: str, doing: str) -> None:
        from api.routes.questionnaire import _dependents

        if used_by := sorted(_dependents(definition, qid)):
            raise _ToolError(f"cannot {doing} {qid}: {', '.join(used_by)} read its answer "
                             "(routing, masks, pipes). Change those first.")

    def propose_question_in_link(args):
        s, _row, definition, q = question(args)
        in_link = bool(args.get("in_link"))
        if not in_link:
            _refuse_if_used(definition, q.qid, "leave out")
        return propose(
            "in_link", f"{'Ask' if in_link else 'Leave out'} {q.qid} in the survey link",
            ("Respondents will be asked it again in links opened from now on."
             if in_link else
             "It stays in the questionnaire and the datamap, but links opened from now on "
             "do not ask it. Links already in the field keep their own copy."),
            "PUT", f"/studies/{s.id}/questionnaire/questions/{q.qid}/in-link",
            {"in_link": in_link})

    def propose_approve_question(args):
        s, row, _definition, q = question(args)
        if q.qid in set(row.approved_qids or []):
            raise _ToolError(f"{q.qid} is already approved")
        flags = sorted(q.flags or [])
        return propose(
            "approve_question", f"Approve {q.qid}",
            "Marks it checked by a person" + (f", clearing its flags ({', '.join(flags)})"
                                              if flags else "") + ".",
            "POST", f"/studies/{s.id}/questionnaire/questions/{q.qid}/approve")

    def propose_delete_question(args):
        s, _row, definition, q = question(args)
        _refuse_if_used(definition, q.qid, "delete")
        return propose(
            "delete_question", f"Delete {q.qid} from the questionnaire",
            f"Removes \"{q.text[:120]}\" from the questionnaire under review. To keep it in "
            "the data but not ask it, leave it out of the link instead.",
            "DELETE", f"/studies/{s.id}/questionnaire/questions/{q.qid}")

    _MEANING = {
        "type_signal": "marks a {value} question",
        "field_alias": "labels the {value} field",
        "option_tag": "marks an option as {value}",
        "randomize_signal": "means the options are shuffled",
        "screen_out_range": "introduces a range screen-out",
        "hidden_variable": "marks a computed variable that is never asked",
    }

    #: How people name a question type, in the words the parser uses.
    _TYPE_WORDS = {"single choice": "single", "single-choice": "single", "single select": "single",
                   "multiple choice": "multi", "multi-choice": "multi", "multiple": "multi",
                   "multi select": "multi", "open": "open_text", "open end": "open_text",
                   "open-ended": "open_text", "grid": "grid_single", "ranking": "rank"}

    def _kind_for(kind: str, value: str) -> str:
        """The pattern kind, worked out from the value when the model named it wrong.

        Seen on a model: `kind="[SA]"`, then `kind="option_tag"` with value
        "single". The value can only belong to one kind, so it decides.
        """
        from src.engines.questionnaire.learned import _ALLOWED_VALUES, PATTERN_KINDS

        if kind in PATTERN_KINDS and value in _ALLOWED_VALUES.get(kind, {value}):
            return kind
        owners = [k for k, allowed in _ALLOWED_VALUES.items() if value in allowed]
        return owners[0] if len(owners) == 1 else kind

    def propose_teach_reading(args):
        from api.auth import UserRole as Role
        from api.routes.corrections import _CORRECTION_ROLES, _profile_slug
        from src.engines.questionnaire.learned import validate_pattern

        s, row, definition = current(args)
        if Role(user.role) not in _CORRECTION_ROLES:
            raise _ToolError("teaching the parser is for owners and founders")
        slug = _profile_slug(row)
        if not slug:
            raise _ToolError("this questionnaire has no detected document format, so there "
                             "is nothing to teach; edit its questions instead")
        pattern = str(args.get("pattern") or "").strip()
        value = " ".join(str(args.get("value") or "").strip().lower().split())
        value = _TYPE_WORDS.get(value, value)
        kind = _kind_for(str(args.get("kind") or "").strip().lower(), value)
        if problem := validate_pattern(kind, pattern, value):
            raise _ToolError(problem)
        needle = pattern.strip("[]").strip().lower()
        hits = [x.qid for x in definition.questions
                if needle in (x.source_text or x.text or "").lower()]
        meaning = _MEANING[kind].format(value=value)
        return propose(
            "teach", f"Teach the {slug} format: \"{pattern}\" {meaning}",
            f"{len(hits)} question(s) in this document contain it"
            + (f" ({', '.join(hits[:12])}{'…' if len(hits) > 12 else ''})" if hits else "")
            + ". Confirm re-reads this questionnaire with it now; later documents in this "
              "format are read the same way. It can be switched off in the format's rules.",
            "POST", f"/profiles/{slug}/rules",
            {"pattern_kind": kind, "pattern": pattern, "pattern_value": value or None,
             "scope": "profile", "study_id": s.id})

    tools = {
        "list_studies": list_studies_tool,
        "study_overview": study_overview,
        "find_question": find_question,
        "question_details": lambda a: get_question_details(current(a)[2], a["study_id"],
                                                           str(a.get("qid", ""))),
        "who_was_asked": lambda a: explain_question_base(current(a)[2], a["study_id"],
                                                         str(a.get("qid", ""))),
        "search_verbatims": search_verbatims,
        "qc_findings": qc_findings,
        "extraction_diagnosis": extraction_diagnosis,
        "propose_reextract": propose_reextract,
        "study_brief": study_brief,
        "field_report": field_report,
        "soft_launch_check": soft_launch_check,
        "dry_run": dry_run,
        "field_projection": field_projection,
        "question_graph": question_graph,
        "search_project": search_project,
        "ask_data": ask_data,
        "make_tables": make_tables,
        "propose_open_link": propose_open_link,
        "propose_set_hold": propose_set_hold,
        "propose_close_link": propose_close_link,
        "propose_materialize": propose_materialize,
        "propose_set_look": propose_set_look,
        "propose_approve_clean": propose_approve_clean,
        "propose_add_quota": propose_add_quota,
        "reading_conventions": reading_conventions,
        "propose_edit_question": propose_edit_question,
        "propose_reread_question": propose_reread_question,
        "propose_question_in_link": propose_question_in_link,
        "propose_approve_question": propose_approve_question,
        "propose_delete_question": propose_delete_question,
        "propose_teach_reading": propose_teach_reading,
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
        + (f" -> refused: {x['error']}" if x.get("error") else "")
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
        if qs := list_questionnaires_for_study(db, current.id):
            page += f" Its questionnaire is {qs[0].status}."
        if body.focus_qid:
            page += (f" They have question {body.focus_qid} open on the questionnaire page; "
                     "'this question' means it.")
        links = list_fielding_links_for_study(db, current.id)
        if links:  # the real IDs, so a model never has to guess one
            page += " Its fielding links: " + "; ".join(
                f"{ln.id} ({ln.status}"
                + (f", held at {ln.soft_launch_limit}" if ln.soft_launch_limit else "")
                + f", {fielding_link_counts(db, ln.id)['completes']} completes)"
                for ln in links
            ) + "."
    from src.platform import usage

    tagged = usage.purpose.set("copilot")  # this request's model calls count as copilot
    try:
        usage.count_question()
        reply, results, answered_by = run_copilot(
            body.message, history, execute,
            user=user.name or user.email, role=user.role, page=page)
    except BackendUnavailable as exc:
        logger.warning(f"copilot: no backend ({exc})")
        raise HTTPException(status_code=503,
                            detail="The copilot can't reach an AI model right now.") from exc
    finally:
        usage.purpose.reset(tagged)
    # A provider that fails mid-answer is retried on the next one from the top,
    # which re-prepares the same cards: keep one card per distinct action.
    unique = list({(c.method, c.path, json.dumps(c.body, sort_keys=True)): c
                   for c in proposals}.values())
    # A refusal is kept with its reason: a follow-up ("1", "try again") then knows
    # what failed, and a flagged answer shows why in Admin instead of a guess.
    lookups = [{"tool": r.get("tool", ""), "args": r.get("args") or {},
                **({"error": str(r.get("error"))[:300]} if r.get("success") is False else {})}
               for r in results]
    tables = [t for r in results if r.get("success") is not False
              for t in r.get("tables") or [] if isinstance(t, dict) and "header" in t]
    charts = [c for r in results if r.get("success") is not False
              for c in r.get("charts") or [] if isinstance(c, dict) and "filename" in c]
    conversation, seq = save_copilot_exchange(
        db, conversation, user.id, body.study_id, body.message, reply, lookups,
        [c.model_dump() for c in unique], answered_by, tables, charts)
    return CopilotResponse(reply=reply, proposals=unique, seq=seq,
                           tools_used=[r.get("tool", "") for r in results],
                           answered_by=answered_by, conversation_id=conversation.id,
                           tables=tables, charts=charts,
                           significance_tested=any(r.get("significance_tested") for r in results),
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
            tables=m.tables or [],
            charts=m.charts or [],
            answered_by=m.answered_by, created_at=m.created_at,
            seq=m.seq, feedback=m.feedback,
        ) for m in list_copilot_messages(db, c.id)],
    )


@router.post("/copilot/conversations/{conversation_id}/messages/{seq}/feedback",
             status_code=204)
def flag_answer(
    conversation_id: str,
    seq: int,
    body: CopilotFeedback,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """"This answer was wrong" — the gaps reach the team as a list, not by surprise."""
    _own(db, conversation_id, user)
    if not flag_copilot_answer(db, conversation_id, seq, body.wrong, body.note.strip()):
        raise HTTPException(status_code=404, detail="No such answer")


@router.get("/admin/copilot-feedback")
def flagged_answers(
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.FOUNDER)),
):
    """Every answer flagged as wrong, with the question behind it. (Owner/Founder)"""
    return list_flagged_copilot_answers(db)


@router.delete("/copilot/conversations/{conversation_id}", status_code=204)
def delete_conversation(
    conversation_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    delete_copilot_conversation(db, _own(db, conversation_id, user))


class _ExportTable(BaseModel):
    title: str = Field(default="Table", max_length=200)
    header: list[str] = Field(max_length=60)
    rows: list[list[str]] = Field(max_length=2000)


class _ExportRequest(BaseModel):
    tables: list[_ExportTable] = Field(min_length=1, max_length=20)


@router.post("/copilot/export.xlsx")
def export_tables(body: _ExportRequest, user: UserDB = Depends(get_current_user)):
    """The tables a copilot answer showed, as one Excel workbook, a sheet each.

    Built from exactly what the user was shown — no data is read here — so it
    cannot reach anything the answer did not already put on their screen.
    """
    import io
    import re

    from fastapi.responses import StreamingResponse
    from openpyxl import Workbook
    from openpyxl.styles import Font

    book = Workbook()
    book.remove(book.active)
    used: set[str] = set()
    for i, table in enumerate(body.tables, start=1):
        # Excel sheet names: 31 characters, none of : \ / ? * [ ], unique.
        name = re.sub(r"[:\\/?*\[\]]", " ", table.title).strip()[:28] or f"Table {i}"
        while name in used:
            name = f"{name[:25]} {i}"
        used.add(name)
        sheet = book.create_sheet(name)
        sheet.append(table.header)
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        for row in table.rows:
            sheet.append([_number_or_text(v) for v in row])
    out = io.BytesIO()
    book.save(out)
    out.seek(0)
    return StreamingResponse(
        out, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="copilot-tables.xlsx"'})


def _number_or_text(value: str):
    """A cell as Excel should hold it: 27.3 stays a number; "36.4% B" stays text."""
    try:
        return float(value)
    except ValueError:
        return value

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
import time
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.auth import UserRole, get_current_user, require_role, role_permits
from api.db import (
    UserDB,
    add_copilot_memory,
    answers_by_session,
    delete_copilot_conversation,
    delete_copilot_memory,
    fielding_link_counts,
    flag_copilot_answer,
    get_copilot_conversation,
    get_copilot_memory,
    get_db,
    get_fielding_link,
    get_study,
    list_copilot_answers,
    list_copilot_conversations,
    list_copilot_evals,
    list_copilot_memories,
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
    CopilotMemoryRequest,
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
from src.engines.questionnaire.conditions import compile_condition, describe_condition
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


def _route(module: str, name: str):
    """An endpoint function, imported when first used (the route modules import
    this one's neighbours; importing them at load time would be circular)."""
    def call(*args, **kwargs):
        import importlib

        return getattr(importlib.import_module(f"api.routes.{module}"), name)(*args, **kwargs)
    return call


def _brief(obj, depth: int = 0):
    """A screen's response, short enough for the model to read whole: long
    lists keep their first dozen and say how many more, long text is cut."""
    if hasattr(obj, "model_dump"):
        obj = obj.model_dump()
    if depth > 6:
        return "…"
    if isinstance(obj, dict):
        return {k: _brief(v, depth + 1) for k, v in list(obj.items())[:40]}
    if isinstance(obj, (list, tuple)):
        head = [_brief(x, depth + 1) for x in obj[:12]]
        return head + ([f"… and {len(obj) - 12} more"] if len(obj) > 12 else [])
    if isinstance(obj, str) and len(obj) > 300:
        return obj[:300] + "…"
    return obj


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


def _options_from(raw: list) -> list[dict]:
    """Answers as the copilot writes them: "99=Prefer not to answer [exclusive]",
    plain labels (numbered in order), or {code, label, exclusive, anchored}."""
    options = []
    for i, o in enumerate(raw, 1):
        if isinstance(o, dict):
            if not str(o.get("label") or "").strip():
                continue
            options.append({"code": int(o.get("code") or i), "label": str(o["label"]).strip(),
                            "exclusive": bool(o.get("exclusive")),
                            "anchored": bool(o.get("anchored") or o.get("anchor"))})
            continue
        text = str(o).strip()
        if not text:
            continue
        tags = {t.lower() for t in re.findall(r"\[(exclusive|anchor(?:ed)?)\]", text, re.I)}
        text = re.sub(r"\s*\[(?:exclusive|anchor(?:ed)?)\]", "", text, flags=re.I).strip()
        m = re.match(r"^(\d+)\s*[=:.)]\s*(.+)$", text)
        options.append({"code": int(m.group(1)) if m else i, "label": m.group(2) if m else text,
                        "exclusive": "exclusive" in tags,
                        "anchored": any(t.startswith("anchor") for t in tags)})
    return options


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
        # Every need the document stated, ticked off against the instrument,
        # plus how THIS document labels questions and marks scripter notes.
        checklist = ((definition.metadata or {}).get("decoding") or {}).get("brief")
        qa = (definition.metadata or {}).get("qa")
        if not brief and not understood and not checklist and not qa:
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
            "checklist": checklist,
            # Variables the notes say to create, questions only a person can
            # settle (each with options), and quality checks to add.
            "analyst": ((definition.metadata or {}).get("decoding") or {}).get("analyst"),
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

    def data_variables(args):
        """The variable list of the project's data: what is in it, what reached
        it from the uploaded file, and what each variable is."""
        import polars as pl

        from api.config import STUDIES_DIR
        from api.routes.chat import _load_datamap, _study_dir, by_label
        from src.adapters.tabular import source_column_names

        s = study(args)
        d = _study_dir(s)
        dm = _load_datamap(d, s)
        if dm is None:
            raise _ToolError(f"{s.id} has no data file processed yet")
        parquet = d / "data.parquet"
        columns = list(pl.read_parquet_schema(parquet)) if parquet.exists() else dm.names()
        # Open-end coding writes 4 columns per coded question (+ a respondent id)
        # into the data after the variable list was saved: they ARE the coding.
        coding = [c for c in columns if c not in dm and (c == "respondent_id" or re.search(
            r"_(codes|coding_confidence|coding_reasoning|needs_coding_review)$", c))]
        unlisted = [c for c in columns if c not in dm and c not in coding]
        types: dict[str, int] = {}
        for v in dm.variables:
            types[v.type.value] = types.get(v.type.value, 0) + 1
        no_codes = [v.name for v in dm.variables
                    if v.type.value == "single" and not v.values]

        source = STUDIES_DIR / s.id / (s.source_filename or "")
        try:
            in_file = (source_column_names(source)
                       if s.source_filename and source.is_file() else None)
        except Exception:  # noqa: BLE001 - a header we cannot read is "not checked", not an error
            in_file = None
        not_carried = [c for c in in_file or [] if c not in dm]

        found = []
        if query := str(args.get("query") or "").strip():
            names = [query] if query in dm else by_label(dm, query) or [
                n for n in dm.names() if n.lower().startswith(query.lower())]
            found = [{"name": n, "label": dm[n].label[:120], "type": dm[n].type.value,
                      "answer_codes": len(dm[n].valid_codes()),
                      "values": dict(list(dm[n].values.items())[:8])} for n in names[:15]]
        return {
            "variables_listed": len(dm),
            "by_type": types,
            "columns_in_data": len(columns),
            "coding_columns": len(coding),
            "coded_open_ends": sorted({re.sub(r"_(codes|coding_confidence|coding_reasoning|"
                                              r"needs_coding_review)$", "", c)
                                       for c in coding if c != "respondent_id"}),
            "columns_not_listed": unlisted[:20],
            "all_identified": not unlisted,
            "uploaded_file": s.source_filename,
            "columns_in_uploaded_file": len(in_file) if in_file is not None else None,
            "file_columns_not_in_data": not_carried[:30],
            "file_columns_not_in_data_count": len(not_carried),
            "single_choice_without_answer_labels": no_codes[:20],
            "matches": found,
            "note": "Columns of the uploaded file that are not in the data were empty for every "
                    "respondent: the importer drops those. Coding columns are the open-end "
                    "coding, added after the variable list. Pass `query` (a name or words) to "
                    "look a variable up.",
        }

    def product_help(args):
        """How DVV itself works, from the same help the app shows on each screen."""
        from src.platform.help import search_help

        return {"entries": search_help(str(args.get("query") or ""))}

    # --- every screen, read as that screen reads it ---------------------------
    # Each calls the endpoint the screen itself calls, with this user, so the
    # answer is what the screen shows — and a role that cannot open the screen
    # cannot read it through the Copilot either.

    def screen(fn, *roles):
        def read(args):
            s = study(args)
            if roles and not role_permits(user, *roles):
                raise _ToolError(f"a {user.role} cannot open this screen; "
                                 + " or ".join(r.value for r in roles) + " can")
            return {"study_id": s.id, "screen": _brief(fn(s.id, db=db, user=user))}
        return read

    def variable_plan(args):
        """The questionnaire's data plan: every column the link will write and
        what fills it — asked, computed by an approved rule (shown), computed
        from a base, assigned, recorded — and the hidden variables with no rule,
        which arrive empty."""
        from api.routes.questionnaire import questionnaire_datamap

        s = study(args)
        if not role_permits(user, UserRole.OWNER, UserRole.ANALYST):
            raise _ToolError(f"a {user.role} cannot open the Variables tab; "
                             "an owner or analyst can")
        plan = questionnaire_datamap(s.id, db=db, user=user)
        rows = plan["variables"]
        counts: dict[str, int] = {}
        for v in rows:
            counts[v["filled_by"]] = counts.get(v["filled_by"], 0) + 1
        want = str(args.get("variable") or "").strip().lower()

        def brief(v):
            return {k: v[k] for k in ("name", "label", "filled_by", "how", "question_ref")
                    if v.get(k)} | ({"values": dict(list(v["values"].items())[:10])}
                                    if v.get("values") else {})

        return {
            "study_id": s.id,
            "questionnaire_status": plan["status"],
            "columns": plan["n_variables"],
            "filled_by": counts,
            "hidden_without_rule": [v["name"] for v in rows if v["filled_by"] == "not_filled"],
            "computed_by_rule": [brief(v) for v in rows
                                 if v["filled_by"] == "computed" and not v["derived"]][:25],
            "matches": [brief(v) for v in rows if want and (
                want == v["name"].lower() or want == (v.get("question_ref") or "").lower()
                or want in (v.get("label") or "").lower())][:10],
            "note": "computed = an approved rule fills it for every respondent (in the link, "
                    "for routing and quotas, and in the data); not_filled = a hidden variable "
                    "with no approved rule: its column arrives empty until a rule is approved "
                    "in Questionnaire review or it is supplied with the sample; assigned = "
                    "the link sets it (rotations, splits); recorded = paradata.",
        }

    def report_findings(args):
        from api.routes.studies import get_findings, get_report

        s = study(args)
        out = {"study_id": s.id, "findings": _brief(get_findings(s.id, db=db, user=user))}
        try:
            out["cited_report"] = _brief(get_report(s.id, db=db, user=user))
        except HTTPException as exc:  # no report drafted yet is an answer, not an error
            out["cited_report"] = str(exc.detail)
        return out

    def open_end_coding(args):
        from api.routes.studies import coding_status, get_coding

        s = study(args)
        return {"study_id": s.id, "status": _brief(coding_status(s.id, db=db, user=user)),
                "coding": _brief(get_coding(s.id, db=db, user=user))}

    def compliance_check(args):
        from api.routes.compliance import adverse_events, notice_check

        s = study(args)
        if not role_permits(user, UserRole.OWNER, UserRole.ANALYST):
            raise _ToolError(f"a {user.role} cannot open compliance; an owner or analyst can")
        return {"study_id": s.id,
                "adverse_events": _brief(adverse_events(s.id, db=db, user=user)),
                "privacy_notice": _brief(notice_check(s.id, db=db, user=user))}

    def run_method(args):
        from api.routes.methods import MethodRequest
        from api.routes.methods import run_method as run

        s = study(args)
        try:
            body = MethodRequest(**{k: v for k, v in args.items() if k != "study_id"})
        except ValueError as exc:
            raise _ToolError(f"method needs: {exc}") from exc
        return {"study_id": s.id, "method": body.method,
                "result": _brief(run(s.id, body, db=db, user=user))}

    def team_and_costs(args):
        from api.routes.auth import list_all_users
        from src.platform.usage import summary

        if not role_permits(user, UserRole.OWNER, UserRole.FOUNDER):
            raise _ToolError(f"a {user.role} cannot see the team or AI costs")
        out = {"ai_usage_30_days": _brief(summary(db, days=30))}
        if role_permits(user, UserRole.OWNER):
            out["team"] = [{"name": u.name, "email": u.email, "role": u.role,
                            "active": u.is_active} for u in list_all_users(db=db, user=user)]
        return out

    def plan_tables(args):
        """What a table request means in this project's data: rows, columns,
        filter, weight, nets — resolved and checked once, for every tool that
        builds tables (make_tables, excel_report)."""
        from types import SimpleNamespace

        from api.routes.chat import _load_datamap, _parse_filter, _study_dir, _unknown, by_label
        from src.engines.tabs.crosstab import Net
        from src.engines.tabs.runner import MAX_WORKBOOK_TABLES
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
        return SimpleNamespace(s=s, d=d, dm=dm, banner=banner, stubs=stubs, skipped=skipped,
                               filter_=filter_, weight=weight, custom=custom, asked=asked)

    def excel_report(args):
        """An Excel report from instructions — finished, or refreshable by formula.
        Asks before building when the request leaves a decision open."""
        from src.engines.tabs.runner import MAX_WORKBOOK_TABLES, refreshable_job, report_job
        from src.engines.tabs.spec import TableSpec, TabPlan, auto_nets
        from src.orchestration.orchestrator import suggest_banner_candidates
        from src.renderers.excel_report import ReportStyle

        s = study(args)
        rows = args.get("rows")
        if isinstance(rows, str) or rows == ["all"]:
            rows = [] if str(rows).strip("[]'\" ").lower() in ("all", "every", "") else [rows]
        output = str(args.get("output") or "").lower()
        questions = []
        if "rows" not in args:
            questions.append({"id": "rows",
                              "question": "Which questions should the report include?",
                              "options": ["Every question", "Only some (I'll name them)"]})
        if "columns" not in args:
            from api.routes.chat import _load_datamap, _study_dir

            dm0 = _load_datamap(_study_dir(s), s)
            picks = [f"{n} ({dm0[n].label[:40]})" for n in suggest_banner_candidates(dm0, 6)] \
                if dm0 else []
            questions.append({"id": "columns", "question": "Which columns should every table be "
                              "broken by?", "options": ["Total only", *picks], "multiple": True})
        if output not in ("finished", "refreshable"):
            questions.append({"id": "output", "question": "Finished or refreshable?", "options": [
                "Finished: as computed, with significance letters",
                "Refreshable: paste next wave's data and it recalculates (no letters)"]})
        if questions:
            return {"needs_answers": questions,
                    "note": "Nothing built yet. In one sentence say what you understood, then "
                            "ask exactly these questions (the user sees them as options). "
                            "Call excel_report again with their answers. Defaults if they do not "
                            "say: one sheet per table, whole percentages, letters on, nets in "
                            "bold, no counts, no charts."}

        p = plan_tables({**args, "rows": rows})
        if output == "refreshable" and p.weight:
            raise _ToolError("a refreshable report counts respondents as they are, unweighted; "
                             "choose finished for a weighted report")
        if output == "refreshable" and len(p.stubs) > 150:
            raise _ToolError(f"{len(p.stubs)} tables is too many to recalculate by formula; "
                             "name fewer questions, or choose finished")
        style = ReportStyle(
            title=str(args.get("title") or ("Report" + (" by " + ", ".join(p.banner)
                                                         if p.banner else ""))),
            layout="one_sheet" if args.get("layout") == "one_sheet" else "sheet_per_table",
            decimals=max(0, min(int(args.get("decimals") or 0), 2)),
            show_counts=bool(args.get("show_counts")),
            significance=args.get("significance", True) is not False,
            bold_nets=args.get("bold_nets", True) is not False,
            charts=bool(args.get("charts")))
        meta = {"project": f"{s.client_name} ({s.id})",
                "notes": ([f"Filter: {p.filter_}"] if p.filter_ else [])
                + ([f"Weighted by {p.weight}"] if p.weight else ["Unweighted."])}
        safe = re.sub(r"\W+", "_", style.title)[:40].strip("_") or "report"
        filename = f"{safe}_{uuid4().hex[:6]}.xlsx"
        (p.d / "charts").mkdir(exist_ok=True)
        out = p.d / "charts" / filename
        nets = {stub: [(n.label, n.codes) for n in auto_nets(p.dm[stub])
                       + [c for c in p.custom if set(c.codes) <= set(p.dm[stub].valid_codes())]]
                for stub in p.stubs}
        if output == "refreshable":
            n = run_heavy_detached(db, refreshable_job, p.d / "data.parquet", p.dm, p.stubs,
                                   p.banner, p.filter_, nets, style, out, meta)
        else:
            mean = args.get("mean")
            plan = TabPlan(title=style.title, banner=p.banner, weight=p.weight, tables=[
                TableSpec(stub=x, filter=p.filter_,
                          nets=[n for n in auto_nets(p.dm[x])] + [
                              c for c in p.custom if set(c.codes) <= set(p.dm[x].valid_codes())],
                          mean=len(p.dm[x].valid_codes()) >= 5 if mean is None else bool(mean))
                for x in p.stubs[:MAX_WORKBOOK_TABLES]])
            n = run_heavy_detached(db, report_job, p.d / "data.parquet", plan, p.dm, style, out,
                                   meta)
        return {
            "tables": [],  # the file holds them; nothing to retype
            "tables_in_file": n, "output": output,
            "skipped_plain_numbers": p.skipped,
            "charts": [{"study_id": s.id, "filename": filename, "title": style.title,
                        "kind": "workbook"}],
            "note": f"The {output} Excel report is offered as a download under your reply: "
                    f"{n} tables, a contents sheet first. Say what it holds and the choices "
                    "it used (layout, decimals, letters, charts); do not list the tables."
                    + (" Refreshable: tell them to paste new data into the Data sheet, same "
                       "column names, codes not labels." if output == "refreshable" else ""),
        }

    def make_tables(args):
        """Any set of tables: rows x columns, filtered, grouped, weighted — one tool
        for the whole family instead of one tool per phrasing."""
        from src.engines.tabs.runner import tables_job
        from src.engines.tabs.spec import TableSpec, TabPlan, auto_nets

        p = plan_tables(args)
        s, d, dm, banner, stubs = p.s, p.d, p.dm, p.banner, p.stubs
        skipped, filter_, weight, custom, asked = p.skipped, p.filter_, p.weight, p.custom, p.asked
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
        charts = []
        if args.get("chart") and tables:
            # Drawn here, from the same rows and columns: a chart used to depend on
            # the data chat choosing to draw one, and the model said "here is the
            # chart" when it had not.
            from src.engines.analysis.charts import generate_chart

            for stub in stubs[:len(tables)]:
                drawn = generate_chart(d, dm, stub=stub, banner=banner, filter_=filter_,
                                       chart_type=str(args.get("chart_type") or "bar"))
                if drawn.get("success"):
                    charts += charts_in({**drawn, "study_id": s.id})
        return {
            "tables_made": n,
            "skipped_plain_numbers": skipped,
            "tables": tables,
            "significance_tested": any(t["significance_tested"] for t in tables) or (
                filename is not None and len(banner) > 0),
            "charts": charts + ([{"study_id": s.id, "filename": filename, "title": title,
                                  "kind": "workbook"}] if filename else []),
            "note": ("The tables are shown under your reply exactly as computed" if tables else
                     "The workbook is offered as a download under your reply, one sheet per "
                     "table with significance letters") + ". Say how many tables and what the "
                     "columns are; do not retype cells or list every table."
                     + (f" Skipped (plain numbers, no categories): {', '.join(skipped)}."
                        if skipped else ""),
        }

    # --- action tools: proposals only -----------------------------------------

    # --- phase 3: acting on Cleaning, Open ends and Reports ---------------------

    def propose_remove_respondents(args):
        """Remove the respondents one check flagged (all speeders), or named ones."""
        from api.routes.cleaning import get_cleaning
        from src.engines.qc.cleaning import REASON_CODES

        s = study(args)
        state = get_cleaning(s.id, db=db, user=user)
        if state["locked"]:
            raise _ToolError(f"cleaning is locked for {s.id}; "
                             "unlock it on the Cleaning screen first")
        flags = state["flags"]
        check = str(args.get("check") or "").strip().lower().replace(" ", "_")
        asked = [str(k) for k in args.get("keys") or []]
        if check:
            keys = [k for k, fl in flags.items() if any(f["check"] == check for f in fl)]
            if not keys:
                checks = sorted({f["check"] for fl in flags.values() for f in fl})
                raise _ToolError(f"no respondent is flagged by {check!r}. The checks that "
                                 f"flagged someone: {', '.join(checks) or 'none'}")
        elif asked:
            unknown = [k for k in asked if k not in flags]
            if unknown:
                raise _ToolError(f"not flagged, so not removed here: {', '.join(unknown[:10])}. "
                                 "Only flagged respondents can be removed from chat")
            keys = asked
        else:
            raise _ToolError("say which check (e.g. speeder) or which respondents to remove")
        keys = [k for k in keys if (state["decisions"].get(k) or {}).get("decision") != "remove"]
        if not keys:
            raise _ToolError("every one of those respondents is already removed")
        reason = check if check in REASON_CODES else str(args.get("reason_code") or "other")
        if reason not in REASON_CODES:
            raise _ToolError(f"reason_code must be one of {', '.join(REASON_CODES)}")
        note = str(args.get("note") or (f"flagged by {check}" if reason == "other" else ""))
        return propose(
            "remove_respondents", f"Remove {len(keys)} respondent{'s' * (len(keys) != 1)}"
            + (f" flagged as {check.replace('_', ' ')}" if check else ""),
            f"Reason: {reason.replace('_', ' ')}. Respondents: {', '.join(keys[:12])}"
            + (f" and {len(keys) - 12} more" if len(keys) > 12 else "")
            + ". They leave the tables when cleaning is locked and stay in the delivery file, "
              "marked removed. Reversible until then.",
            "POST", f"/studies/{s.id}/cleaning/decisions",
            {"keys": keys, "decision": "remove", "reason_code": reason,
             **({"note": note} if note else {})})

    def propose_lock_cleaning(args):
        from api.routes.cleaning import get_cleaning

        s = study(args)
        state = get_cleaning(s.id, db=db, user=user)
        if state["locked"]:
            raise _ToolError(f"cleaning is already locked for {s.id}")
        removed = sum(1 for d in state["decisions"].values() if d.get("decision") == "remove")
        return propose(
            "lock_cleaning", f"Lock cleaning for {s.client_name}",
            f"{removed} removed respondent{'s' * (removed != 1)} leave the tables, which are "
            f"rebuilt; they stay in the delivery file, marked. {state['n_unreviewed_flagged']} "
            "flagged respondents are still unreviewed and stay in.",
            "POST", f"/studies/{s.id}/cleaning/lock")

    def propose_code_open_ends(args):
        from api.routes.chat import _load_datamap, _study_dir, _unknown

        s = study(args)
        dm = _load_datamap(_study_dir(s), s)
        if dm is None:
            raise _ToolError(f"{s.id} has no data to code yet")
        wanted = [str(v) for v in args.get("variables") or []]
        if bad := [v for v in wanted if v not in dm]:
            raise _ToolError(_unknown("variable(s)", bad, dm))
        if not_text := [v for v in wanted if dm[v].type.value != "text"]:
            raise _ToolError(f"not open text, nothing to code: {', '.join(not_text)}")
        return propose(
            "code_open_ends",
            "Code " + (", ".join(wanted) if wanted else "every open-ended question"),
            "Builds a codeframe from a sample of answers and codes every answer with the "
            "project's model, in the background. Uncertain answers go to the review queue on "
            "the Open ends screen.",
            "POST", f"/studies/{s.id}/coding/run", {"variables": wanted or None})

    def propose_draft_report(args):
        from api.routes.studies import get_crosstabs

        s = study(args)
        try:
            get_crosstabs(s.id, db, user)
        except HTTPException as exc:
            raise _ToolError(f"no tables to report on yet: {exc.detail}") from exc
        return propose(
            "draft_report", f"Draft the cited report for {s.client_name}",
            "Writes the report from the findings in the background; every sentence cites the "
            "table it comes from and is checked against it. Drafting again replaces the "
            "current sentences.",
            "POST", f"/studies/{s.id}/report/draft")

    def propose_remember(args):
        """Keep a lasting preference or a client's convention, by confirmed card."""
        scope = str(args.get("scope") or "user")
        text = " ".join(str(args.get("text") or "").split())
        if scope not in ("user", "client") or len(text) < 3:
            raise _ToolError("scope is user or client, and text says what to remember")
        body = {"scope": scope, "text": text[:500]}
        whose = "you"
        if scope == "client":
            s = study(args)
            body["study_id"] = s.id
            whose = s.client_name
        return propose("remember", f"Remember for {whose}: {text[:80]}",
                       "Used in every later answer" + (" about this client's projects"
                                                       if scope == "client" else "")
                       + ". You can see and delete it under Memory in this panel.",
                       "POST", "/copilot/memories", body)

    def propose_weighting(args):
        """Rim weighting to the user's targets, previewed on the card before it is applied."""
        from api.routes.methods import WeightingRequest, weighting_preview

        s = study(args)
        targets = args.get("targets") or {}
        if not isinstance(targets, dict) or not targets:
            raise _ToolError('targets look like {"S1": {"1": 49, "2": 51}, '
                             '"S4b": {"1": 17, "2": 21, "3": 38, "4": 24}} — variable, code, share')
        try:
            body = WeightingRequest(targets={str(v): {str(c): float(x) for c, x in cells.items()}
                                             for v, cells in targets.items()},
                                    **{k: float(args[k]) for k in ("cap_low", "cap_high")
                                       if args.get(k) is not None})
        except (TypeError, ValueError, AttributeError) as exc:
            raise _ToolError(f"targets must be variable -> code -> share: {exc}") from exc
        report = weighting_preview(s.id, body, db=db, user=user)
        lines = []
        for var, cells in report["targets"].items():
            lines.append(f"{var}: " + ", ".join(
                f"{c['label']} {c['unweighted_pct']}%→{c['weighted_pct']}% "
                f"(target {c['target_pct']}%)"
                for c in cells.values()))
        detail = (f"Efficiency {report['efficiency_pct']}% (effective sample "
                  f"{report['effective_n']} of {report['n']}); weights {report['min_weight']}–"
                  f"{report['max_weight']}" + (", converged" if report["converged"]
                                               else ", DID NOT fully converge")
                  + f". {'; '.join(lines)}. Adds dvv_weight to the data; tables use it when "
                  "asked for weighted figures.")
        card = propose("weighting", f"Weight {s.client_name} to {', '.join(body.targets)}",
                       detail, "POST", f"/studies/{s.id}/weighting", body.model_dump())
        return {**card, "preview": report}

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
            body["options"] = _options_from(args["options"])
        for flag in ("required", "randomize_options"):
            if isinstance(args.get(flag), bool):
                body[flag] = args[flag]
        # Conditions in plain words ("QAGE < 18"), compiled and checked against
        # this questionnaire; "none" clears one.
        for field in ("show_if", "terminate_if"):
            if (said := str(args.get(field) or "").strip()):
                if said.lower() in ("none", "clear", "remove", "-"):
                    body[field] = None
                else:
                    try:
                        body[field] = compile_condition(said, definition).to_dict()
                    except ValueError as exc:
                        raise _ToolError(f"{field}: {exc}") from exc
        bounds = {k: args[k] for k in ("min", "max") if isinstance(args.get(k), (int, float))}
        if bounds:
            body["validation"] = bounds
        if not body:
            raise _ToolError("name what to change: text, type, options, required, "
                             "randomize_options, show_if, terminate_if, min or max")

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
                return "; ".join(
                    f"{o.code}={o.label}" + (" [exclusive]" if o.exclusive else "")
                    + (" [anchor]" if o.anchored else "") for o in value or []) or "(none)"
            if field in ("show_if", "terminate_if"):
                return describe_condition(value) if value is not None else "(none)"
            if field == "validation":
                v = value or {}
                return f"{v.get('min', '…')} to {v.get('max', '…')}" if v else "(none)"
            return getattr(value, "value", value) if value is not None else "(none)"

        diff = [{"field": field, "before": str(shown(field, q)),
                 "after": str(shown(field, trial[q.qid]))} for field in body]
        body["base_fingerprint"] = question_fingerprint(q)
        return propose(
            "edit_question", f"Change {q.qid}: {', '.join(d['field'] for d in diff)}",
            f"Saved to the questionnaire under review; {q.qid} goes back to review. If anyone "
            f"changes {q.qid} before you confirm, this card is refused.",
            "PATCH", f"/studies/{s.id}/questionnaire/questions/{q.qid}", body, diff)

    def propose_add_hidden_variable(args):
        """A variable the script computes and never asks: bands, each with the
        condition that puts a respondent in it, compiled and checked here."""
        s, _row, definition = current(args)
        name = re.sub(r"[^A-Za-z0-9_]", "_", str(args.get("name") or "").strip()).upper()
        if not name:
            raise _ToolError("give the variable a name, e.g. HID_AGE_BANDS")
        if not name.startswith(("HID_", "HIDDEN_")):
            name = f"HID_{name}"
        if name in {q.qid.upper() for q in definition.questions}:
            raise _ToolError(f"{name} already exists; edit it instead")
        bands = args.get("bands") or []
        options = []
        for i, band in enumerate(bands, 1):
            if isinstance(band, dict):
                code, label, rule = band.get("code") or i, band.get("label"), band.get("when")
            else:
                m = re.match(r"^\s*(?:(\d+)\s*=\s*)?(.+?)\s*:\s*(.+)$", str(band))
                if not m:
                    raise _ToolError(f"write each band as \"code=label: condition\", e.g. "
                                     f"\"1=18-34: QAGE between 18 and 34\" (got {band!r})")
                code, label, rule = m.group(1) or i, m.group(2), m.group(3)
            try:
                when = compile_condition(str(rule), definition)
            except ValueError as exc:
                raise _ToolError(f"band {label!r}: {exc}") from exc
            options.append({"code": int(code), "label": str(label).strip(),
                            "derive_if": when.to_dict()})
        if not options:
            raise _ToolError("give at least one band: \"code=label: condition\"")
        text = str(args.get("label") or name).strip()
        return propose(
            "add_question", f"Add hidden variable {name}",
            "Computed for every respondent, never asked: "
            + "; ".join(f"{o['code']}={o['label']} when {b}" for o, b in
                        zip(options, [str(x.get("when") if isinstance(x, dict) else
                                          str(x).split(":", 1)[1]).strip() for x in bands],
                            strict=True))
            + ". It is added at the end of the questionnaire and goes to review.",
            "POST", f"/studies/{s.id}/questionnaire/questions",
            {"qid": name, "text": text, "type": "info", "options": options,
             "required": False, "hidden": True})

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
        "question_label": "is how this format writes a question label",
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

    def _propose_label(s, row, slug: str, example: str):
        """Teach where questions start, previewed on the document itself.

        The card says how many questions the archived document splits into
        today and with this label, so the owner confirms a measured change —
        and a label the document does not use is refused before it is saved.
        """
        import tempfile
        from pathlib import Path

        from api.db import taught_question_labels
        from api.routes import questionnaire as qroutes
        from src.intake.questionnaire_loader import segment_into_questions

        if not row.source_file_b64:
            raise _ToolError("the document was not archived for this study, so a label "
                             "cannot be checked against it; upload it again")
        with tempfile.TemporaryDirectory() as folder:
            path = qroutes._archived_source(db, row, Path(folder))
            blocks = qroutes.load_pdf(path) if path.suffix == ".pdf" else qroutes.load_docx(path)
        known = taught_question_labels(db)
        before = segment_into_questions(blocks, known)
        after = segment_into_questions(blocks, [*known, example])
        labelled = lambda chunks: [c.candidate_qid for c in chunks if c.candidate_qid]  # noqa: E731
        now, then = labelled(before), labelled(after)
        if len(then) <= len(now):
            raise _ToolError(
                f"\"{example}\" does not find any more questions in this document "
                f"({len(now)} either way). It needs at least 3 lines that start the same "
                "way; give a label copied exactly as the document writes it.")
        gained = [q for q in then if q not in now]
        return propose(
            "teach", f"Teach how questions start: labels written like \"{example}\"",
            f"This document splits into {len(now)} labelled questions today and "
            f"{len(then)} with it (new: {', '.join(gained[:12])}"
            f"{'…' if len(gained) > 12 else ''}). Confirm saves it and reads this "
            "questionnaire again from the document as a new version — the current one "
            "and its approvals are kept. Any later document writing labels this way is "
            "read the same way.",
            "POST", f"/profiles/{slug}/rules",
            {"pattern_kind": "question_label", "pattern": example, "scope": "profile",
             "study_id": s.id})

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
        if kind == "question_label":
            return _propose_label(s, row, slug, pattern)
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
        "propose_add_hidden_variable": propose_add_hidden_variable,
        "study_brief": study_brief,
        "field_report": field_report,
        "soft_launch_check": soft_launch_check,
        "dry_run": dry_run,
        "field_projection": field_projection,
        "question_graph": question_graph,
        "search_project": search_project,
        "ask_data": ask_data,
        "make_tables": make_tables,
        "excel_report": excel_report,
        "cleaning_status": screen(_route("cleaning", "get_cleaning")),
        "data_vs_questionnaire": screen(_route("studies", "get_crosscheck")),
        "fieldwork_from_data": screen(_route("studies", "data_field_report_route")),
        "project_lifecycle": screen(_route("lifecycle", "get_lifecycle")),
        "analysis_methods": screen(_route("methods", "method_inputs")),
        "vendor_scorecard": screen(_route("vendors", "vendor_scorecard"),
                                   UserRole.OWNER, UserRole.ANALYST),
        "ledger_check": screen(_route("platform", "verify_ledger")),
        "weighting_status": screen(_route("methods", "weighting_status")),
        "report_findings": report_findings,
        "variable_plan": variable_plan,
        "open_end_coding": open_end_coding,
        "compliance_check": compliance_check,
        "run_method": run_method,
        "team_and_costs": team_and_costs,
        "data_variables": data_variables,
        "product_help": product_help,
        "propose_open_link": propose_open_link,
        "propose_weighting": propose_weighting,
        "propose_remember": propose_remember,
        "propose_remove_respondents": propose_remove_respondents,
        "propose_lock_cleaning": propose_lock_cleaning,
        "propose_code_open_ends": propose_code_open_ends,
        "propose_draft_report": propose_draft_report,
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


#: Messages kept word for word; older ones (up to _EARLIER) become one summary.
_RECENT = 6
_EARLIER = 40


def _history(db: Session, conversation_id: str) -> list[dict]:
    """The recent turns verbatim, and what came before as one compact summary —
    a long session no longer sends twelve full answers with every question."""
    rows = list_copilot_messages(db, conversation_id)[-(_RECENT + _EARLIER):]
    older, recent = rows[:-_RECENT], rows[-_RECENT:]
    out = []
    if older:
        lines = []
        for m in older:
            text = " ".join((m.content or "").split())
            if m.role == "user":
                lines.append(f"- asked: {text[:160]}")
            else:
                tools = ", ".join(x.get("tool", "") for x in m.lookups or [])
                lines.append(f"  answered: {text[:220]}"
                             + (f" [looked up: {tools}]" if tools else ""))
        out = [{"role": "user", "content": "Earlier in this conversation (summarised; look "
                "anything up again before relying on it):\n" + "\n".join(lines)},
               {"role": "assistant", "content": "Understood."}]
    return out + [{"role": m.role,
                   "content": (m.content or "")[:3900] + (_lookups_note(m.lookups)
                                                          if m.role == "assistant" else "")}
                  for m in recent]


_INTENTS = (
    ("a figure (count, share, average)", r"\bhow many|\bcount\b|share|percent|%|average|mean\b"),
    ("a table or crosstab", r"table|crosstab|cross.?tab|\bby\b|breakdown|banner"),
    ("an explanation (why / how it works)", r"\bwhy\b|\bhow (?:is|does|do|was)\b|explain|mean"),
    ("a change to be prepared (card)", r"\b(change|edit|fix|remove|delete|add|set|weight|"
                                       r"open|close|hold|release|approve|lock|code)\b"),
    ("a file (Excel / report)", r"excel|workbook|report|deck|export|download|file"),
    ("a check (is it right / does it match)", r"\b(check|verify|match|correct|identified|"
                                              r"missing|broken|valid)\b"),
)
_STOP = frozenset("what which where when this that with from have does into they them their "
                  "there about show tell give make please could would should data project "
                  "question questions variable variables survey".split())


def _understood(db: Session, study, message: str) -> str:
    """What THIS message is about, resolved against this project: the questions and
    variables it names (with what they are) and what kind of answer it wants — so the
    model answers the question asked instead of a generic one. No model; never fatal."""
    from api.routes.chat import _load_datamap, _study_dir, by_label

    try:
        dm = _load_datamap(_study_dir(study), study)
        qs = list_questionnaires_for_study(db, study.id)
        questions = {}
        if qs and qs[0].definition_json:
            questions = {q.get("qid"): q for q in json.loads(qs[0].definition_json).get(
                "questions") or [] if q.get("qid")}
        named, lines = set(), []
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_.]*\d[A-Za-z0-9_]*|[A-Za-z_]{2,}", message):
            for qid in questions:
                if qid.lower() == token.lower() and qid not in named:
                    q = questions[qid]
                    named.add(qid)
                    lines.append(f"- {qid}: questionnaire question ({q.get('type')}) — "
                                 f"\"{(q.get('text') or '')[:90]}\"")
            if dm is not None:
                for v in dm.variables:
                    if v.name.lower() == token.lower() and v.name not in named:
                        named.add(v.name)
                        lines.append(f"- {v.name}: data variable ({v.type.value}, "
                                     f"{len(v.valid_codes())} codes) — \"{(v.label or '')[:90]}\"")
        if dm is not None and not named:  # words, not names: "region", "age band"
            for word in {w.lower() for w in re.findall(r"[A-Za-z]{4,}", message)} - _STOP:
                hits = [h for h in by_label(dm, word) if dm[h].valid_codes()][:3]
                if 0 < len(hits) <= 3:
                    lines.append(f"- \"{word}\" may mean: " + ", ".join(
                        f"{h} ({dm[h].label[:40]})" for h in hits))
        wants = [label for label, pattern in _INTENTS if re.search(pattern, message, re.I)]
        if not lines and not wants:
            return ""
        return ("What this message is about (resolved against the project — answer THIS, "
                "specifically, naming these):\n" + "\n".join(lines[:8])
                + (f"\n- Wants: {'; '.join(wants[:3])}" if wants else ""))
    except Exception:  # noqa: BLE001 - grounding help only; the answer must not fail on it
        logger.exception("understanding the ask for %s", study.id)
        return ""


def _project_card(db: Session, study) -> str:
    """A compact picture of the project in view, put in front of every question,
    so the model starts from where the project stands instead of rediscovering it.
    Built from what is stored — no model, no heavy work — and never fatal."""
    import polars as pl

    from api.routes.chat import _load_datamap, _study_dir
    from src.orchestration.orchestrator import suggest_banner_candidates

    lines = [f"Project card — {study.client_name} ({study.id}), status {study.status}:"]
    try:
        qs = list_questionnaires_for_study(db, study.id)
        if qs and qs[0].definition_json:
            survey = json.loads(qs[0].definition_json)
            questions = survey.get("questions") or []
            hidden = sum(1 for q in questions
                         if "hidden" in f"{q.get('text', '')} {q.get('qid', '')}".lower())
            lines.append(f"- Questionnaire: {qs[0].status}, {len(questions)} questions"
                         + (f", {hidden} hidden/derived" if hidden else ""))
        else:
            lines.append("- Questionnaire: none uploaded")
        d = _study_dir(study)
        dm = _load_datamap(d, study)
        if dm is not None and (d / "data.parquet").exists():
            n = pl.scan_parquet(d / "data.parquet").select(pl.len()).collect().item()
            banners = ", ".join(f"{v} ({dm[v].label[:28]})"
                                for v in suggest_banner_candidates(dm, 5))
            lines.append(f"- Data: {n} respondents, {len(dm)} variables"
                         + (f" from {study.source_filename}" if study.source_filename else ""))
            if banners:
                lines.append(f"- Likely banner variables: {banners}")
        else:
            lines.append("- Data: none processed yet")
        if study.crosscheck_json:
            match = (json.loads(study.crosscheck_json).get("variable_match") or {}).get(
                "summary") or {}
            if match:
                lines.append(f"- Data vs questionnaire (last run): {match.get('rules_broken', 0)} "
                             f"rules broken, {match.get('missing_in_data', 0)} questions not in "
                             f"the data, {match.get('rules_met', 0)} rules met")
        lines.append(f"- Tables: {'built' if study.excel_path else 'not built yet'}")
        if w := (study.study_metadata or {}).get("weighting"):
            lines.append(f"- Weighting: applied (dvv_weight, rim on {', '.join(w['targets'])}, "
                         f"efficiency {w['report']['efficiency_pct']}%) — pass weight "
                         "dvv_weight for weighted tables")
        else:
            lines.append("- Weighting: none (tables are unweighted)")
    except Exception:  # noqa: BLE001 - a card is a convenience; the answer must not fail on it
        logger.exception("project card for %s", study.id)
    return "\n".join(lines)


@router.post("/copilot", response_model=CopilotResponse)
def copilot(
    body: CopilotRequest,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """Ask the copilot anything about the platform. It may prepare actions,
    never perform them. Each exchange is saved to the user's conversation."""
    return _answer(body, db, user)


@router.post("/copilot/stream")
def copilot_stream(
    body: CopilotRequest,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """The same answer, with each step announced as it starts (server-sent
    events): `step` {label} while it works, then `done` {the CopilotResponse},
    or `error` {status, detail}. Up to a minute of silence read as broken."""
    import queue
    import threading

    from fastapi.responses import StreamingResponse
    from sqlalchemy.orm import sessionmaker

    # Its own session and a plain copy of the user: the request's session may be
    # closed before the stream finishes.
    own_session = sessionmaker(bind=db.get_bind(), autoflush=False)
    me = UserDB(id=user.id, email=user.email, name=user.name, role=user.role,
                is_active=user.is_active)
    events: queue.Queue = queue.Queue()

    def work() -> None:
        session = own_session()
        try:
            answer = _answer(body, session, me,
                             on_step=lambda label: events.put(("step", {"label": label})))
            events.put(("done", answer.model_dump()))
        except HTTPException as exc:
            events.put(("error", {"status": exc.status_code, "detail": exc.detail}))
        except Exception:  # noqa: BLE001 - the browser gets a sentence, the log the trace
            logger.exception("copilot stream failed")
            events.put(("error", {"status": 500, "detail": "The copilot failed on the server."}))
        finally:
            session.close()
            events.put(None)

    threading.Thread(target=work, daemon=True).start()

    def stream():
        yield ": started\n\n"  # past any proxy buffer at once
        while (item := events.get()) is not None:
            kind, data = item
            yield f"event: {kind}\ndata: {json.dumps(data, default=str)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _answer(body: CopilotRequest, db: Session, user: UserDB, on_step=None) -> CopilotResponse:
    from src.ai.copilot import TOOL_NAMES
    from src.ai.copilot_router import areas_for, is_heavy, label_for, route

    conversation = None
    if body.conversation_id:
        conversation = get_copilot_conversation(db, body.conversation_id, user.id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="No such conversation")
        history = _history(db, conversation.id)
    else:
        history = [t.model_dump() for t in body.history]
    execute_tool, proposals = make_executor(db, user)
    steps: list[dict] = []

    def execute(name: str, args: dict) -> dict:
        """Each step timed and kept, so a wrong answer can be explained from
        Admin instead of being rebuilt on a laptop."""
        if on_step:
            on_step(label_for(name))
        started = time.perf_counter()
        out = execute_tool(name, args)
        steps.append({"tool": name, "ok": out.get("success") is not False,
                      "ms": round((time.perf_counter() - started) * 1000),
                      "chars": len(json.dumps(out, default=str)),
                      **({"error": str(out.get("error"))[:300]}
                         if out.get("success") is False else {})})
        return out

    if body.images:
        from src.ai.vision import with_screenshots

        # The transcript becomes part of what the user said: every tool and rule
        # applies to it, and later turns still know what the screenshot showed.
        body = body.model_copy(update={
            "message": with_screenshots(body.message, body.images, on_step), "images": []})

    page = ""
    if body.study_id and (current := get_study(db, body.study_id)):
        page = f"The user is looking at project {current.id} ({current.client_name}).\n"
        page += _project_card(db, current) + "\n"
        if about := _understood(db, current, body.message):
            page += about + "\n"
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

    client = current.client_name if body.study_id and current else None
    if remembered := list_copilot_memories(db, user.id, client)[-20:]:
        page += "\nRemembered — apply unless the user says otherwise, and say when you do:\n" + \
            "\n".join(f"- [{'you' if m['scope'] == 'user' else m['client']}] {m['text'][:200]}"
                      for m in remembered)
    earlier = [x.get("tool", "") for m in (list_copilot_messages(db, conversation.id)[-2:]
                                          if conversation else [])
               for x in m.lookups or []]
    screen = body.screen or ""
    offered = route(body.message, sorted(TOOL_NAMES), screen, earlier)
    areas = areas_for(body.message, screen, earlier)
    heavy = is_heavy(body.message, areas)
    tagged = usage.purpose.set("copilot")  # this request's model calls count as copilot
    totals = {"model_calls": 0, "tokens_in": 0, "tokens_out": 0}
    counting = usage.tally.set(totals)
    started = time.perf_counter()
    try:
        usage.count_question()
        reply, results, answered_by = run_copilot(
            body.message, history, execute,
            user=user.name or user.email, role=user.role, page=page, offered=offered,
            areas=areas, heavy=heavy)
    except BackendUnavailable as exc:
        logger.warning(f"copilot: no backend ({exc})")
        raise HTTPException(status_code=503,
                            detail="The copilot can't reach an AI model right now.") from exc
    finally:
        usage.purpose.reset(tagged)
        usage.tally.reset(counting)
    trace = {"ms": round((time.perf_counter() - started) * 1000), **totals,
             "steps": steps, "page": body.study_id, "focus_qid": body.focus_qid,
             "screen": screen, "areas": sorted(areas), "tools_offered": len(offered),
             "heavy": heavy, "history_messages": len(history)}
    # The history shows the model "[Looked up for this answer: …]" after each
    # earlier reply; it sometimes copies that line into its own. Not for the user.
    reply = re.sub(r"\n*\[Looked up for this answer:[^\]]*\]", "", reply).strip()
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
    # A tool that needs a decision first: shown as options the user can click.
    questions = [q for r in results[-3:] if r.get("success") is not False
                 for q in r.get("needs_answers") or [] if isinstance(q, dict)][:6]
    conversation, seq = save_copilot_exchange(
        db, conversation, user.id, body.study_id, body.message, reply, lookups,
        [c.model_dump() for c in unique], answered_by, tables, charts, trace)
    return CopilotResponse(reply=reply, proposals=unique, seq=seq, questions=questions,
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


@router.get("/copilot/memories")
def copilot_memories(
    study_id: str | None = None,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """What the Copilot remembers for you, and for the client of `study_id`."""
    s = get_study(db, study_id) if study_id else None
    return list_copilot_memories(db, user.id, s.client_name if s else None)


@router.post("/copilot/memories", status_code=201)
def remember(
    body: CopilotMemoryRequest,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """Keep a preference (yours) or a convention (a client's: owner or analyst)."""
    if body.scope == "user":
        return add_copilot_memory(db, "user", body.text, user.name or user.email,
                                  user_id=user.id)
    if not role_permits(user, UserRole.OWNER, UserRole.ANALYST):
        raise HTTPException(status_code=403, detail="Only an owner or analyst can set what "
                                                    "the Copilot remembers about a client")
    s = get_study(db, body.study_id or "")
    if not s:
        raise HTTPException(status_code=404, detail="A client memory needs the project it is for")
    return add_copilot_memory(db, "client", body.text, user.name or user.email,
                              client=s.client_name)


@router.delete("/copilot/memories/{memory_id}", status_code=204)
def forget(
    memory_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    m = get_copilot_memory(db, memory_id)
    mine = m is not None and m.scope == "user" and m.user_id == user.id
    clients = m is not None and m.scope == "client" and role_permits(
        user, UserRole.OWNER, UserRole.ANALYST)
    if not (mine or clients):  # someone else's reads the same as none
        raise HTTPException(status_code=404, detail="No such memory")
    delete_copilot_memory(db, m)


@router.get("/admin/copilot-answers")
def copilot_answers(
    show: Literal["all", "flagged", "errors", "slow"] = "all",
    limit: int = 100,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.FOUNDER)),
):
    """Recent Copilot answers with how each was made. (Owner/Founder)"""
    return list_copilot_answers(db, show, max(1, min(limit, 500)))


@router.get("/admin/copilot-evals")
def copilot_evals(
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.FOUNDER)),
):
    """The eval's score history, newest first. (Owner/Founder)"""
    return list_copilot_evals(db)


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

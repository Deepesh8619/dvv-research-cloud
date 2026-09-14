"""Study navigator endpoint — a tool-using agent scoped to ONE study.

Every request is scoped to one study only: the context contains only that
study's DataMap, computed tables, and metadata; tool execution reads only
that study's files. The LLM (agent loop in src/ai/navigator.py) decides
which tools to call; every number comes from the deterministic engines
(crosstab, DuckDB), never from the model (CLAUDE.md §4.4).
"""

import json
import logging
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.auth import get_current_user
from api.config import STUDIES_DIR
from api.db import (
    UserDB,
    create_chat_message,
    get_db,
    get_study,
    list_chat_messages,
)
from api.intent_router import build_routed_context
from api.models import ChatMessage, ChatRequest, ChatResponse, ChatToolCall
from src.ai.navigator import navigate_basic, navigate_cerebras, navigate_groq
from src.ai.router import BackendUnavailable, ModelOutputError
from src.ai.specialists import specialist_for_intent
from src.core.models import DataMap
from src.core.survey_definition import QuestionVariableLink, SurveyDefinition
from src.engines.analysis.charts import generate_chart
from src.engines.analysis.codegen import generate_code
from src.engines.analysis.pivot import generate_pivot
from src.engines.analysis.quality import data_quality
from src.engines.qc.respondents import respondent_qc_report
from src.engines.questionnaire.linker import (
    RoutingValidation,
    cross_validate_routing,
    link_questions_to_datamap,
)
from src.engines.questionnaire.navigator_tools import (
    explain_question_base,
    get_question_context,
    get_question_details,
    search_questionnaire,
)
from src.engines.tabs.crosstab import crosstab
from src.engines.tabs.runner import table_to_dict
from src.engines.tabs.spec import auto_nets

logger = logging.getLogger(__name__)
router = APIRouter()

# --- SQL sandbox ---
_SQL_ALLOWED_RE = re.compile(r"^\s*(SELECT|WITH)\s", re.IGNORECASE)
_SQL_DISALLOWED_RE = re.compile(
    r"\b(DROP|DELETE|INSERT|UPDATE|ALTER|CREATE|EXEC|EXECUTE|TRUNCATE|REPLACE"
    r"|LOAD|ATTACH|COPY|IMPORT|EXPORT|PRAGMA|VACUUM|INSTALL)\b",
    re.IGNORECASE,
)


def _run_sql(parquet_path: Path, sql: str, limit: int = 100) -> dict:
    """Run a read-only SELECT against the study's respondent data."""
    import duckdb

    if not _SQL_ALLOWED_RE.match(sql) or _SQL_DISALLOWED_RE.search(sql):
        return {"success": False, "error": "Only read-only SELECT queries are allowed"}
    if not parquet_path.exists():
        return {"success": False, "error": "Respondent data not available for this study"}

    con = duckdb.connect()
    try:
        con.execute("SET memory_limit = '256MB'")
        con.execute("SET threads = 2")
        con.execute(f"CREATE VIEW data AS SELECT * FROM read_parquet('{parquet_path}')")
        frame = con.execute(sql.strip().rstrip(";")).fetchdf()
        return {
            "success": True,
            "columns": list(frame.columns),
            "rows": frame.head(limit).to_dict(orient="records"),
            "row_count": len(frame),
            "truncated": len(frame) > limit,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    finally:
        con.close()


# --- Study context ---


def _study_dir(study) -> Path:
    """The orchestrator run directory (data.parquet, crosstabs.json, datamap.json).

    Located via the rendered deck's parent — the upload directory under
    STUDIES_DIR/{study_id} holds only the source file.
    """
    if study.excel_path:
        return Path(study.excel_path).parent
    return STUDIES_DIR / study.id


def _load_datamap(study_dir: Path, study=None) -> DataMap | None:
    """DataMap from the run dir, falling back to the durable DB copy.

    The run dir lives on Render's ephemeral disk — a deploy/restart wipes it.
    `studies.datamap_json` (written at pipeline completion) keeps the variable
    metadata alive so the navigator still knows the study's structure after a
    restart, even when the parquet is gone. File wins when both exist (it may
    carry a fresher label merge... though retab persists to both eventually)."""
    path = study_dir / "datamap.json"
    if path.exists():
        try:
            return DataMap.from_json(path.read_text())
        except Exception:
            pass
    if study is not None and getattr(study, "datamap_json", None):
        try:
            return DataMap.from_json(study.datamap_json)
        except Exception:
            pass
    return None


def _load_deck(study_dir: Path) -> dict:
    path = study_dir / "crosstabs.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _load_approved_survey(study_id: str | None) -> SurveyDefinition | None:
    """Load a study's approved questionnaire SurveyDefinition, or None.

    Reuses the questionnaire route's storage convention: the QuestionnaireDB row
    carries the on-disk path to the SurveyDefinition JSON (via
    list_questionnaires_for_study). We only surface an APPROVED questionnaire to
    the navigator — a draft/in-review instrument is still being corrected, so the
    Steward should treat it as "not available yet" rather than quote unreviewed
    extractions. Returns None (never raises) for every degraded case — no
    study_id, no questionnaire, none approved, missing/corrupt file — so the
    Steward tools can report a clean "not available" instead of a stack trace.
    """
    if not study_id:
        return None
    from api.db import SessionLocal, list_questionnaires_for_study

    db = SessionLocal()
    try:
        questionnaires = list_questionnaires_for_study(db, study_id)
    except Exception:
        return None
    finally:
        db.close()

    for questionnaire in questionnaires:  # most-recent first
        if questionnaire.status != "approved" or not questionnaire.definition_path:
            continue
        path = Path(questionnaire.definition_path)
        if not path.exists():
            continue
        try:
            return SurveyDefinition.from_json(path.read_text())
        except Exception:
            return None
    return None


MAX_CONTEXT_VARS = 150
MAX_VALUE_LABELS = 10
BASIC_MAX_VARS = 25


def _question_keywords(question: str, datamap: DataMap) -> set[str]:
    """Keywords for relevance scoring.

    Keep general words >2 chars, PLUS any token that exactly matches a variable
    name regardless of length. Survey variable names are short (`S2`, `C2`,
    `Q5`) and tokenize to ≤2 chars — a plain `len>2` filter silently drops them,
    so a question explicitly naming `S2` never pulls S2 into context and the
    model wrongly reports it "not defined." Matching real names fixes that
    without admitting noise words like "of"/"is".
    """
    tokens = re.findall(r"[a-z0-9]+", question.lower())
    varnames = {v.name.lower() for v in datamap.variables}
    return {t for t in tokens if len(t) > 2 or t in varnames}


def _rank_variables(datamap: DataMap, question: str, limit: int) -> list:
    """Variables most relevant to the question, padded with leading vars.

    Exact variable-name matches are weighted heavily so a directly-named
    variable is never crowded out of the top `limit` by incidental label hits.
    """
    words = _question_keywords(question, datamap)

    def score(v) -> int:
        name_hit = 10 if v.name.lower() in words else 0
        hay = f"{v.label or ''} {' '.join(v.values.values())}".lower()
        return name_hit + sum(1 for w in words if w in hay)

    ranked = sorted(datamap.variables, key=score, reverse=True)
    chosen = [v for v in ranked if score(v) > 0][:limit]
    if len(chosen) < limit:
        seen = {v.name for v in chosen}
        chosen += [v for v in datamap.variables if v.name not in seen][: limit - len(chosen)]
    return chosen


def _basic_context(study, datamap: DataMap | None, deck: dict, question: str) -> str:
    """Compact context for the local 3B fallback model.

    A 150-variable context drowns a small model, so keep only the variables
    relevant to the question (plus leading vars as anchors when nothing matches).
    """
    lines = [f"Project: {study.client_name}."]
    if deck:
        lines.append(
            "Computed tables: "
            + "; ".join(f"{t['id']}={t['title']}" for t in deck.get("tables", [])[:15])
        )

    if datamap:
        lines.append("Variables (name [type] label | code=label):")
        for v in _rank_variables(datamap, question, BASIC_MAX_VARS):
            entry = f"  {v.name} [{v.type}] {v.label or ''}"
            if v.values:
                entry += " | " + " ".join(
                    f"{c}={label}" for c, label in list(v.values.items())[:8]
                )
            lines.append(entry)
    return "\n".join(lines)




# --- Tool execution ---


def _parse_filter(
    filter_: dict | None, datamap: DataMap
) -> tuple[dict[str, list[int]] | None, str | None]:
    """Validate + coerce a {var: [codes]} filter dict. Returns (filter, error)."""
    if not filter_:
        return None, None
    try:
        coerced = {var: [int(c) for c in codes] for var, codes in filter_.items()}
    except (TypeError, ValueError):
        return None, "Filter codes must be integers"
    bad = [v for v in coerced if v not in datamap]
    if bad:
        return None, f"Unknown filter variable(s): {bad}"
    return coerced, None


def _make_executor(
    study_dir: Path, datamap: DataMap | None, deck: dict, study_id: str | None = None
):
    """study_id is optional (defaults to None, kept for existing test call sites
    that construct an executor directly from a study_dir without a DB-backed
    study) — only used to stamp generate_chart results with the id the frontend
    needs to build the chart's serving URL (/studies/{study_id}/charts/{filename}).
    study_dir's own name is NOT a substitute: it's the orchestrator's run_id
    (STUDIES_DIR/study_{date}_{hex}), not the upload-time study_id.
    """
    parquet = study_dir / "data.parquet"

    def _live_table(params: dict) -> dict:
        if datamap is None:
            return {"success": False, "error": "Datamap unavailable — cannot compute new tables"}
        stub = params.get("stub", "")
        if stub not in datamap:
            return {"success": False, "error": f"Unknown variable: {stub}"}
        # No default banner (product decision): a table is Total-only unless
        # the analyst's question named a comparison group, so the model
        # supplied one explicitly. Silently substituting a study-wide default
        # here used to staple irrelevant columns onto unrelated questions —
        # e.g. a Country crosstab getting Vehicle-Purchase-Intent columns
        # because that's what auto_tab_plan happened to guess.
        banner = params.get("banner") or []
        bad = [b for b in banner if b not in datamap]
        if bad:
            return {"success": False, "error": f"Unknown banner variable(s): {bad}"}
        filter_, err = _parse_filter(params.get("filter"), datamap)
        if err:
            return {"success": False, "error": err}
        try:
            result = crosstab(
                parquet,
                stub=stub,
                banner=banner,
                datamap=datamap,
                nets=auto_nets(datamap[stub]) or None,
                show_mean=bool(params.get("mean", len(datamap[stub].valid_codes()) >= 5)),
                filter_=filter_,
            )
        except Exception as exc:
            return {"success": False, "error": str(exc)}
        return {"success": True, "table": table_to_dict(result, "live")}

    def _chart(params: dict) -> dict:
        if datamap is None:
            return {"success": False, "error": "Datamap unavailable — cannot compute chart"}
        stub = params.get("stub", "")
        if stub not in datamap:
            return {"success": False, "error": f"Unknown variable: {stub}"}
        filter_, err = _parse_filter(params.get("filter"), datamap)
        if err:
            return {"success": False, "error": err}
        result = generate_chart(
            study_dir,
            datamap,
            stub=stub,
            banner=params.get("banner") or [],
            chart_type=params.get("chart_type", "bar"),
            filter_=filter_,
        )
        # Frontend builds the chart's serving URL from study_id (see
        # api/routes/studies.py get_chart) — generate_chart itself has no
        # notion of study_id, so stamp it on here where it's available.
        if result.get("success") and study_id:
            result["study_id"] = study_id
        return result

    def _pivot(params: dict) -> dict:
        return generate_pivot(
            parquet,
            rows=params.get("rows", ""),
            columns=params.get("columns", ""),
            values=params.get("values"),
            aggfunc=params.get("aggfunc", "count"),
        )

    def _codegen(params: dict) -> dict:
        sql = params.get("sql")
        run_sql = (lambda q: _run_sql(parquet, q)) if sql else None
        return generate_code(
            datamap,
            language=params.get("language", ""),
            question=params.get("question", ""),
            sql=sql,
            run_sql=run_sql,
        )

    def _quality(params: dict) -> dict:
        return data_quality(parquet, variables=params.get("variables"))

    def _respondent_quality(params: dict) -> dict:
        if not parquet.exists():
            return {
                "success": False,
                "tool": "check_respondent_quality",
                "error": "Respondent data not available for this study",
            }
        if datamap is None:
            return {
                "success": False,
                "tool": "check_respondent_quality",
                "error": "No DataMap available for this study",
            }
        try:
            import polars as pl

            df = pl.read_parquet(parquet)
        except Exception as exc:  # noqa: BLE001 - surface, never stack-trace to LLM
            return {"success": False, "tool": "check_respondent_quality", "error": str(exc)}
        return respondent_qc_report(df, datamap)

    # Approved questionnaire is loaded lazily and cached for this executor: most
    # questions never touch it, and a study rarely has one at all, so we avoid a
    # DB hit + disk read unless a questionnaire tool is actually called.
    survey_cache: dict[str, SurveyDefinition | None] = {}

    def _survey() -> SurveyDefinition | None:
        if "survey" not in survey_cache:
            survey_cache["survey"] = _load_approved_survey(study_id)
        return survey_cache["survey"]

    # Question<->variable links are computed lazily and cached alongside the
    # survey: linking is pure/deterministic but touches every variable, so we
    # only pay for it when a questionnaire tool that needs it is actually called.
    # Guards for a missing survey OR datamap by caching None (never raises).
    links_cache: dict[str, list[QuestionVariableLink] | None] = {}

    def _links() -> list[QuestionVariableLink] | None:
        if "links" not in links_cache:
            survey = _survey()
            if survey is None or datamap is None:
                links_cache["links"] = None
            else:
                links_cache["links"] = link_questions_to_datamap(survey, datamap).links
        return links_cache["links"]

    # cross_validate_routing runs a DuckDB query over data.parquet; it computes
    # the whole survey's routing checks in one pass. Cache that whole-survey list
    # once per executor so check_question_routing(qid) is a dict lookup, not a
    # fresh DuckDB query on every call.
    routing_cache: dict[str, dict[str, RoutingValidation]] = {}

    def _routing_validations() -> dict[str, RoutingValidation]:
        if "by_qid" not in routing_cache:
            survey = _survey()
            links = _links()
            if survey is None or not links:
                routing_cache["by_qid"] = {}
            else:
                validations = cross_validate_routing(survey, links, parquet)
                routing_cache["by_qid"] = {v.qid: v for v in validations}
        return routing_cache["by_qid"]

    def _check_question_routing(params: dict) -> dict:
        qid = params.get("qid", "")
        survey = _survey()
        if survey is None:
            return {
                "success": False,
                "available": False,
                "tool": "check_question_routing",
                "study_id": study_id or "",
                "message": (
                    "No approved questionnaire is available for this study, so "
                    "routing cannot be cross-checked against the data."
                ),
            }
        if qid not in survey:
            return {
                "success": False,
                "available": True,
                "tool": "check_question_routing",
                "study_id": study_id or "",
                "qid": qid,
                "message": f"Question '{qid}' is not in this study's approved questionnaire.",
                "known_qids": [q.qid for q in survey.questions],
            }
        validation = _routing_validations().get(qid)
        if validation is None:
            # No show_if, no resolved link, or no respondent data yet — the
            # question simply wasn't cross-checkable. cross_validate_routing
            # already skips these; report a clean "not available" for this qid.
            return {
                "success": True,
                "available": True,
                "checked": False,
                "tool": "check_question_routing",
                "study_id": study_id or "",
                "qid": qid,
                "message": (
                    "Routing for this question could not be cross-validated against "
                    "the data: it has no conditional routing (show_if), no resolved "
                    "data link, or no respondent data is available yet."
                ),
            }
        return {
            "success": True,
            "available": True,
            "checked": True,
            "tool": "check_question_routing",
            "study_id": study_id or "",
            "qid": qid,
            "agreement_pct": validation.agreement_pct,
            "flagged": validation.flagged,
            "detail": validation.detail,
        }

    def execute(name: str, params: dict) -> dict:
        if name == "show_table":
            # Deck-table fetch by id, or live computation from a stub variable
            if params.get("table_id") and not params.get("stub"):
                for t in deck.get("tables", []):
                    if t.get("id") == params["table_id"]:
                        return {"success": True, "table": t}
                return {"success": False, "error": f"Table {params['table_id']} not found"}
            return _live_table(params)

        if name == "query_data":
            return _run_sql(parquet, params.get("sql", ""))

        if name == "compare_tables":
            wanted = params.get("table_ids", [])
            found = [t for t in deck.get("tables", []) if t.get("id") in wanted]
            missing = set(wanted) - {t.get("id") for t in found}
            if missing:
                return {"success": False, "error": f"Tables not found: {sorted(missing)}"}
            return {"success": True, "tables": found}

        if name == "navigate_to":
            return {"success": True, "params": params}

        if name == "generate_chart":
            return _chart(params)

        if name == "generate_pivot":
            return _pivot(params)

        if name == "generate_code":
            return _codegen(params)

        if name == "data_quality":
            return _quality(params)

        if name == "check_respondent_quality":
            return _respondent_quality(params)

        if name == "get_question_details":
            return get_question_details(
                _survey(), study_id or "", params.get("qid", ""), links=_links()
            )

        if name == "explain_question_base":
            return explain_question_base(_survey(), study_id or "", params.get("qid", ""))

        if name == "search_questionnaire":
            return search_questionnaire(_survey(), study_id or "", params.get("query", ""))

        if name == "check_question_routing":
            return _check_question_routing(params)

        if name == "get_question_context":
            return get_question_context(
                _survey(), study_id or "", params.get("stub", ""), links=_links()
            )

        return {"success": False, "error": f"Unknown tool: {name}"}

    return execute


# --- Endpoints ---


@router.post("/studies/{study_id}/chat", response_model=ChatResponse)
def chat_with_study(
    study_id: str,
    request: ChatRequest,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """Ask the study navigator a question; it may compute tables or run SQL."""
    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")

    study_dir = _study_dir(study)
    datamap = _load_datamap(study_dir, study)
    deck = _load_deck(study_dir)
    context, intent = build_routed_context(study, datamap, deck, request.message)
    specialist = specialist_for_intent(intent)
    logger.info(f"[{study_id}] Intent routed to: {intent} (specialist: {specialist})")
    executor = _make_executor(study_dir, datamap, deck, study_id=study_id)

    user_msg = create_chat_message(db, study_id, "user", request.message)

    # Recent turns (text only) so follow-up questions have conversational grounding
    history = [
        {"role": m.role, "content": m.content}
        for m in list_chat_messages(db, study_id, limit=8)
        if m.id != user_msg.id and m.content
    ]

    try:
        reply, tool_results = navigate_groq(
            request.message, context, executor, history=history, intent=intent
        )
    except (BackendUnavailable, ModelOutputError) as groq_exc:
        # Same open-weight model family on a second provider before dropping to
        # the weak local model: a Groq quota or outage changes who answers, not
        # the quality of the answer. Open-source models only, by DVV policy.
        logger.warning(f"[{study_id}] Groq unavailable ({groq_exc}); trying Cerebras")
        try:
            reply, tool_results = navigate_cerebras(
                request.message, context, executor, history=history, intent=intent
            )
        except (BackendUnavailable, ModelOutputError) as cerebras_exc:
            # Degrade to the local single-shot model: slower and simpler, but it
            # keeps answering with zero API credits and data never leaves the
            # server. Live when the client's bundle runs the local-ai profile;
            # otherwise it fails instantly and the message below explains why.
            logger.warning(
                f"[{study_id}] Cerebras also unavailable ({cerebras_exc}); trying basic mode"
            )
            try:
                reply, tool_results = navigate_basic(
                    request.message,
                    _basic_context(study, datamap, deck, request.message),
                    executor,
                    intent=intent,
                )
                reply += "\n\n_(basic local mode)_"
            except (BackendUnavailable, ModelOutputError) as basic_exc:
                logger.warning(f"[{study_id}] Basic navigator also unavailable: {basic_exc}")
                fallback = (
                    "The navigator can't reach any AI backend right now "
                    f"(Groq: {groq_exc} · Cerebras: {cerebras_exc} · local: {basic_exc}). "
                    "The Tables tab still has every computed table."
                )
                assistant_msg = create_chat_message(db, study_id, "assistant", fallback)
                return ChatResponse(
                    message=ChatMessage(
                        id=assistant_msg.id,
                        role="assistant",
                        content=fallback,
                        created_at=assistant_msg.created_at,
                    ),
                    tool_results=[],
                    conversation_id=study_id,
                )

    executed = [
        ChatToolCall(tool=r.get("tool", "unknown"), params={}) for r in tool_results
    ]
    assistant_msg = create_chat_message(
        db,
        study_id,
        "assistant",
        reply,
        tools=[t.model_dump() for t in executed] or None,
    )

    return ChatResponse(
        message=ChatMessage(
            id=assistant_msg.id,
            role="assistant",
            content=reply,
            tools=executed or None,
            created_at=assistant_msg.created_at,
        ),
        tool_results=tool_results or None,
        conversation_id=study_id,
    )


@router.get("/studies/{study_id}/chat/history")
def get_chat_history(
    study_id: str,
    limit: int = 50,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """Get chat history for a study."""
    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")

    messages = list_chat_messages(db, study_id, limit=limit)
    return {
        "study_id": study_id,
        "messages": [
            ChatMessage(
                id=m.id,
                role=m.role,
                content=m.content,
                tools=[ChatToolCall(**t) for t in m.tools] if m.tools else None,
                created_at=m.created_at,
            )
            for m in messages
        ],
    }

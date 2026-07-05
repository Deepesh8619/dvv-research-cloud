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
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from api.auth import get_current_user
from api.config import STUDIES_DIR
from api.db import (
    UserDB,
    create_chat_message,
    get_db,
    get_study,
    list_chat_messages,
)
from api.models import ChatMessage, ChatRequest, ChatResponse, ChatToolCall
from src.ai.navigator import navigate_basic, navigate_groq
from src.ai.router import BackendUnavailable, ModelOutputError
from src.core.models import DataMap
from src.engines.tabs.crosstab import crosstab
from src.engines.tabs.runner import table_to_dict
from src.engines.tabs.spec import auto_nets
from src.orchestration.orchestrator import auto_tab_plan

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


def _load_datamap(study_dir: Path) -> DataMap | None:
    path = study_dir / "datamap.json"
    if not path.exists():
        return None
    try:
        return DataMap.from_json(path.read_text())
    except Exception:
        return None


def _load_deck(study_dir: Path) -> dict:
    path = study_dir / "crosstabs.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


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


NAV_DETAIL_VARS = 35


def _build_context(study, datamap: DataMap | None, deck: dict, question: str = "") -> str:
    """Context for the cloud (Groq) navigator, kept lean enough to survive the
    free tier's per-minute token budget.

    The agent loop re-sends this on every turn, so dumping all 150+ variables
    (≈3k tokens) blew the 12k-tokens/minute free limit on a single question.
    Instead: send the full deck-table index (cheap, and it's what "top-level
    findings"-style questions need) plus full detail only for variables relevant
    to the question (via _rank_variables, which weights exact variable-name
    matches so a directly-named var like S2 is always included). For anything
    else the model uses query_data / show_table by name.
    """
    lines = [f"Project: {study.client_name} (status: {study.status})"]

    if deck:
        # Just a one-line pointer, not the full table list. The dataframe is the
        # source of truth (per the system prompt); the model should query it, not
        # lean on precomputed summaries. Listing 30 table titles here cost ~600
        # tokens re-sent every turn — the compare_tables tool can fetch any of
        # them by id (t1..tN) on demand if the user asks about the deck.
        n = len(deck.get("tables", []))
        weight = f"weighted by {deck['weight']}" if deck.get("weight") else "unweighted"
        lines.append(
            f"A deck of {n} precomputed crosstabs exists ({weight}, "
            f"{deck.get('confidence', 0.95):.0%} confidence), ids t1..t{n}; "
            "fetch one with compare_tables only if the user asks about the deck."
        )

    if datamap:
        detailed = _rank_variables(datamap, question, NAV_DETAIL_VARS)

        lines.append(
            f"Data has {len(datamap.variables)} columns. Most relevant to this "
            "question (name [type] label | code=label):"
        )
        for v in detailed:
            entry = f"  {v.name} [{v.type}] {v.label or ''}"
            if v.values:
                shown = list(v.values.items())[:MAX_VALUE_LABELS]
                entry += " | " + " ".join(f"{c}={label}" for c, label in shown)
                if len(v.values) > MAX_VALUE_LABELS:
                    entry += " …"
            lines.append(entry)

        # No full column roster — for a 400+ column study it's ~700 tokens per
        # turn. Relevance matching covers named variables; query_data discovers
        # the rest (`SELECT * FROM data LIMIT 0`).
        plan = auto_tab_plan(datamap)
        if plan:
            lines.append(f"Default banner (columns) for crosstabs: {', '.join(plan.banner)}")
    else:
        lines.append(
            "Datamap unavailable for this study (processed before datamap persistence "
            "existed) — rely on query_data; SQL columns match the original file's headers."
        )

    return "\n".join(lines)


# --- Tool execution ---


def _make_executor(study_dir: Path, datamap: DataMap | None, deck: dict):
    parquet = study_dir / "data.parquet"
    default_plan = auto_tab_plan(datamap) if datamap else None

    def _live_table(params: dict) -> dict:
        if datamap is None:
            return {"success": False, "error": "Datamap unavailable — cannot compute new tables"}
        stub = params.get("stub", "")
        if stub not in datamap:
            return {"success": False, "error": f"Unknown variable: {stub}"}
        banner = params.get("banner") or (default_plan.banner if default_plan else [])
        bad = [b for b in banner if b not in datamap]
        if bad:
            return {"success": False, "error": f"Unknown banner variable(s): {bad}"}
        filter_ = params.get("filter") or None
        if filter_:
            try:
                filter_ = {var: [int(c) for c in codes] for var, codes in filter_.items()}
            except (TypeError, ValueError):
                return {"success": False, "error": "Filter codes must be integers"}
            bad = [v for v in filter_ if v not in datamap]
            if bad:
                return {"success": False, "error": f"Unknown filter variable(s): {bad}"}
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

        return {"success": False, "error": f"Unknown tool: {name}"}

    return execute


# --- Endpoints ---


@router.post("/studies/{study_id}/chat", response_model=ChatResponse)
async def chat_with_study(
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
    datamap = _load_datamap(study_dir)
    deck = _load_deck(study_dir)
    context = _build_context(study, datamap, deck, request.message)
    executor = _make_executor(study_dir, datamap, deck)

    user_msg = create_chat_message(db, study_id, "user", request.message)

    # Recent turns (text only) so follow-up questions have conversational grounding
    history = [
        {"role": m.role, "content": m.content}
        for m in list_chat_messages(db, study_id, limit=8)
        if m.id != user_msg.id and m.content
    ]

    try:
        reply, tool_results = navigate_groq(request.message, context, executor, history=history)
    except (BackendUnavailable, ModelOutputError) as exc:
        # Degrade to the local single-shot model: slower and simpler, but the
        # demo keeps answering with zero API credits and data never leaves
        # the machine.
        logger.warning(f"[{study_id}] Cloud navigator unavailable ({exc}); trying basic mode")
        try:
            reply, tool_results = navigate_basic(
                request.message,
                _basic_context(study, datamap, deck, request.message),
                executor,
            )
            reply += "\n\n_(basic local mode)_"
        except (BackendUnavailable, ModelOutputError) as basic_exc:
            logger.warning(f"[{study_id}] Basic navigator also unavailable: {basic_exc}")
            fallback = (
                "The navigator can't reach any AI backend right now "
                f"(cloud: {exc} · local: {basic_exc}). "
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
async def get_chat_history(
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

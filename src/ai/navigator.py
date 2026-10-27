"""Study navigator: a tool-using agent scoped to ONE study's data.

The LLM never computes numbers. It translates the user's question into tool
calls (run a crosstab, run a SQL query, point at an existing table) and the
deterministic engines execute them. Answers are grounded exclusively in the
study context supplied by the caller — the system prompt forbids outside
knowledge, which is what keeps this a study navigator rather than a generic
chatbot bolted onto the app (CLAUDE.md §4.4).
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable

from src.ai.router import (
    CEREBRAS_DEFAULT_MODEL,
    GROQ_DEFAULT_MODEL,
    USER_AGENT,
    BackendUnavailable,
    ModelOutputError,
    http_json,
    run_task,
)
from src.ai.specialists import (
    SPECIALISTS,
    is_compound_request,
    specialist_for_intent,
    tools_for_specialist,
)

# Groq's Llama-3.3-70B is the default cloud navigator: free tier, tuned for
# tool use, cloud-fast. Overridable if the account is on a different model.
# Groq no longer serves llama-3.3-70b-versatile (the old default here): the
# data chat's primary path had been failing over on every question.
GROQ_MODEL = os.environ.get("GROQ_MODEL", GROQ_DEFAULT_MODEL)
# Fallback navigator when Groq fails (429 daily budget, or a tool call it can't
# recover from): the same open-weight family on a different provider, so a
# Groq outage changes WHO answers, not what kind of model does. DVV uses
# open-source models only — there is no proprietary vendor in this file.
CEREBRAS_MODEL = os.environ.get("CEREBRAS_MODEL", CEREBRAS_DEFAULT_MODEL)
# Each turn re-sends context + tools + accumulated tool results, so turns are
# the dominant cost against Groq's free 12k-tokens/min. 3 is enough for the
# normal pattern (call a tool, read the result, answer) while capping runaway
# loops that would blow the budget mid-question. 2 is the normal shape
# (call one tool, then answer) and keeps a question well inside Groq's free
# 12k-tokens/min; the forced-final-answer turn still guarantees a reply.
MAX_AGENT_TURNS = 2

SYSTEM_PROMPT = """You are the data navigator for one survey research study on the \
DVV Research Cloud platform. You help the analyst see, understand, and work with \
THIS study's data only.

The uploaded respondent-level data is the source of truth — a table named \
`data`, one row per respondent, columns = the variables below. Work from that \
data, not from any precomputed summary.

Capabilities — any data-related request should be handled with one of these:
- Crosstabs with significance letters (show_table)
- Ad-hoc counts/distributions/averages via SQL (query_data)
- Charts: bar, line, pie, heatmap (generate_chart)
- Pivot tables: rows x columns aggregation (generate_pivot)
- SQL / Python / Excel-formula code, written by you using the tool's schema \
(generate_code)
- Data quality checks: missing %, constants, duplicate IDs (data_quality)
- Questionnaire lookups when a questionnaire was uploaded: a question's exact \
wording/options/routing (get_question_details), who was asked it in plain English \
(explain_question_base), or finding a question by topic (search_questionnaire)

Hard rules:
- Answer only from THIS study's data + your tool results. No outside knowledge \
about brands, markets, or benchmarks.
- Prefer query_data (SQL over `data`) for counts, distributions, averages, and \
ad-hoc questions; use show_table for a proper crosstab with significance.
- Numbers come from tools, never your own arithmetic. To show data, call a tool.
- Use only variable names listed below; never invent one. If a needed variable \
isn't shown, run query_data (`SELECT * FROM data LIMIT 0`) to discover columns.
- If a question can't be answered from this data, say so and suggest a better \
variable to ask about. Don't guess.
- generate_code returns schema only — you write the actual SQL/Python/formula \
in your reply, using real column names from that schema.
- Reply short and analyst-like: lead with the finding; a significance letter \
means significantly higher than that column at the study's confidence level.

STATISTICAL INTEGRITY RULES:
- Pre-computed statistical guardrails are embedded in every crosstab result. \
These include p-values, effect sizes (Cramér's V), confidence intervals, and power.
- ONLY cite the pre-computed p-values and significance flags from the tool result. \
Do NOT compute p-values, chi-square, or statistical tests yourself.
- If a crosstab has fewer than 5 unweighted respondents in any cell, Fisher's \
exact test was used instead of chi-square—note this when reporting.
- If the reported power is below 0.80, the test may not be able to detect true \
differences—mention this limitation.
- Report effect size (Cramér's V) alongside p-values: "p < 0.05, V = 0.25 \
(small-to-medium effect)".
- Confidence intervals show uncertainty ranges for each cell proportion. Cite them \
when discussing precision.
- Example: "Age is significantly associated with purchase intent (p = 0.032, \
V = 0.18), with power = 0.72 to detect this effect."

Study context:
{context}"""

TOOLS = [
    {
        "name": "show_table",
        "description": (
            "Compute a crosstab live from the respondent data: one stub variable "
            "broken by banner variables, with column % and significance letters. "
            "Use for any 'show me X', 'cross X by Y', 'X among [subgroup]' request."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "stub": {"type": "string", "description": "variable name for the rows"},
                "banner": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "variable names for the columns (defaults to the study banner)",
                },
                "filter": {
                    "type": "object",
                    "description": (
                        "optional subgroup filter: variable name -> list of category "
                        "codes to keep, e.g. {\"S1\": [2]}"
                    ),
                },
                "mean": {"type": "boolean", "description": "include a mean row"},
            },
            "required": ["stub"],
        },
    },
    {
        "name": "query_data",
        "description": (
            "Run a read-only SQL SELECT against the respondent-level data "
            "(DuckDB, table name: data). Use for counts, averages, distributions, "
            "or anything a crosstab doesn't fit. Columns are the datamap variable names."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "a single SELECT statement"}
            },
            "required": ["sql"],
        },
    },
    {
        "name": "compare_tables",
        "description": (
            "Fetch already-computed deck tables by id (t1, t2, ...) to compare or reference."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "table_ids": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["table_ids"],
        },
    },
    {
        "name": "navigate_to",
        "description": (
            "Point the user at a place in the app: a tab (Overview, Reports, "
            "Activity). Use when they ask where to find something. Crosstabs "
            "aren't a separate tab — use show_table to display one inline instead "
            "of navigating."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tab": {"type": "string"},
            },
            "required": ["tab"],
        },
    },
    {
        "name": "generate_chart",
        "description": (
            "Render a chart (bar/line/pie/heatmap) for a variable, optionally broken "
            "by a banner. Use for 'chart', 'plot', 'graph', 'visualize' requests. "
            "Also returns the underlying table data."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "stub": {"type": "string", "description": "variable to chart"},
                "banner": {"type": "array", "items": {"type": "string"}},
                "chart_type": {
                    "type": "string",
                    "enum": ["bar", "line", "pie", "heatmap"],
                    "description": "defaults to bar",
                },
                "filter": {
                    "type": "object",
                    "description": "optional subgroup filter: variable -> list of codes",
                },
            },
            "required": ["stub"],
        },
    },
    {
        "name": "generate_pivot",
        "description": (
            "Compute a rows x columns pivot table with an aggregation (count/mean/"
            "sum/min/max), no significance testing. Use for 'pivot', 'aggregate by' "
            "requests that don't need sig letters."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "rows": {"type": "string", "description": "variable for pivot rows"},
                "columns": {"type": "string", "description": "variable for pivot columns"},
                "values": {
                    "type": "string",
                    "description": "variable to aggregate (required unless aggfunc=count)",
                },
                "aggfunc": {
                    "type": "string",
                    "enum": ["count", "mean", "sum", "min", "max"],
                    "description": "defaults to count",
                },
            },
            "required": ["rows", "columns"],
        },
    },
    {
        "name": "generate_code",
        "description": (
            "Get the data schema (column names/types) so you can write correct SQL, "
            "Python, or Excel-formula code in your reply. For sql, pass the query you "
            "wrote to execute it via the sandbox and return real results."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "language": {"type": "string", "enum": ["sql", "python", "excel_formula"]},
                "question": {"type": "string", "description": "the user's original question"},
                "sql": {
                    "type": "string",
                    "description": "optional SELECT to execute (language=sql only)",
                },
            },
            "required": ["language", "question"],
        },
    },
    {
        "name": "data_quality",
        "description": (
            "Run a data quality report: missing %, distinct counts, min/max or top "
            "values per column, flags for >50% missing, constant, and likely-duplicate "
            "ID columns. Use for 'quality', 'missing data', 'duplicates', 'clean' requests."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "variables": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "optional subset of columns to check (default: all)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "check_respondent_quality",
        "description": (
            "Flag low-quality respondents in a fielded study: speeders (finished in "
            "under 1/3 of the median interview time), straightliners (identical answer "
            "across a whole grid), duplicate respondents, and gibberish open-ends. "
            "Returns per-respondent ids per check plus summary counts. Use for 'bad "
            "respondents', 'speeders', 'straightliners', 'clean the sample', 'data "
            "cleaning'. Needs paradata (fielded study) for the speeder check; on a "
            "non-fielded study it says so and still runs the other checks."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_question_details",
        "description": (
            "Look up the full extracted questionnaire question for a given qid: "
            "its wording, type, answer options, routing (with the original "
            "verbatim text), extraction confidence, flags, and the DataMap "
            "variable(s) it produced if known. Use when the analyst asks 'what "
            "exactly did Q5 ask', 'what were the options for X', or 'show me the "
            "wording of that question'. Needs the study to have an approved "
            "questionnaire uploaded."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "qid": {"type": "string", "description": "the question id, e.g. 'Q5'"},
            },
            "required": ["qid"],
        },
    },
    {
        "name": "explain_question_base",
        "description": (
            "Explain in plain English WHO was asked a given question — its base — "
            "by translating the question's routing condition (show_if) into "
            "stakeholder-friendly prose, e.g. 'Asked only of respondents who said "
            "Yes to Q3'. Use for 'who was asked X', 'what's the base for Q5', 'why "
            "do fewer people have this question'. Needs an approved questionnaire."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "qid": {"type": "string", "description": "the question id, e.g. 'Q5'"},
            },
            "required": ["qid"],
        },
    },
    {
        "name": "search_questionnaire",
        "description": (
            "Free-text search the study's questionnaire to find the qid(s) of "
            "questions about a topic, when the analyst names a subject rather than "
            "a qid — e.g. 'which question asked about brand awareness'. Matches "
            "question wording and routing text; returns matching qids with "
            "snippets. Needs an approved questionnaire."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "text to search for in question/routing wording",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "check_question_routing",
        "description": (
            "Cross-check a question's documented routing (show_if) against what "
            "real respondents actually did in the data: returns the % of answered "
            "rows that satisfy the routing, whether it's flagged, and a plain "
            "explanation. Use to QC whether the questionnaire's stated skip logic "
            "matches the fielded data — e.g. 'does Q5's base match its routing', "
            "'did anyone answer Q5 who shouldn't have'. Needs an approved "
            "questionnaire and respondent data."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "qid": {"type": "string", "description": "the question id, e.g. 'Q5'"},
            },
            "required": ["qid"],
        },
    },
    {
        "name": "get_question_context",
        "description": (
            "Resolve a DATA variable (a column/stub like 'Q5r1') back to the "
            "questionnaire question that produced it: its wording, type, and a "
            "plain-English base/routing description. Use when a table's base size "
            "looks unexpectedly small (was the question routed/skipped?), or the "
            "user asks what a variable's question actually said. Needs an approved "
            "questionnaire; system/paradata columns return a clean 'not linked'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "stub": {
                    "type": "string",
                    "description": "the data variable/column name, e.g. 'Q5r1'",
                },
            },
            "required": ["stub"],
        },
    },
]




_KNOWN_TOOLS = {t["name"] for t in TOOLS}
# Matches the tool-call-as-text formats llama emits on Groq, e.g.
#   <function=show_table>{"stub": "S1"}</function>
#   <function(show_table){"stub": "S1", "banner": []}
# Capture the tool name and the first {...} JSON object after it.
_TEXT_TOOLCALL_RE = re.compile(r"<function[=(]\s*([A-Za-z_]\w*)\s*[)>]?\s*(\{.*?\})", re.DOTALL)


def _parse_text_tool_calls(content: str, known: frozenset | set = frozenset()) -> list[dict]:
    """Recover tool calls the model wrote as text into structured-call dicts."""
    calls = []
    for i, m in enumerate(_TEXT_TOOLCALL_RE.finditer(content or "")):
        name, raw_args = m.group(1), m.group(2)
        if name not in (known or _KNOWN_TOOLS):
            continue
        try:
            json.loads(raw_args)  # validate; keep as string for the executor path
        except json.JSONDecodeError:
            continue
        calls.append(
            {"id": f"text_call_{i}", "function": {"name": name, "arguments": raw_args}}
        )
    return calls


def _repair_malformed_tool_call(fn: dict, known: frozenset | set = frozenset()) -> dict | None:
    """Recover a structured tool call whose name and JSON arguments got fused
    into one string by the model — observed on llama-3.3/Groq as
    function.name == 'query_data{"sql": "..."}' instead of a bare tool name
    with separate arguments.

    Returns a clean {"name", "arguments"} dict, or None if unsalvageable.
    None must never be echoed back into message history as-is: Groq's own
    API re-validates the ENTIRE conversation history against the current
    request's `tools` schema on every turn, so a malformed name here doesn't
    just fail this one tool call — it hard-fails every later turn with
    HTTP 400 "tool call validation failed", taking down the whole
    conversation instead of just one bad call.
    """
    known = known or _KNOWN_TOOLS
    name = fn.get("name", "")
    if name in known:
        return {"name": name, "arguments": fn.get("arguments") or "{}"}

    brace = name.find("{")
    if brace == -1:
        return None
    real_name, embedded_json = name[:brace], name[brace:]
    if real_name not in known:
        return None
    try:
        json.loads(embedded_json)  # validate before trusting it as arguments
    except json.JSONDecodeError:
        return None
    return {"name": real_name, "arguments": embedded_json}


def _groq_tools(tool_names: list[str] | None = None) -> list[dict]:
    """TOOLS in the standard function-calling shape Groq, Cerebras and Ollama accept.

    tool_names restricts the schema list to a specialist's owned tools (see
    src/ai/specialists.py) — before this, every call sent all 8 tool schemas
    regardless of the classified intent, which was the single largest
    remaining token cost the routing work hadn't addressed (context text was
    already trimmed per-intent; the tool schemas never were). None means "no
    specialist context available" and sends the full set, matching prior
    behavior (e.g. the basic-mode fallback path, or callers that haven't
    adopted specialist routing).
    """
    tools = TOOLS if tool_names is None else [t for t in TOOLS if t["name"] in tool_names]
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in tools
    ]


# On the free tier a burst of questions can exhaust the per-minute token
# budget; Groq's 429 tells us how long to wait ("try again in 6.045s"). One
# short retry keeps the user on the good 70B model instead of dropping to the
# weak local fallback, which gives visibly worse answers. Capped so a genuinely
# long backoff still falls through rather than hanging the request.
_RETRY_AFTER_RE = re.compile(r"try again in ([\d.]+)s")
GROQ_MAX_RETRY_WAIT = 12.0


def _compatible_chat(
    url: str,
    key_env: str,
    model: str,
    label: str,
    messages: list[dict],
    timeout: int,
    with_tools: bool = True,
    tools_subset: list[str] | None = None,
    tools_schema: list[dict] | None = None,
) -> dict:
    """One chat-completions call to an open-model provider, with one 429 retry.

    Groq and Cerebras both speak this request shape (the de-facto standard wire
    format — it is a protocol, not a vendor). with_tools=False forces a plain
    text answer, used for the final "now summarize" turn. tools_subset limits
    the schemas to one specialist's tools; None sends the full registry.
    tools_schema sends a caller's own tool set instead (the copilot's).
    key_env=None is a keyless local endpoint (Ollama on the client's server).
    """
    headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
    if key_env is not None:
        api_key = os.environ.get(key_env)
        if not api_key:
            raise BackendUnavailable(f"{key_env} not set")
        headers["Authorization"] = f"Bearer {api_key}"
    body_obj = {"model": model, "max_tokens": 1500, "temperature": 0, "messages": messages}
    if with_tools:
        body_obj["tools"] = tools_schema if tools_schema is not None else _groq_tools(tools_subset)
    request = urllib.request.Request(url, data=json.dumps(body_obj).encode(), headers=headers)
    # A burst of questions can exhaust a free per-minute budget; the 429 says
    # how long to wait. One short retry keeps the good model; a long backoff
    # still falls through rather than hanging the request.
    for attempt in (1, 2):
        try:
            return http_json(request, timeout)
        except (urllib.error.URLError, TimeoutError) as exc:
            wait = _RETRY_AFTER_RE.search(str(exc))
            if attempt == 1 and wait and float(wait.group(1)) <= GROQ_MAX_RETRY_WAIT:
                time.sleep(float(wait.group(1)) + 0.5)
                continue
            raise BackendUnavailable(f"{label} API unreachable: {exc}") from exc


def _groq_chat(messages, timeout, with_tools=True, tools_subset=None) -> dict:
    return _compatible_chat(
        "https://api.groq.com/openai/v1/chat/completions", "GROQ_API_KEY", GROQ_MODEL,
        "Groq", messages, timeout, with_tools, tools_subset,
    )


def _bedrock_chat(messages, timeout, with_tools=True, tools_subset=None) -> dict:
    from src.ai.router import bedrock_chat_url, bedrock_model

    return _compatible_chat(
        bedrock_chat_url(), "BEDROCK_API_KEY", bedrock_model(),
        "Bedrock", messages, timeout, with_tools, tools_subset,
    )


def _cerebras_chat(messages, timeout, with_tools=True, tools_subset=None) -> dict:
    return _compatible_chat(
        "https://api.cerebras.ai/v1/chat/completions", "CEREBRAS_API_KEY", CEREBRAS_MODEL,
        "Cerebras", messages, timeout, with_tools, tools_subset,
    )


def navigate_groq(
    question: str,
    context: str,
    execute_tool: Callable[[str, dict], dict],
    history: list[dict] | None = None,
    timeout: int = 60,
    intent: str | None = None,
    specialist: str | None = None,
) -> tuple[str, list[dict]]:
    """Full multi-tool agent loop on Groq (the standard chat-completions format).

    Args:
        intent: Optional intent classification (e.g., 'crosstab_analysis').
        specialist: Optional specialist name (see src/ai/specialists.py) —
            restricts which tool schemas are sent to Groq and adds that
            specialist's prompt fragment, instead of the generic "Classified
            intent" line. Derived from `intent` automatically when omitted, so
            existing callers passing only `intent=` keep working and get the
            tool-schema savings for free. Passing neither sends the full tool
            registry, matching pre-specialist behavior.
    """
    return _run_agent_loop(
        _groq_chat, "Groq", question, context, execute_tool, history, timeout, intent, specialist
    )




def navigate_bedrock(
    question: str,
    context: str,
    execute_tool: Callable[[str, dict], dict],
    history: list[dict] | None = None,
    timeout: int = 60,
    intent: str | None = None,
    specialist: str | None = None,
) -> tuple[str, list[dict]]:
    """The same agent loop on Amazon Bedrock, in the client's own AWS region."""
    return _run_agent_loop(
        _bedrock_chat, "Bedrock", question, context, execute_tool, history, timeout,
        intent, specialist,
    )


def navigate_cerebras(
    question: str,
    context: str,
    execute_tool: Callable[[str, dict], dict],
    history: list[dict] | None = None,
    timeout: int = 60,
    intent: str | None = None,
    specialist: str | None = None,
) -> tuple[str, list[dict]]:
    """The same agent loop on Cerebras — the open-weight fallback when Groq fails."""
    return _run_agent_loop(
        _cerebras_chat, "Cerebras", question, context, execute_tool, history, timeout,
        intent, specialist,
    )


def _run_agent_loop(
    chat_fn: Callable[..., dict],
    backend_label: str,
    question: str,
    context: str,
    execute_tool: Callable[[str, dict], dict],
    history: list[dict] | None,
    timeout: int,
    intent: str | None,
    specialist: str | None,
    system_prompt: str | None = None,
    max_turns: int = MAX_AGENT_TURNS,
    known_tools: frozenset | None = None,
) -> tuple[str, list[dict]]:
    """Shared multi-tool agent loop for any chat-completions backend (Groq,
    Cerebras). chat_fn must accept (messages, timeout, with_tools=,
    tools_subset=) and return a {"choices": [...]} dict.
    """
    if specialist is None and intent is not None:
        specialist = specialist_for_intent(intent)

    tools_subset: list[str] | None = None
    if system_prompt is None:  # the navigator's own prompt (a caller may bring its own)
        system_prompt = SYSTEM_PROMPT.format(context=context)
        if specialist:
            tools_subset = tools_for_specialist(
                specialist, compound=is_compound_request(question)
            )
            fragment = SPECIALISTS.get(specialist, SPECIALISTS["analyst"]).prompt_fragment
            system_prompt += f"\n\n{fragment}"
        elif intent:
            system_prompt += f"\n\nClassified intent: {intent}. Focus on this analysis task."

    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    for turn in history or []:
        messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": question})

    tool_results: list[dict] = []
    reply = ""

    for _ in range(max_turns):
        body = chat_fn(messages, timeout, tools_subset=tools_subset)
        choices = body.get("choices", [])
        if not choices:
            raise ModelOutputError(f"{backend_label} returned no choices")
        msg = choices[0]["message"]
        content = msg.get("content") or ""
        raw_calls = msg.get("tool_calls") or []
        echo_msg = msg

        # llama-3.x on Groq sometimes emits a tool call as literal text
        # (`<function=name>{json}</function>`) in content instead of the
        # structured tool_calls field. Recover those so the call still executes
        # rather than leaking raw markup to the user.
        if not raw_calls:
            recovered = _parse_text_tool_calls(content, known_tools)
            if recovered:
                raw_calls = recovered
                content = ""  # the markup wasn't a real answer
        else:
            # Structured tool_calls (not the text-recovery path above):
            # sanitize every call's name/arguments before executing OR
            # echoing. A dropped/repaired call must not leave the original
            # malformed name in what gets echoed back — see
            # _repair_malformed_tool_call's docstring for why that poisons
            # every later turn, not just this one.
            sanitized = []
            for call in raw_calls:
                fixed = _repair_malformed_tool_call(call.get("function", {}), known_tools)
                if fixed is not None:
                    sanitized.append({**call, "function": fixed})
            raw_calls = sanitized
            echo_msg = {**msg, "tool_calls": raw_calls}

        calls = raw_calls

        if content:
            reply = content

        if not calls:
            break

        messages.append(echo_msg)  # assistant turn with tool_calls, echoed back (sanitized)
        for call in calls:
            fn = call["function"]
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            result = execute_tool(fn["name"], args)
            result["tool"] = fn["name"]
            result.setdefault("success", True)
            tool_results.append(result)
            messages.append(
                {"role": "tool", "tool_call_id": call["id"], "content": _compact(result)}
            )
            result["args"] = args  # after the model's copy: what was asked, for the record

    if not reply.strip():
        # The model spent every turn calling tools and never wrote an answer
        # (common when a requested crossbreak comes back thin/empty and it keeps
        # retrying). Force one final text-only turn so the user always gets a
        # real reply grounded in whatever the tools returned, instead of the
        # loop silently failing over to the weak local model.
        if tool_results:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Based on the tool results above, answer the original "
                        "question now in plain text. If a requested crossbreak "
                        "returned no usable columns, say so and suggest a better "
                        "variable to cross by."
                    ),
                }
            )
            final = chat_fn(messages, timeout, with_tools=False)
            choices = final.get("choices", [])
            if choices and choices[0]["message"].get("content"):
                reply = choices[0]["message"]["content"]

    if not reply.strip():
        raise ModelOutputError(f"{backend_label} navigator returned no reply text")
    return reply.strip(), tool_results


def _compact(result: dict, limit: int = 1500) -> str:
    """Tool result as JSON for the model, truncated to keep turns cheap."""
    text = json.dumps(result, ensure_ascii=False, default=str)
    return text[:limit] + ("…(truncated)" if len(text) > limit else "")




# --- Basic mode: local model, single shot, one tool max ---
#
# The full agent loop needs a model that can chain tool calls reliably;
# llama3.2:3b (the largest that fits an 8GB dev machine) cannot. Basic mode
# trades capability for zero cost: one local call decides between answering
# directly from the datamap context or requesting ONE simple tool, which the
# caller executes and attaches. Slow (~15-60s locally) but free and private —
# the demo keeps working with no API credits at all.

BASIC_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "tool": {"type": "string", "enum": ["none", "show_table", "query_data"]},
        "stub": {"type": "string"},
        "banner": {"type": "array", "items": {"type": "string"}},
        "sql": {"type": "string"},
    },
    "required": ["answer", "tool"],
}

BASIC_PROMPT = """You are the data navigator for one survey study. Answer the \
analyst's question using ONLY the study context below. Never use outside \
knowledge; if the context can't answer it, say so.

To show a crosstab for a variable, set tool="show_table" and stub=<variable name \
from the datamap>. If the analyst named a specific variable to cross/break it by \
(e.g. "by gender", "for banner S1"), set banner=[<that variable's name>]; \
otherwise leave banner=[] for a plain Total-only breakdown. For a count/average \
over the raw data, set tool="query_data" and sql=<one SELECT over table `data`>. \
Otherwise tool="none" and stub="" and sql="".
Put your short reply in "answer" either way.

Study context:
{context}

Question: {question}"""


def navigate_basic(
    question: str,
    context: str,
    execute_tool: Callable[[str, dict], dict],
    timeout: int = 120,
    intent: str | None = None,
) -> tuple[str, list[dict]]:
    """Single-shot local-model navigation. Returns (reply, tool_results).

    Args:
        intent: Optional intent classification (ignored in basic mode but kept
                for signature compatibility).
    """
    result = run_task(
        "study.chat.basic",
        BASIC_PROMPT.format(context=context, question=question),
        BASIC_SCHEMA,
        timeout=timeout,
    )
    content = result.content
    answer = str(content.get("answer", "")).strip()
    tool = content.get("tool", "none")

    tool_results: list[dict] = []
    if tool == "show_table" and content.get("stub"):
        banner = content.get("banner")
        params = {"stub": content["stub"]}
        if isinstance(banner, list) and all(isinstance(b, str) for b in banner):
            params["banner"] = banner
        outcome = execute_tool("show_table", params)
        outcome["tool"] = "show_table"
        outcome.setdefault("success", True)
        tool_results.append(outcome)
    elif tool == "query_data" and content.get("sql"):
        outcome = execute_tool("query_data", {"sql": content["sql"]})
        outcome["tool"] = "query_data"
        outcome.setdefault("success", True)
        tool_results.append(outcome)

    if not answer:
        raise ModelOutputError("basic navigator returned no answer")
    return answer, tool_results

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
import urllib.error
import urllib.request
from collections.abc import Callable

from src.ai.router import USER_AGENT, BackendUnavailable, ModelOutputError, http_json, run_task

NAVIGATOR_MODEL = os.environ.get("NAVIGATOR_MODEL", "claude-haiku-4-5-20251001")
# Groq's Llama-3.3-70B is the default cloud navigator: free tier, tuned for
# tool use, cloud-fast. Overridable if the account is on a different model.
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
MAX_AGENT_TURNS = 4

SYSTEM_PROMPT = """You are the data navigator for one survey research study on the \
DVV Research Cloud platform. You help the analyst see, understand, and work with \
THIS study's data only.

Hard rules:
- Ground every answer exclusively in the study context and tool results provided. \
Never use outside knowledge about brands, markets, or benchmarks.
- If the question cannot be answered from this study's data, say exactly that and \
suggest what the user could ask instead. Do not guess.
- Numbers come from tools, never from your own arithmetic. To show data, call a tool.
- Variable names must come from the datamap listed in the context. Never invent one.
- Keep replies short and analyst-like: lead with the finding, mention significance \
letters when relevant (a letter means significantly higher than that column at the \
study's confidence level).

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
            "Point the user at a place in the app: a tab (Overview, Tables, Reports, "
            "Activity) and optionally a table id. Use when they ask where to find something."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tab": {"type": "string"},
                "table_id": {"type": "string"},
            },
            "required": ["tab"],
        },
    },
]


def _claude_messages(messages: list[dict], system: str, timeout: int) -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise BackendUnavailable("ANTHROPIC_API_KEY not set")
    payload = json.dumps(
        {
            "model": NAVIGATOR_MODEL,
            "max_tokens": 1500,
            "system": system,
            "messages": messages,
            "tools": TOOLS,
        }
    ).encode()
    request = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        return http_json(request, timeout)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise BackendUnavailable(f"claude API unreachable: {exc}") from exc


def _groq_tools() -> list[dict]:
    """TOOLS in OpenAI/Groq function-calling shape (Anthropic uses input_schema)."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in TOOLS
    ]


def _groq_chat(messages: list[dict], timeout: int) -> dict:
    """One Groq chat-completions call. messages already include the system role."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise BackendUnavailable("GROQ_API_KEY not set")
    payload = json.dumps(
        {
            "model": GROQ_MODEL,
            "max_tokens": 1500,
            "temperature": 0,
            "messages": messages,
            "tools": _groq_tools(),
        }
    ).encode()
    request = urllib.request.Request(
        "https://api.groq.com/openai/v1/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        return http_json(request, timeout)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise BackendUnavailable(f"Groq API unreachable: {exc}") from exc


def navigate_groq(
    question: str,
    context: str,
    execute_tool: Callable[[str, dict], dict],
    history: list[dict] | None = None,
    timeout: int = 60,
) -> tuple[str, list[dict]]:
    """Full multi-tool agent loop on Groq (OpenAI-compatible message format)."""
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT.format(context=context)}]
    for turn in history or []:
        messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": question})

    tool_results: list[dict] = []
    reply = ""

    for _ in range(MAX_AGENT_TURNS):
        body = _groq_chat(messages, timeout)
        choices = body.get("choices", [])
        if not choices:
            raise ModelOutputError("Groq returned no choices")
        msg = choices[0]["message"]
        calls = msg.get("tool_calls") or []
        if msg.get("content"):
            reply = msg["content"]

        if not calls:
            break

        messages.append(msg)  # assistant turn with tool_calls, echoed back verbatim
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

    if not reply.strip():
        raise ModelOutputError("Groq navigator returned no reply text")
    return reply.strip(), tool_results


def _compact(result: dict, limit: int = 4000) -> str:
    """Tool result as JSON for the model, truncated to keep turns cheap."""
    text = json.dumps(result, ensure_ascii=False, default=str)
    return text[:limit] + ("…(truncated)" if len(text) > limit else "")


def navigate(
    question: str,
    context: str,
    execute_tool: Callable[[str, dict], dict],
    history: list[dict] | None = None,
    timeout: int = 90,
) -> tuple[str, list[dict]]:
    """Run the agent loop: question in, (reply text, executed tool results) out.

    execute_tool(name, input) -> result dict; must set "success" and, on
    failure, "error". Tool results are returned so the UI can render tables
    inline exactly as the deterministic engines produced them.
    """
    system = SYSTEM_PROMPT.format(context=context)
    messages = list(history or []) + [{"role": "user", "content": question}]
    tool_results: list[dict] = []
    reply_parts: list[str] = []

    for _ in range(MAX_AGENT_TURNS):
        body = _claude_messages(messages, system, timeout)
        content = body.get("content", [])
        tool_uses = [b for b in content if b.get("type") == "tool_use"]
        # Each turn's text replaces the previous — intermediate turns are
        # narration ("let me run that…"); the final no-tool turn is the answer.
        turn_text = [b["text"] for b in content if b.get("type") == "text" and b["text"]]
        if turn_text:
            reply_parts = turn_text

        if not tool_uses:
            break

        messages.append({"role": "assistant", "content": content})
        result_blocks = []
        for use in tool_uses:
            result = execute_tool(use["name"], use.get("input", {}))
            result["tool"] = use["name"]
            result.setdefault("success", True)
            tool_results.append(result)
            result_blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": use["id"],
                    "content": _compact(result),
                }
            )
        messages.append({"role": "user", "content": result_blocks})

    reply = "\n\n".join(reply_parts).strip()
    if not reply:
        raise ModelOutputError("navigator returned no reply text")
    return reply, tool_results


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
        "sql": {"type": "string"},
    },
    "required": ["answer", "tool"],
}

BASIC_PROMPT = """You are the data navigator for one survey study. Answer the \
analyst's question using ONLY the study context below. Never use outside \
knowledge; if the context can't answer it, say so.

To show a crosstab for a variable, set tool="show_table" and stub=<variable name \
from the datamap>. For a count/average over the raw data, set tool="query_data" \
and sql=<one SELECT over table `data`>. Otherwise tool="none" and stub="" and sql="".
Put your short reply in "answer" either way.

Study context:
{context}

Question: {question}"""


def navigate_basic(
    question: str,
    context: str,
    execute_tool: Callable[[str, dict], dict],
    timeout: int = 120,
) -> tuple[str, list[dict]]:
    """Single-shot local-model navigation. Returns (reply, tool_results)."""
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
        outcome = execute_tool("show_table", {"stub": content["stub"]})
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

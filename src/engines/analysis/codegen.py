"""Code-generation support tool: schema lookup, not code generation.

This module deliberately does NOT call an LLM and does NOT write code. Writing
correct SQL/Python/Excel formulas requires understanding the user's question in
natural language — that's the navigator LLM's job, not a deterministic engine's.
What this tool provides is the missing piece the LLM needs to write CORRECT
code: the real column names and types, pulled from the datamap, so the model
doesn't invent columns. For sql it optionally executes the LLM-authored query
through the existing read-only sandbox so the reply can show real results
alongside the code.
"""

from __future__ import annotations

from collections.abc import Callable

from src.core.models import DataMap

LANGUAGES = ("sql", "python", "excel_formula")


def generate_code(
    datamap: DataMap | None,
    language: str,
    question: str,
    sql: str | None = None,
    run_sql: Callable[[str], dict] | None = None,
) -> dict:
    """Return the data schema for `language`, optionally executing `sql`.

    Args:
        datamap: study datamap (schema source); None means schema unavailable.
        language: one of "sql", "python", "excel_formula".
        question: the user's original question, echoed back for context.
        sql: an already-written SELECT to execute (only used when language == "sql").
        run_sql: callable(sql) -> dict, the sandboxed executor (injected so this
            module stays free of the api-layer SQL guard — keeps engines free of
            HTTP-layer concerns).
    """
    if language not in LANGUAGES:
        return {
            "success": False,
            "error": f"Unknown language '{language}'; must be one of {LANGUAGES}",
        }

    schema = []
    if datamap is not None:
        for v in datamap.variables:
            schema.append(
                {
                    "name": v.name,
                    "type": v.type.value,
                    "label": v.label or "",
                    "values": v.values,
                }
            )

    executed_result = None
    if language == "sql" and sql:
        if run_sql is None:
            return {"success": False, "error": "SQL execution unavailable in this context"}
        executed_result = run_sql(sql)

    return {
        "success": True,
        "language": language,
        "question": question,
        "schema": schema,
        "executed_result": executed_result,
        "table_name": "data",
    }

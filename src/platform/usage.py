"""What the AI models were asked to do, counted from what they report back.

Every OpenAI-compatible reply carries `usage` — prompt and completion tokens.
`record` adds them to one row per day, provider, model and purpose, so the
Bedrock line on the AWS bill can be read as "140 copilot questions and three
documents", not guessed at.

It must never cost a model call anything: every failure here is logged and
swallowed. It writes through its own short session, because extraction calls
the model from worker processes that have no request session to borrow.
"""

from __future__ import annotations

import json
import logging
import os
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

#: Set around a copilot request; everything else is counted as "other".
purpose: ContextVar[str] = ContextVar("dvv_ai_purpose", default="other")


def _provider(url: str) -> str:
    host = url.lower()
    for name in ("bedrock", "groq", "cerebras"):
        if name in host:
            return name
    if "11434" in host or "ollama" in host or "localhost" in host or "127.0.0.1" in host:
        return "local"
    return "other"


def _bump(provider: str, model: str, what: str, calls: int = 0, input_tokens: int = 0,
          output_tokens: int = 0, questions: int = 0) -> None:
    from api.db import ModelUsageDB, SessionLocal

    day = datetime.now(UTC).date().isoformat()
    key = f"{day}|{provider}|{model}|{what}"
    db = SessionLocal()
    try:
        added = {ModelUsageDB.calls: ModelUsageDB.calls + calls,
                 ModelUsageDB.input_tokens: ModelUsageDB.input_tokens + input_tokens,
                 ModelUsageDB.output_tokens: ModelUsageDB.output_tokens + output_tokens,
                 ModelUsageDB.questions: ModelUsageDB.questions + questions}
        # One UPDATE that adds, so two processes counting at once both count.
        if not db.query(ModelUsageDB).filter(ModelUsageDB.id == key).update(added):
            db.add(ModelUsageDB(id=key, day=day, provider=provider, model=model, purpose=what,
                                calls=calls, input_tokens=input_tokens,
                                output_tokens=output_tokens, questions=questions))
        db.commit()
    except Exception:  # first insert racing another process: add to its row instead
        db.rollback()
        try:
            db.query(ModelUsageDB).filter(ModelUsageDB.id == key).update(added)
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("could not count model usage")
    finally:
        db.close()


def record(url: str, request_body: bytes | None, reply: Any) -> None:
    """Count one model reply. Never raises."""
    try:
        used = reply.get("usage") if isinstance(reply, dict) else None
        if not isinstance(used, dict):
            return
        model = ""
        if request_body:
            model = str(json.loads(request_body).get("model") or "")
        _bump(_provider(url), model or "unknown", purpose.get(), calls=1,
              input_tokens=int(used.get("prompt_tokens") or 0),
              output_tokens=int(used.get("completion_tokens") or 0))
    except Exception:
        logger.exception("could not count model usage")


def count_question() -> None:
    """One copilot question asked. Never raises."""
    try:
        _bump("all", "all", "copilot", questions=1)
    except Exception:
        logger.exception("could not count a copilot question")


def prices() -> dict[str, tuple[float, float]]:
    """US$ per million (input, output) tokens, by provider. Bedrock's comes from
    the environment because AWS sets it, not us; local models cost nothing."""
    return {
        "bedrock": (float(os.environ.get("BEDROCK_PRICE_IN_PER_M", "0.15")),
                    float(os.environ.get("BEDROCK_PRICE_OUT_PER_M", "0.60"))),
        "local": (0.0, 0.0),
    }


def summary(db, days: int = 30) -> dict:
    """Per day: questions, model calls, tokens and estimated cost; plus the month."""
    from api.db import ModelUsageDB

    since = (datetime.now(UTC).date() - timedelta(days=days - 1)).isoformat()
    table = prices()
    by_day: dict[str, dict] = {}
    for row in db.query(ModelUsageDB).filter(ModelUsageDB.day >= since):
        day = by_day.setdefault(row.day, {
            "day": row.day, "copilot_questions": 0, "calls": 0, "input_tokens": 0,
            "output_tokens": 0, "estimated_usd": 0.0, "copilot_usd": 0.0, "unpriced_calls": 0})
        day["copilot_questions"] += row.questions or 0
        day["calls"] += row.calls or 0
        day["input_tokens"] += row.input_tokens or 0
        day["output_tokens"] += row.output_tokens or 0
        if row.calls:
            if row.provider in table:
                p_in, p_out = table[row.provider]
                cost = ((row.input_tokens or 0) * p_in + (row.output_tokens or 0) * p_out) / 1e6
                day["estimated_usd"] += cost
                if row.purpose == "copilot":
                    day["copilot_usd"] += cost
            else:
                day["unpriced_calls"] += row.calls
    rows = sorted(by_day.values(), key=lambda d: d["day"], reverse=True)
    for d in rows:
        d["estimated_usd"] = round(d["estimated_usd"], 4)
        d["copilot_usd"] = round(d["copilot_usd"], 4)
    month = datetime.now(UTC).date().isoformat()[:7]
    in_month = [d for d in rows if d["day"].startswith(month)]
    return {
        "days": rows,
        "month": {
            "month": month,
            "copilot_questions": sum(d["copilot_questions"] for d in in_month),
            "calls": sum(d["calls"] for d in in_month),
            "estimated_usd": round(sum(d["estimated_usd"] for d in in_month), 2),
        },
        "prices_per_million": {k: {"input": v[0], "output": v[1]} for k, v in table.items()},
        "note": "Estimated from the token counts the models report, at the prices shown, "
                "before tax. The AWS bill is the authority; set BEDROCK_PRICE_IN_PER_M / "
                "BEDROCK_PRICE_OUT_PER_M if AWS's price for your region differs.",
    }

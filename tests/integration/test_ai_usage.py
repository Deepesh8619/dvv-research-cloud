"""AI usage is counted from what the models report, and priced for the admin page."""

import io
import json
import urllib.request

import pytest

from tests.integration.test_fielding_routes import client  # noqa: F401 - fixture


def _reply(prompt_tokens: int, completion_tokens: int):
    body = {"choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}}

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    return lambda *a, **k: Response(json.dumps(body).encode())


def _call(url: str, model: str):
    from src.ai.router import http_json

    request = urllib.request.Request(url, data=json.dumps({"model": model}).encode())
    return http_json(request, 5)


@pytest.mark.exercises_http_json
def test_every_model_reply_is_counted_and_priced(client, monkeypatch):  # noqa: F811
    from src.platform import usage

    test_client, sf, state = client
    monkeypatch.setattr("api.db.SessionLocal", sf)
    monkeypatch.setenv("BEDROCK_PRICE_IN_PER_M", "0.15")
    monkeypatch.setenv("BEDROCK_PRICE_OUT_PER_M", "0.60")
    bedrock = "https://bedrock-runtime.ap-south-1.amazonaws.com/openai/v1/chat/completions"

    monkeypatch.setattr(urllib.request, "urlopen", _reply(1_000_000, 0))
    _call(bedrock, "qwen.qwen3-32b-v1:0")  # an extraction call: "other"
    tagged = usage.purpose.set("copilot")
    try:
        usage.count_question()
        monkeypatch.setattr(urllib.request, "urlopen", _reply(0, 1_000_000))
        _call(bedrock, "qwen.qwen3-32b-v1:0")
        _call("http://127.0.0.1:11434/v1/chat/completions", "llama3.2:3b")  # local: free
    finally:
        usage.purpose.reset(tagged)

    body = test_client.get("/api/admin/ai-usage").json()
    (today,) = body["days"]
    assert today["copilot_questions"] == 1 and today["calls"] == 3
    assert today["input_tokens"] == 1_000_000 and today["output_tokens"] == 2_000_000
    assert today["estimated_usd"] == 0.75  # 1M in at 0.15 + 1M Bedrock out at 0.60
    assert today["copilot_usd"] == 0.6
    assert body["month"]["estimated_usd"] == 0.75

    state["role"] = "analyst"
    assert test_client.get("/api/admin/ai-usage").status_code == 403


@pytest.mark.exercises_http_json
def test_counting_never_breaks_a_model_call(monkeypatch):
    """No usage table, no database: the reply still comes back."""
    def broken():
        raise RuntimeError("no database")

    monkeypatch.setattr("api.db.SessionLocal", broken)
    monkeypatch.setattr(urllib.request, "urlopen", _reply(10, 5))
    assert _call("https://bedrock-runtime.x/chat", "m")["choices"][0]["message"]["content"] == "ok"

"""Model router: backend dispatch, OpenAI structured generation, and the
auto-route provider selection for questionnaire extraction.

Network is never hit — `router.http_json` is monkeypatched to return a canned
OpenAI-shaped response (or raise a network error), matching how the sibling
navigator tests stub the same helper. Env vars are toggled with monkeypatch so
key-presence logic reflects the test's intent, not the developer's shell.
"""

from __future__ import annotations

import json
import urllib.error

import pytest

from src.ai import router
from src.ai.router import (
    AIResponse,
    BackendUnavailable,
    ModelOutputError,
    _default_extraction_route,
    _openai_generate,
    run_task,
)

_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
}


def _openai_body(arguments: str) -> dict:
    """An OpenAI chat-completions response carrying a forced `emit` tool call."""
    return {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {"function": {"name": "emit", "arguments": arguments}}
                    ]
                }
            }
        ]
    }


# --- _openai_generate: happy path ------------------------------------------


def test_openai_generate_happy_path(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    captured = {}

    def fake_http_json(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = {k.lower(): v for k, v in request.header_items()}
        captured["body"] = json.loads(request.data)
        return _openai_body(json.dumps({"answer": "hello"}))

    monkeypatch.setattr(router, "http_json", fake_http_json)

    result = _openai_generate("gpt-4o", "extract this", _SCHEMA, timeout=30)

    assert result == {"answer": "hello"}
    # Request shape matches navigator.py's OpenAI conventions.
    assert captured["url"] == "https://api.openai.com/v1/chat/completions"
    assert captured["headers"]["authorization"] == "Bearer sk-test"
    body = captured["body"]
    assert body["model"] == "gpt-4o"
    assert body["tool_choice"] == {"type": "function", "function": {"name": "emit"}}
    fn = body["tools"][0]["function"]
    assert fn["name"] == "emit"
    assert fn["parameters"] == _SCHEMA


# --- _openai_generate: missing key -> config_error --------------------------


def test_openai_generate_missing_key_is_config_error(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(BackendUnavailable) as exc:
        _openai_generate("gpt-4o", "p", _SCHEMA, timeout=30)
    assert exc.value.config_error is True


# --- _openai_generate: network failure -> transient (not config) ------------


def test_openai_generate_network_failure_is_transient(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    def boom(request, timeout):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(router, "http_json", boom)

    with pytest.raises(BackendUnavailable) as exc:
        _openai_generate("gpt-4o", "p", _SCHEMA, timeout=30)
    assert exc.value.config_error is False


# --- _openai_generate: schema-invalid response -> ModelOutputError ----------


def test_openai_generate_missing_required_key_raises_model_error(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    def fake_http_json(request, timeout):
        # Valid JSON but missing the required "answer" key.
        return _openai_body(json.dumps({"wrong": "x"}))

    monkeypatch.setattr(router, "http_json", fake_http_json)

    with pytest.raises(ModelOutputError):
        _openai_generate("gpt-4o", "p", _SCHEMA, timeout=30)


def test_openai_generate_no_tool_call_raises_model_error(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    def fake_http_json(request, timeout):
        return {"choices": [{"message": {"content": "sorry, no tool call"}}]}

    monkeypatch.setattr(router, "http_json", fake_http_json)

    with pytest.raises(ModelOutputError):
        _openai_generate("gpt-4o", "p", _SCHEMA, timeout=30)


# --- _default_extraction_route: key-driven provider selection ---------------


def test_default_route_prefers_claude_when_anthropic_key_set(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai")
    route = _default_extraction_route()
    assert route["backend"] == "claude"


def test_default_route_uses_openai_when_only_openai_key_set(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai")
    route = _default_extraction_route()
    assert route["backend"] == "openai"
    assert route["model"] == "gpt-4o"


def test_default_route_fails_clearly_when_no_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(BackendUnavailable) as exc:
        _default_extraction_route()
    assert exc.value.config_error is True


# --- run_task: "auto" backend resolves and dispatches to openai -------------


def test_run_task_auto_dispatches_to_openai_with_only_openai_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai")

    def fake_http_json(request, timeout):
        assert "api.openai.com" in request.full_url
        return _openai_body(json.dumps({"answer": "ok"}))

    monkeypatch.setattr(router, "http_json", fake_http_json)

    resp = run_task("questionnaire.extract", "prompt", _SCHEMA)
    assert isinstance(resp, AIResponse)
    assert resp.backend == "openai"
    assert resp.model == "gpt-4o"
    assert resp.content == {"answer": "ok"}


def test_run_task_unknown_backend_raises(monkeypatch):
    routes = {"x.y": {"backend": "nope", "model": "m"}}
    with pytest.raises(ValueError):
        run_task("x.y", "p", _SCHEMA, routes=routes)

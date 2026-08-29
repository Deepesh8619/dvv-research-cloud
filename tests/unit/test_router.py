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
    BackendUnavailable,
    ModelOutputError,
    _extraction_chain,
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


# --- _extraction_chain: Ollama-first fallback ordering ----------------------


def test_chain_is_ollama_first_then_configured_cloud(monkeypatch):
    # Opt out of open-weights-only mode (the default) to see the full chain.
    monkeypatch.setenv("LLM_OPEN_WEIGHTS_ONLY", "0")
    monkeypatch.setenv("GROQ_API_KEY", "gk")
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    order = [c["backend"] for c in _extraction_chain()]
    assert order == ["ollama", "groq", "openai", "claude"]


def test_chain_omits_cloud_backends_without_keys(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # Ollama is always attempted (may be reachable locally; fails fast if not).
    assert [c["backend"] for c in _extraction_chain()] == ["ollama"]


def test_chain_includes_only_configured_cloud(monkeypatch):
    monkeypatch.setenv("LLM_OPEN_WEIGHTS_ONLY", "0")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert [c["backend"] for c in _extraction_chain()] == ["ollama", "openai"]


# --- run_task: "auto" walks Ollama -> Groq -> OpenAI, escalating on failure --


def test_run_task_auto_falls_through_ollama_to_openai(monkeypatch):
    """Ollama unreachable -> chain escalates to the configured OpenAI backend."""
    monkeypatch.setenv("LLM_OPEN_WEIGHTS_ONLY", "0")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai")

    def fake_http_json(request, timeout):
        if "11434" in request.full_url:  # Ollama attempt
            raise urllib.error.URLError("connection refused")
        assert "api.openai.com" in request.full_url
        return _openai_body(json.dumps({"answer": "ok"}))

    monkeypatch.setattr(router, "http_json", fake_http_json)

    resp = run_task("questionnaire.extract", "prompt", _SCHEMA)
    assert resp.backend == "openai"
    assert resp.model == "gpt-4o"
    assert resp.content == {"answer": "ok"}


def test_run_task_auto_prefers_ollama_when_it_answers(monkeypatch):
    """A healthy local Ollama serves the request without touching the cloud."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai")

    def fake_http_json(request, timeout):
        assert "11434" in request.full_url  # never reaches the cloud
        return {"response": json.dumps({"answer": "local"})}

    monkeypatch.setattr(router, "http_json", fake_http_json)

    resp = run_task("questionnaire.extract", "prompt", _SCHEMA)
    assert resp.backend == "ollama"
    assert resp.content == {"answer": "local"}


def test_run_task_auto_all_missing_is_config_error(monkeypatch):
    """No Ollama and no cloud keys -> single actionable config error."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def boom(request, timeout):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(router, "http_json", boom)

    with pytest.raises(BackendUnavailable):
        run_task("questionnaire.extract", "prompt", _SCHEMA)


def test_run_task_unknown_backend_raises(monkeypatch):
    routes = {"x.y": {"backend": "nope", "model": "m"}}
    with pytest.raises(ValueError):
        run_task("x.y", "p", _SCHEMA, routes=routes)


# --- open-weights-only mode -------------------------------------------------


def test_open_weights_only_drops_proprietary_backends(monkeypatch):
    """A stray proprietary key must not route client questionnaires off-catalog.

    This is a contractual constraint in market research (client NDAs), not a
    preference, so the guarantee is asserted rather than assumed.
    """
    from src.ai.router import _extraction_chain

    for k in ("GROQ_API_KEY", "HF_TOKEN", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(k, "x")
    monkeypatch.delenv("LLM_OPEN_WEIGHTS_ONLY", raising=False)  # default is ON

    backends = [step["backend"] for step in _extraction_chain()]
    assert "openai" not in backends
    assert "claude" not in backends
    assert backends == ["ollama", "groq", "huggingface"]


def test_opting_out_restores_the_proprietary_tail(monkeypatch):
    from src.ai.router import _extraction_chain

    for k in ("GROQ_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(k, "x")
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setenv("LLM_OPEN_WEIGHTS_ONLY", "0")

    backends = [step["backend"] for step in _extraction_chain()]
    assert backends == ["ollama", "groq", "openai", "claude"]


def test_huggingface_backend_needs_a_token(monkeypatch):
    from src.ai.router import BackendUnavailable, _huggingface_generate

    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGINGFACE_API_KEY", raising=False)
    with pytest.raises(BackendUnavailable) as exc:
        _huggingface_generate("Qwen/Qwen3-32B", "hi", {}, 5)
    assert exc.value.config_error is True

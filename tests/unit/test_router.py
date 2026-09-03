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
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    order = list(dict.fromkeys(c["backend"] for c in _extraction_chain()))
    assert order == ["ollama", "groq", "openai", "claude"]


def test_chain_omits_cloud_backends_without_keys(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # Ollama is always attempted (may be reachable locally; fails fast if not).
    assert [c["backend"] for c in _extraction_chain()] == ["ollama"]


def test_chain_includes_only_configured_cloud(monkeypatch):
    monkeypatch.setenv("LLM_OPEN_WEIGHTS_ONLY", "0")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
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
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
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

    # Every key set explicitly — including CEREBRAS_API_KEY, so this asserts the
    # full open-weight chain rather than passing by accident on a machine that
    # happens not to have one of them configured.
    for k in (
        "GROQ_API_KEY",
        "CEREBRAS_API_KEY",
        "HF_TOKEN",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.setenv(k, "x")
    monkeypatch.delenv("LLM_OPEN_WEIGHTS_ONLY", raising=False)  # default is ON

    # De-duplicated: groq contributes several MODELS (separate daily
    # quotas), which is not what this test is about.
    backends = list(dict.fromkeys(s["backend"] for s in _extraction_chain()))
    assert "openai" not in backends
    assert "claude" not in backends
    assert backends == ["ollama", "groq", "cerebras", "huggingface"]


def test_opting_out_restores_the_proprietary_tail(monkeypatch):
    from src.ai.router import _extraction_chain

    for k in ("GROQ_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(k, "x")
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    monkeypatch.setenv("LLM_OPEN_WEIGHTS_ONLY", "0")

    backends = list(dict.fromkeys(s["backend"] for s in _extraction_chain()))
    assert backends == ["ollama", "groq", "openai", "claude"]


def test_huggingface_backend_needs_a_token(monkeypatch):
    from src.ai.router import BackendUnavailable, _huggingface_generate

    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    monkeypatch.delenv("HUGGINGFACE_API_KEY", raising=False)
    with pytest.raises(BackendUnavailable) as exc:
        _huggingface_generate("Qwen/Qwen3-32B", "hi", {}, 5)
    assert exc.value.config_error is True


# --- 429 handling: rate limits are transient, not "backend unavailable" -----


def _http_error(code, body, headers=None):
    import io

    return urllib.error.HTTPError(
        "https://api.groq.com/x", code, "Too Many Requests",
        headers or {}, io.BytesIO(json.dumps(body).encode()),
    )


@pytest.mark.exercises_http_json
def test_429_is_retried_using_the_providers_own_wait_hint(monkeypatch):
    """Groq's free tier caps TPM and one extraction prompt can exhaust it.

    Treating that as a dead backend burned the fallback chain and landed on a
    WEAKER model for work the good one would do seconds later — in a real run
    this failed 10 of 12 extractions.
    """
    slept = []
    monkeypatch.setattr(router.time, "sleep", lambda s: slept.append(s))

    calls = {"n": 0}

    def flaky(request, timeout, context=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http_error(
                429,
                {"error": {"message": "Rate limit reached. Please try again in 14.69s."}},
            )
        raise AssertionError("should not reach here in this stub")

    # Drive http_json's real retry loop with a urlopen that 429s once.
    import urllib.request as _req

    def fake_urlopen(request, timeout=None, context=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http_error(
                429,
                {"error": {"message": "Rate limit reached. Please try again in 14.69s."}},
            )

        class _R:
            def read(self):
                return json.dumps({"ok": True}).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return _R()

    monkeypatch.setattr(_req, "urlopen", fake_urlopen)
    out = router.http_json(_req.Request("https://api.groq.com/x"), timeout=10)

    assert out == {"ok": True}
    # Waited the amount the provider asked for, rather than guessing.
    assert slept == [14.69]


@pytest.mark.exercises_http_json
def test_429_gives_up_after_bounded_retries(monkeypatch):
    """A genuinely exhausted quota must still fail, not hang forever."""
    monkeypatch.setattr(router.time, "sleep", lambda s: None)
    import urllib.request as _req

    def always_429(request, timeout=None, context=None):
        raise _http_error(429, {"error": {"message": "Rate limit reached."}})

    monkeypatch.setattr(_req, "urlopen", always_429)
    with pytest.raises(urllib.error.URLError) as exc:
        router.http_json(_req.Request("https://api.groq.com/x"), timeout=10)
    assert "429" in str(exc.value)


@pytest.mark.exercises_http_json
def test_non_429_errors_are_not_retried(monkeypatch):
    """A 402 or 401 is not transient — retrying wastes time and hides the cause."""
    sleeps = []
    monkeypatch.setattr(router.time, "sleep", lambda s: sleeps.append(s))
    import urllib.request as _req

    def payment_required(request, timeout=None, context=None):
        raise _http_error(402, {"error": {"message": "Payment required"}})

    monkeypatch.setattr(_req, "urlopen", payment_required)
    with pytest.raises(urllib.error.URLError) as exc:
        router.http_json(_req.Request("https://api.cerebras.ai/x"), timeout=10)
    assert "402" in str(exc.value)
    assert sleeps == []


def test_retry_after_header_wins_over_message_text(monkeypatch):
    monkeypatch.setattr(router.time, "sleep", lambda s: None)
    err = _http_error(
        429, {"error": {"message": "try again in 99s"}}, headers={"Retry-After": "3"}
    )
    assert router._retry_after_seconds(err, "try again in 99s") == 3.0


# --- quota exhaustion: stop paying for a backend that said "come back later" --


@pytest.mark.exercises_http_json
def test_long_429_becomes_quota_exhausted_not_a_sleep(monkeypatch):
    """A daily limit says 'try again in 14m'. Sleeping through that per call
    would cost over an hour across one document."""
    slept = []
    monkeypatch.setattr(router.time, "sleep", lambda s: slept.append(s))
    import urllib.request as _req

    def daily_limit(request, timeout=None, context=None):
        raise _http_error(429, {"error": {"message":
            "Rate limit reached on tokens per day (TPD). Please try again in 14m10.1s."}})

    monkeypatch.setattr(_req, "urlopen", daily_limit)
    # "14m10.1s" has no bare-seconds hint, so it backs off normally; the
    # seconds-form below is what a provider sends for the long window.
    def daily_limit_seconds(request, timeout=None, context=None):
        raise _http_error(429, {"error": {"message":
            "tokens per day (TPD) exceeded. Please try again in 850.5s."}})

    monkeypatch.setattr(_req, "urlopen", daily_limit_seconds)
    with pytest.raises(router.QuotaExhausted) as exc:
        router.http_json(_req.Request("https://api.groq.com/x"), timeout=10)
    assert exc.value.retry_after == 850.5
    # Crucially: it did NOT sleep through a 14-minute window.
    assert slept == []


def test_exhausted_backend_is_skipped_until_its_window_passes(monkeypatch):
    """The point: a 75-question document must not re-probe a dead backend 75x."""
    router._backend_cooldown.clear()
    # Every Groq model must be cooling, or the chain simply uses the next one.
    for model in [router._EXTRACTION_MODELS["groq"], *router.GROQ_FALLBACK_MODELS]:
        router.note_backend_exhausted("groq", 600, model)
    assert router.backend_available_in("groq", router._EXTRACTION_MODELS["groq"]) > 0

    monkeypatch.setenv("GROQ_API_KEY", "gk")
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    monkeypatch.delenv("HF_TOKEN", raising=False)

    calls = []

    def fake_http_json(request, timeout):
        calls.append(request.full_url)
        raise urllib.error.URLError("ollama down")

    monkeypatch.setattr(router, "http_json", fake_http_json)
    with pytest.raises(BackendUnavailable) as exc:
        run_task("questionnaire.extract", "p", _SCHEMA)

    # Ollama was tried; Groq was skipped without a network call.
    assert not any("groq.com" in u for u in calls)
    assert "out of quota" in str(exc.value)
    router._backend_cooldown.clear()


def test_cooldown_expires(monkeypatch):
    router._backend_cooldown.clear()
    router.note_backend_exhausted("groq", -1, "m")  # already in the past
    assert router.backend_available_in("groq", "m") == 0
    router._backend_cooldown.clear()


# --- output budget: reserved tokens are billed, so do not over-reserve -------


def test_max_tokens_is_not_over_reserved(monkeypatch):
    """Providers bill prompt + schema + max_tokens. A 4096 reservation was two
    thirds of every bill for questions whose largest real output was 493."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    seen = {}

    def capture(request, timeout):
        seen["body"] = json.loads(request.data)
        return _openai_body(json.dumps({"answer": "ok"}))

    monkeypatch.setattr(router, "http_json", capture)
    _openai_generate("gpt-4o", "p", _SCHEMA, timeout=30)

    assert seen["body"]["max_tokens"] == router.MAX_OUTPUT_TOKENS
    # Comfortably above the largest observed question (493) and far below the
    # old 4096.
    assert 600 < router.MAX_OUTPUT_TOKENS < 4096


# --- errors a researcher reads, not a deployment engineer --------------------


def test_quota_message_tells_a_researcher_what_to_do():
    msg = router.friendly_backend_error(
        "all backends failed — ollama: unreachable at http://localhost:11434 "
        "| groq: out of quota for another 20 min"
    )
    assert "20 minutes" in msg
    assert "Nothing was lost" in msg
    # None of the deployment detail leaks onto the upload screen.
    for leak in ("ollama", "localhost", "groq", "11434", "Errno"):
        assert leak not in msg


def test_missing_key_is_named_as_an_admin_problem():
    msg = router.friendly_backend_error("GROQ_API_KEY not set")
    assert "administrator" in msg


def test_an_unknown_failure_still_says_something_useful():
    msg = router.friendly_backend_error("connection reset by peer")
    assert "could not be reached" in msg
    assert "try uploading it again" in msg.lower()


# --- per-model quotas: a second model is real extra free capacity ------------


def test_groq_fallback_models_are_added_to_the_chain(monkeypatch):
    """Quotas are metered per MODEL, so a second model has its own allowance."""
    monkeypatch.setenv("GROQ_API_KEY", "gk")
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    router._backend_cooldown.clear()

    models = [c["model"] for c in _extraction_chain() if c["backend"] == "groq"]
    assert models[0] == router._EXTRACTION_MODELS["groq"]   # primary first
    assert len(models) > 1
    assert len(models) == len(set(models))                  # no duplicates


def test_cooldown_is_per_model_not_per_backend():
    """gpt-oss-120b running out says nothing about gpt-oss-20b's budget."""
    router._backend_cooldown.clear()
    router.note_backend_exhausted("groq", 600, "openai/gpt-oss-120b")

    assert router.backend_available_in("groq", "openai/gpt-oss-120b") > 0
    assert router.backend_available_in("groq", "openai/gpt-oss-20b") == 0
    router._backend_cooldown.clear()


def test_fallback_models_can_be_disabled(monkeypatch):
    """Uniformity matters more than capacity for some deployments."""
    monkeypatch.setenv("GROQ_FALLBACK_MODELS", "")
    import importlib

    importlib.reload(router)
    try:
        assert router.GROQ_FALLBACK_MODELS == []
    finally:
        monkeypatch.delenv("GROQ_FALLBACK_MODELS", raising=False)
        importlib.reload(router)

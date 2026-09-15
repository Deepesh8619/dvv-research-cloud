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
    _extraction_chain,
    run_task,
)

_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
}


def _tool_call_body(arguments: str) -> dict:
    """A chat-completions response carrying a forced `emit` tool call (Groq/Cerebras shape)."""
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




















# --- _extraction_chain: Ollama-first fallback ordering ----------------------


def test_chain_is_ollama_first_then_configured_cloud(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gk")
    monkeypatch.setenv("CEREBRAS_API_KEY", "csk")
    monkeypatch.delenv("HF_TOKEN", raising=False)
    order = list(dict.fromkeys(c["backend"] for c in _extraction_chain()))
    assert order == ["ollama", "groq", "cerebras"]


def test_chain_omits_cloud_backends_without_keys(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    # Ollama is always attempted (may be reachable locally; fails fast if not).
    assert [c["backend"] for c in _extraction_chain()] == ["ollama"]


def test_chain_includes_only_configured_cloud(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setenv("CEREBRAS_API_KEY", "csk")
    assert [c["backend"] for c in _extraction_chain()] == ["ollama", "cerebras"]


# --- run_task: "auto" walks Ollama -> Groq -> OpenAI, escalating on failure --


def test_run_task_auto_falls_through_ollama_to_cerebras(monkeypatch):
    """Ollama unreachable -> chain escalates to the configured Cerebras backend."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setenv("CEREBRAS_API_KEY", "csk")

    def fake_http_json(request, timeout):
        if "11434" in request.full_url:  # Ollama attempt
            raise urllib.error.URLError("connection refused")
        assert "api.cerebras.ai" in request.full_url
        return _tool_call_body(json.dumps({"answer": "ok"}))

    monkeypatch.setattr(router, "http_json", fake_http_json)

    resp = run_task("questionnaire.extract", "prompt", _SCHEMA)
    assert resp.backend == "cerebras"
    assert resp.model == "qwen-3.8-27b"
    assert resp.content == {"answer": "ok"}


def test_run_task_auto_prefers_ollama_when_it_answers(monkeypatch):
    """A healthy local Ollama serves the request without touching the cloud."""
    monkeypatch.setenv("CEREBRAS_API_KEY", "csk")

    def fake_http_json(request, timeout):
        assert "11434" in request.full_url  # never reaches the cloud
        return {"response": json.dumps({"answer": "local"})}

    monkeypatch.setattr(router, "http_json", fake_http_json)

    resp = run_task("questionnaire.extract", "prompt", _SCHEMA)
    assert resp.backend == "ollama"
    assert resp.content == {"answer": "local"}


def test_run_task_auto_all_missing_is_config_error(monkeypatch):
    """No Ollama and no cloud keys -> single actionable config error."""
    for var in ("GROQ_API_KEY", "CEREBRAS_API_KEY", "HF_TOKEN"):
        monkeypatch.delenv(var, raising=False)

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
    monkeypatch.setenv("CEREBRAS_API_KEY", "csk")
    seen = {}

    def capture(request, timeout):
        seen["body"] = json.loads(request.data)
        return _tool_call_body(json.dumps({"answer": "ok"}))

    monkeypatch.setattr(router, "http_json", capture)
    router._cerebras_generate("qwen-3.8-27b", "p", _SCHEMA, timeout=30)

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
    # The default list is empty (Groq serves one Qwen today); the mechanism is
    # for when a server configures extra models with GROQ_FALLBACK_MODELS.
    monkeypatch.setattr(router, "GROQ_FALLBACK_MODELS", ["qwen/extra-model"])

    models = [c["model"] for c in _extraction_chain() if c["backend"] == "groq"]
    assert models[0] == router._EXTRACTION_MODELS["groq"]   # primary first
    assert models[1:] == ["qwen/extra-model"]
    assert len(models) == len(set(models))                  # no duplicates


def test_cooldown_is_per_model_not_per_backend():
    """One model running out says nothing about another model's budget."""
    router._backend_cooldown.clear()
    router.note_backend_exhausted("groq", 600, "qwen/qwen3.8-27b")

    assert router.backend_available_in("groq", "qwen/qwen3.8-27b") > 0
    assert router.backend_available_in("groq", "qwen/some-other-model") == 0
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


def _timeouts_seen(monkeypatch, cloud_key: bool) -> dict[str, int]:
    """Run one extraction and record the timeout each backend was given."""
    for var in ("GROQ_API_KEY", "HF_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    if cloud_key:
        monkeypatch.setenv("CEREBRAS_API_KEY", "csk")
    else:
        monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    seen: dict[str, int] = {}

    def fake_http_json(request, timeout):
        backend = "ollama" if "11434" in request.full_url else "cloud"
        seen[backend] = timeout
        if backend == "ollama":
            return {"response": json.dumps({"answer": "local"})}
        raise AssertionError("cloud should not be reached")

    monkeypatch.setattr(router, "http_json", fake_http_json)
    run_task("questionnaire.extract", "prompt", _SCHEMA, timeout=120)
    return seen


def test_a_local_only_deployment_waits_for_its_model(monkeypatch):
    """Ollama is the whole chain: cutting it off at 30s leaves nothing to answer."""
    assert _timeouts_seen(monkeypatch, cloud_key=False)["ollama"] == 120


def test_a_local_model_with_a_cloud_fallback_is_still_capped(monkeypatch):
    """A hung local model must not stall a request a cloud backend could answer."""
    assert _timeouts_seen(monkeypatch, cloud_key=True)["ollama"] == 30











# --- Open-source models only: no proprietary vendor is reachable ------------


def test_no_proprietary_ai_vendor_is_reachable_from_the_code():
    """DVV policy, enforced by absence: nothing in the app can call these vendors."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    offenders = [
        f"{path.relative_to(root)}: {needle}"
        for path in list((root / "src").rglob("*.py")) + list((root / "api").rglob("*.py"))
        for needle in ("api.openai.com", "api.anthropic.com", "ANTHROPIC_API_KEY", "OPENAI_API_KEY")
        if needle in path.read_text()
    ]
    assert offenders == []
    backends = {route["backend"] for route in router.DEFAULT_ROUTES.values()}
    assert backends <= {"auto", "ollama", "bedrock", "groq", "cerebras", "huggingface"}


def test_the_network_guard_covers_every_by_name_import():
    """conftest must refuse the network in the navigator too, not only the router."""
    import urllib.error

    from src.ai import navigator

    with pytest.raises(urllib.error.URLError, match="network disabled in tests"):
        navigator.http_json(object(), 1)



# --- Amazon Bedrock: the in-region provider on AWS ---------------------------


def test_bedrock_leads_the_cloud_providers_when_its_key_is_set(monkeypatch):
    monkeypatch.setenv("BEDROCK_API_KEY", "bk")
    monkeypatch.setenv("GROQ_API_KEY", "gk")
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    order = list(dict.fromkeys(c["backend"] for c in _extraction_chain()))
    assert order == ["ollama", "bedrock", "groq"]


def test_bedrock_is_called_in_the_configured_region_with_its_key(monkeypatch):
    monkeypatch.setenv("BEDROCK_API_KEY", "bk-secret")
    monkeypatch.setenv("BEDROCK_REGION", "eu-central-1")
    for var in ("GROQ_API_KEY", "CEREBRAS_API_KEY", "HF_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    seen = {}

    def fake(request, timeout):
        if "11434" in request.full_url:
            raise urllib.error.URLError("no local model")
        seen["url"] = request.full_url
        seen["auth"] = request.get_header("Authorization")
        seen["model"] = json.loads(request.data)["model"]
        return _tool_call_body(json.dumps({"answer": "ok"}))

    monkeypatch.setattr(router, "http_json", fake)
    resp = run_task("questionnaire.extract", "prompt", _SCHEMA)
    assert resp.backend == "bedrock"
    assert seen["url"] == (
        "https://bedrock-runtime.eu-central-1.amazonaws.com/openai/v1/chat/completions"
    )
    assert seen["auth"] == "Bearer bk-secret"
    assert seen["model"] == "qwen.qwen3-32b-v1:0"  # the runtime endpoint's ID


def test_the_bedrock_endpoint_can_be_switched_without_code(monkeypatch):
    monkeypatch.setenv("BEDROCK_BASE_URL", "https://bedrock-mantle.ap-south-1.api.aws/v1/")
    assert router.bedrock_chat_url() == (
        "https://bedrock-mantle.ap-south-1.api.aws/v1/chat/completions"
    )
    monkeypatch.setenv("BEDROCK_MODEL", "some.other-endpoint-id")
    assert router.bedrock_model() == "some.other-endpoint-id"  # an override always wins


def test_the_copilot_tries_bedrock_first(monkeypatch):
    from src.ai import copilot

    monkeypatch.setenv("BEDROCK_API_KEY", "bk")
    monkeypatch.setenv("GROQ_API_KEY", "gk")
    labels = [p[0] for p in copilot._providers()]
    assert labels[:2] == ["Bedrock", "Groq"]
    assert labels[-1] == "local model"



def test_no_default_model_is_made_by_openai(monkeypatch):
    """DVV policy: open-source models only, and none of them OpenAI's."""
    import pathlib

    from src.ai import copilot, navigator

    for var in ("BEDROCK_MODEL", "GROQ_MODEL", "CEREBRAS_MODEL", "HF_MODEL", "COPILOT_GROQ_MODEL"):
        monkeypatch.delenv(var, raising=False)
    defaults = [router.bedrock_model(), router.GROQ_DEFAULT_MODEL, router.CEREBRAS_DEFAULT_MODEL,
                router.HF_DEFAULT_MODEL, navigator.GROQ_MODEL, navigator.CEREBRAS_MODEL,
                *router.GROQ_FALLBACK_MODELS, *(r["model"] for r in router.DEFAULT_ROUTES.values())]
    monkeypatch.setenv("BEDROCK_API_KEY", "bk")
    monkeypatch.setenv("GROQ_API_KEY", "gk")
    monkeypatch.setenv("CEREBRAS_API_KEY", "ck")
    defaults += [p[3] for p in copilot._providers()]
    assert not [m for m in defaults if "gpt" in m.lower() or m.lower().startswith("openai")]
    root = pathlib.Path(__file__).resolve().parents[2]
    policy = (root / "deploy" / "aws" / "app-policy.json").read_text()
    assert "openai" not in policy

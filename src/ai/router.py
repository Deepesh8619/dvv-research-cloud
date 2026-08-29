"""Model router — every engine calls AI through here, never a backend directly.

Backends (architecture §5): Ollama (local, free, private) and Claude (cloud,
judgment-heavy). Routes are config, not code: each task name maps to a backend
and model, with confidence-gated escalation handled by callers.

Local models run in plain JSON mode with the schema described in the prompt —
full grammar-constrained decoding proved pathologically slow on low-RAM
machines. Outputs are validated in code instead; invalid output raises
ModelOutputError so callers can escalate or degrade.
"""

from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from src.env import load_env

# _EXTRACTION_MODELS and the key-presence checks read os.environ at import
# time, so a local .env has to be in place before this module finishes loading.
load_env()

# Groq's Cloudflare front rejects urllib's default "Python-urllib/3.x" User-Agent
# with a 403 (Cloudflare error 1010, generic bot-signature block) — reproduced by
# swapping in curl's UA and getting 200. Anthropic hasn't been observed to block
# it, but there's no reason to leave any external AI call looking like a bot
# scraper, so every non-local request sends this.
USER_AGENT = "dvv-research-cloud/1.0"


def _ssl_context() -> ssl.SSLContext | None:
    """SSL context with certifi's CA bundle when available.

    macOS system Python ships without CA certificates wired up, so plain
    urlopen to https://api.anthropic.com fails with CERTIFICATE_VERIFY_FAILED.
    certifi is installed as a transitive dep almost everywhere; fall back to
    default verification if it isn't.
    """
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return None


def http_json(request: urllib.request.Request, timeout: int) -> dict:
    """urlopen with the certifi SSL context, returning parsed JSON.

    HTTP error responses re-raise as URLError with the API's own error
    message — "credit balance is too low" is actionable, "HTTP 400" is not.
    """
    try:
        with urllib.request.urlopen(
            request, timeout=timeout, context=_ssl_context()
        ) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read()).get("error", {}).get("message", "")
        except Exception:
            detail = ""
        raise urllib.error.URLError(f"HTTP {exc.code}: {detail or exc.reason}") from exc


DEFAULT_ROUTES: dict[str, dict[str, str]] = {
    "intake.classify": {"backend": "ollama", "model": "llama3.2:3b"},
    # Verbatim coding rides the same Ollama → Groq → OpenAI → Claude chain as
    # questionnaire extraction: the previous hardcoded backends meant coding
    # silently failed anywhere the one named backend was missing (Render has
    # no Ollama and no Anthropic key — production never coded a verbatim).
    "coding.apply": {"backend": "auto", "model": ""},
    "coding.codeframe": {"backend": "auto", "model": ""},
    "extract.questionnaire": {"backend": "claude", "model": "claude-fable-5"},
    # Two-pass questionnaire extraction (src/engines/questionnaire/extractor.py).
    # Judgment-heavy, hallucination-sensitive structured extraction. Unlike the
    # sibling tasks above these are NOT hardcoded to a provider: the sentinel
    # backend "auto" makes run_task walk the Ollama → Groq → OpenAI → Claude
    # fallback chain (_extraction_chain()) — local first (free/private), then
    # whichever cloud backends have a key — trying each until one succeeds.
    "questionnaire.extract": {"backend": "auto", "model": ""},
    "questionnaire.verify": {"backend": "auto", "model": ""},
    # Whole-document segmentation reconciliation (safety net over the regex
    # segmenter, src/engines/questionnaire/reconciler.py). Same "auto" chain as
    # extract/verify — local first, then whichever cloud key is present.
    "questionnaire.reconcile_segmentation": {"backend": "auto", "model": ""},
    "report.narrative": {"backend": "claude", "model": "claude-fable-5"},
    # study.chat routes through src/ai/navigator.py (multi-tool agent loop on
    # Claude Haiku) rather than run_task — kept here so the task inventory in
    # one place stays truthful. study.chat.basic is its local fallback:
    # single shot, one tool max, free.
    "study.chat": {"backend": "claude", "model": "claude-haiku-4-5-20251001"},
    "study.chat.basic": {"backend": "ollama", "model": "llama3.2:3b"},
}


class BackendUnavailable(RuntimeError):
    """Raised when a backend can't serve the request (offline, no key, no model).

    `config_error` distinguishes *actionable-once* config faults (a missing API
    key — one fix in one place fixes every request) from genuinely *transient*
    outages (unreachable host, timeout — retryable, may clear on its own). The
    questionnaire extractor's circuit breaker and flag taxonomy key off this
    boolean rather than string-sniffing the message, so the two failure classes
    stay distinguishable even if a message's wording changes.
    """

    def __init__(self, *args: object, config_error: bool = False) -> None:
        super().__init__(*args)
        self.config_error = config_error


class ModelOutputError(RuntimeError):
    """Raised when a model responds but the output doesn't match the schema."""


@dataclass
class AIResponse:
    content: dict[str, Any]  # parsed JSON per the task's schema
    backend: str
    model: str


def _parse_json_output(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ModelOutputError(f"no JSON object in model output: {text[:200]}") from None
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ModelOutputError(f"invalid JSON from model: {text[:200]}") from exc


def _validate(content: dict, schema: dict) -> dict:
    missing = [k for k in schema.get("required", []) if k not in content]
    if missing:
        raise ModelOutputError(f"model output missing required keys: {missing}")
    return content


def _schema_to_instruction(schema: dict) -> str:
    """Turn a JSON schema into field descriptions + a concrete example.

    Small local models reliably imitate an example but tend to echo a raw
    schema document back, so never show them the schema itself.
    """
    lines, example = [], {}
    for name, prop in schema.get("properties", {}).items():
        kind = prop.get("type", "string")
        if "enum" in prop:
            lines.append(f"- {name}: exactly one of {prop['enum']}")
            example[name] = prop["enum"][0]
        elif kind == "number":
            lines.append(f"- {name}: a number")
            example[name] = 0.9
        elif kind == "integer":
            lines.append(f"- {name}: an integer")
            example[name] = 1
        elif kind == "array":
            lines.append(f"- {name}: a list")
            example[name] = []
        else:
            lines.append(f"- {name}: a short string")
            example[name] = "..."
    return (
        "Respond with ONLY one JSON object (no other text) with exactly these keys:\n"
        + "\n".join(lines)
        + f"\nFor example: {json.dumps(example)}"
    )


def _ollama_generate(model: str, prompt: str, schema: dict, timeout: int) -> dict:
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    full_prompt = f"{prompt}\n\n{_schema_to_instruction(schema)}"
    payload = json.dumps(
        {
            "model": model,
            "prompt": full_prompt,
            "format": "json",
            "stream": False,
            "keep_alive": "15m",
            "options": {"temperature": 0},
        }
    ).encode()
    request = urllib.request.Request(
        f"{host}/api/generate", data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        body = http_json(request, timeout)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise BackendUnavailable(f"ollama unreachable at {host}: {exc}") from exc
    return _validate(_parse_json_output(body["response"]), schema)


def _claude_generate(model: str, prompt: str, schema: dict, timeout: int) -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise BackendUnavailable("ANTHROPIC_API_KEY not set", config_error=True)
    payload = json.dumps(
        {
            "model": model,
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": prompt}],
            "tools": [
                {
                    "name": "emit",
                    "description": "Emit the structured result",
                    "input_schema": schema,
                }
            ],
            "tool_choice": {"type": "tool", "name": "emit"},
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
        body = http_json(request, timeout)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise BackendUnavailable(f"claude API unreachable: {exc}") from exc
    for block in body.get("content", []):
        if block.get("type") == "tool_use":
            return _validate(block["input"], schema)
    raise ModelOutputError("claude returned no structured output")


def _openai_compatible_generate(
    endpoint: str, api_key: str, model: str, prompt: str, schema: dict, timeout: int, provider: str
) -> dict:
    """Structured extraction via any OpenAI-compatible chat-completions endpoint.

    Both OpenAI and Groq speak this exact shape (forced schema-valid function
    call, arguments returned as a JSON string under
    choices[0].message.tool_calls), so one implementation serves both — only the
    endpoint URL, key, and provider label differ.
    """
    payload = json.dumps(
        {
            "model": model,
            "max_tokens": 4096,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "emit",
                        "description": "Emit the structured result",
                        "parameters": schema,
                    },
                }
            ],
            "tool_choice": {"type": "function", "function": {"name": "emit"}},
        }
    ).encode()
    request = urllib.request.Request(
        endpoint,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        body = http_json(request, timeout)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise BackendUnavailable(f"{provider} API unreachable: {exc}") from exc
    choices = body.get("choices", [])
    if not choices:
        raise ModelOutputError(f"{provider} returned no choices")
    tool_calls = choices[0].get("message", {}).get("tool_calls") or []
    if not tool_calls:
        raise ModelOutputError(f"{provider} returned no structured output")
    arguments = tool_calls[0].get("function", {}).get("arguments", "")
    return _validate(_parse_json_output(arguments), schema)


def _groq_generate(model: str, prompt: str, schema: dict, timeout: int) -> dict:
    """Structured extraction via Groq's OpenAI-compatible chat-completions API."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise BackendUnavailable("GROQ_API_KEY not set", config_error=True)
    return _openai_compatible_generate(
        "https://api.groq.com/openai/v1/chat/completions",
        api_key,
        model,
        prompt,
        schema,
        timeout,
        "Groq",
    )


def _cerebras_generate(model: str, prompt: str, schema: dict, timeout: int) -> dict:
    """Structured extraction via Cerebras' OpenAI-compatible API.

    Worth its own backend rather than reaching Cerebras through the HF router:
    HF bills its own credits (~$0.10/month free), whereas a direct Cerebras key
    gets Cerebras' own far larger free daily allowance — the difference between
    finishing a full-document benchmark and running out mid-run.

    Free tiers vary in whether they train on submitted data. Client
    questionnaires are NDA-bound, so check the current terms before pointing
    this at real client work; benchmarking with test documents is the safe use.
    """
    api_key = os.environ.get("CEREBRAS_API_KEY")
    if not api_key:
        raise BackendUnavailable("CEREBRAS_API_KEY not set", config_error=True)
    return _openai_compatible_generate(
        "https://api.cerebras.ai/v1/chat/completions",
        api_key,
        model,
        prompt,
        schema,
        timeout,
        "Cerebras",
    )


def _huggingface_generate(model: str, prompt: str, schema: dict, timeout: int) -> dict:
    """Structured extraction via Hugging Face Inference Providers.

    HF's router is OpenAI-compatible and fans out to the provider actually
    serving a model (Cerebras, Together, Novita, Fireworks, ...), so ONE key
    reaches the whole open-weight catalog instead of one key per vendor. That
    matters here because it makes swapping the extraction model a config change
    rather than a new backend: any open model HF serves is reachable by putting
    its repo id in HF_MODEL.
    """
    api_key = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_API_KEY")
    if not api_key:
        raise BackendUnavailable("HF_TOKEN not set", config_error=True)
    # HF's own docs recommend PINNING a provider for structured output, because
    # schema support varies between the providers behind the auto-router and a
    # mismatch shows up as a malformed/absent tool call rather than a clean
    # error. HF_PROVIDER pins it (e.g. "cerebras", "together", "fireworks-ai");
    # unset keeps auto-routing, which is fine for chat but is the first thing to
    # change if extraction starts returning no structured output.
    provider = os.environ.get("HF_PROVIDER", "").strip()
    base = f"https://router.huggingface.co/{provider}/v1" if provider else (
        "https://router.huggingface.co/v1"
    )
    return _openai_compatible_generate(
        f"{base}/chat/completions",
        api_key,
        model,
        prompt,
        schema,
        timeout,
        "HuggingFace",
    )


def _openai_generate(model: str, prompt: str, schema: dict, timeout: int) -> dict:
    """Structured extraction via OpenAI chat-completions function calling.

    Mirrors _claude_generate's contract (forced schema-valid tool call, same
    error taxonomy) but in OpenAI's request/response shape — the same shape
    navigator.py already uses (chat/completions, Bearer auth, function tools
    with a `parameters` JSON Schema). The one wrapper difference from Claude:
    OpenAI wants `tools=[{"type":"function","function":{name, parameters}}]`
    with `tool_choice` naming the function, and returns the arguments as a JSON
    *string* under choices[0].message.tool_calls, versus Claude's `input_schema`
    + parsed `tool_use` block.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise BackendUnavailable("OPENAI_API_KEY not set", config_error=True)
    payload = json.dumps(
        {
            "model": model,
            "max_tokens": 4096,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "emit",
                        "description": "Emit the structured result",
                        "parameters": schema,
                    },
                }
            ],
            "tool_choice": {"type": "function", "function": {"name": "emit"}},
        }
    ).encode()
    request = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        body = http_json(request, timeout)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise BackendUnavailable(f"OpenAI API unreachable: {exc}") from exc
    choices = body.get("choices", [])
    if not choices:
        raise ModelOutputError("openai returned no choices")
    tool_calls = choices[0].get("message", {}).get("tool_calls") or []
    if not tool_calls:
        raise ModelOutputError("openai returned no structured output")
    arguments = tool_calls[0].get("function", {}).get("arguments", "")
    return _validate(_parse_json_output(arguments), schema)


# Env vars that gate the cloud AI backends. Reported as presence booleans by the
# health/startup preflight so a missing key is caught at deploy — not at the
# first client upload (the production incident this preflight exists to prevent).
_LLM_KEY_ENV_VARS = (
    "GROQ_API_KEY",
    "CEREBRAS_API_KEY",
    "HF_TOKEN",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
)


def llm_key_presence() -> dict[str, bool]:
    """Which LLM API keys are set in the environment — booleans only.

    NEVER returns key values, lengths, or prefixes: a boolean is all a preflight
    needs and anything more leaks secret material into logs/health payloads. A
    key present but empty/whitespace counts as absent (the same check the
    backends themselves apply).
    """
    return {
        name: bool(os.environ.get(name, "").strip()) for name in _LLM_KEY_ENV_VARS
    }


# Cloud model per provider for judgment-heavy structured extraction. Claude
# (Fable) stays the preferred choice when an Anthropic key is present; OpenAI's
# gpt-4o is the fallback when only OPENAI_API_KEY is configured — the codebase's
# actual production state. gpt-4o (not the navigator's cheap gpt-4o-mini
# fallback) because questionnaire extraction is hallucination-sensitive and
# warrants the stronger model.
_EXTRACTION_MODELS = {
    "ollama": os.environ.get("OLLAMA_EXTRACT_MODEL", "llama3.2:3b"),
    # Groq's catalog is entirely open-weight. gpt-oss-120b (Apache 2.0) is the
    # default because it is both cheaper and stronger on schema-constrained
    # extraction than the llama-3.3-70b it replaces; override per-deployment
    # with GROQ_MODEL after benchmarking against a real client document.
    "groq": os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b"),
    "huggingface": os.environ.get("HF_MODEL", "Qwen/Qwen3-32B"),
    "cerebras": os.environ.get("CEREBRAS_MODEL", "llama-3.3-70b"),
    "openai": "gpt-4o",
    "claude": "claude-fable-5",
}

# Open-weight-only mode. When on, the proprietary steps (OpenAI, Anthropic) are
# dropped from every fallback chain even if their keys happen to be present —
# so a stray key in the environment cannot silently route confidential client
# questionnaires to a proprietary vendor. Defaults ON: for market research the
# constraint is contractual (client NDAs), not a preference, and a default that
# has to be remembered is a default that gets forgotten.
def open_weights_only() -> bool:
    """True when only open-weight backends may serve LLM tasks."""
    return os.environ.get("LLM_OPEN_WEIGHTS_ONLY", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )

# Ollama gets a short per-attempt timeout inside the fallback chain: a local
# model that hangs must not stall the whole request when a cloud backend could
# answer in seconds. A connection-refused (Ollama not running — the production
# state on Render) fails instantly regardless and falls straight through.
_OLLAMA_CHAIN_TIMEOUT = 30


def _extraction_chain() -> list[dict[str, str]]:
    """Ordered backend fallback for judgment-heavy extraction: local first.

    The chain is **Ollama → Groq → OpenAI → Claude**: try the free/private local
    model first, fall through to Groq's free cloud tier, then paid OpenAI, then
    Claude — each step included only if it's actually usable (a cloud step needs
    its key; Ollama is always attempted since it may be reachable locally and
    fails fast when it isn't). `run_task` walks this list, escalating on any
    BackendUnavailable/ModelOutputError and only raising once every candidate has
    failed. Resolved per call so tests and runtime see the current environment.
    """
    keys = llm_key_presence()
    chain: list[dict[str, str]] = [
        {"backend": "ollama", "model": _EXTRACTION_MODELS["ollama"]}
    ]
    if keys.get("GROQ_API_KEY"):
        chain.append({"backend": "groq", "model": _EXTRACTION_MODELS["groq"]})
    if keys.get("CEREBRAS_API_KEY"):
        chain.append({"backend": "cerebras", "model": _EXTRACTION_MODELS["cerebras"]})
    if keys.get("HF_TOKEN"):
        chain.append(
            {"backend": "huggingface", "model": _EXTRACTION_MODELS["huggingface"]}
        )
    # Proprietary tail — skipped entirely in open-weights-only mode (the default).
    if not open_weights_only():
        if keys.get("OPENAI_API_KEY"):
            chain.append({"backend": "openai", "model": _EXTRACTION_MODELS["openai"]})
        if keys.get("ANTHROPIC_API_KEY"):
            chain.append({"backend": "claude", "model": _EXTRACTION_MODELS["claude"]})
    return chain


def run_task(
    task: str,
    prompt: str,
    schema: dict,
    routes: dict[str, dict[str, str]] | None = None,
    timeout: int = 90,
) -> AIResponse:
    """Execute an AI task through its configured route."""
    route = (routes or DEFAULT_ROUTES).get(task)
    if route is None:
        raise KeyError(f"no route configured for task: {task}")
    # "auto" defers to the Ollama → Groq → OpenAI → Claude fallback chain,
    # trying each configured backend until one succeeds.
    if route["backend"] == "auto":
        return _run_chain(_extraction_chain(), prompt, schema, timeout)
    return _run_one(route["backend"], route["model"], prompt, schema, timeout)


def _run_one(
    backend: str, model: str, prompt: str, schema: dict, timeout: int
) -> AIResponse:
    """Dispatch one concrete backend and wrap the result."""
    if backend == "ollama":
        content = _ollama_generate(model, prompt, schema, timeout)
    elif backend == "groq":
        content = _groq_generate(model, prompt, schema, timeout)
    elif backend == "cerebras":
        content = _cerebras_generate(model, prompt, schema, timeout)
    elif backend == "huggingface":
        content = _huggingface_generate(model, prompt, schema, timeout)
    elif backend == "openai":
        content = _openai_generate(model, prompt, schema, timeout)
    elif backend == "claude":
        content = _claude_generate(model, prompt, schema, timeout)
    else:
        raise ValueError(f"unknown backend: {backend}")
    return AIResponse(content=content, backend=backend, model=model)


def _run_chain(
    chain: list[dict[str, str]], prompt: str, schema: dict, timeout: int
) -> AIResponse:
    """Walk a fallback chain, escalating on failure, raising only if all fail.

    A backend that fails to serve (BackendUnavailable) or returns unusable output
    (ModelOutputError) hands off to the next candidate. If the whole chain fails,
    the LAST error is raised — but its `config_error` flag is set only when EVERY
    step was a config fault (all keys missing), so the extractor's flag taxonomy
    still distinguishes "nothing is configured" (actionable) from "a configured
    backend was down" (transient).
    """
    if not chain:
        raise BackendUnavailable(
            "no extraction backend available: start Ollama or set GROQ_API_KEY / "
            "OPENAI_API_KEY / ANTHROPIC_API_KEY",
            config_error=True,
        )
    last_exc: Exception | None = None
    all_config_errors = True
    for candidate in chain:
        # Cap Ollama's attempt so a hung local model can't stall the chain.
        attempt_timeout = (
            min(timeout, _OLLAMA_CHAIN_TIMEOUT)
            if candidate["backend"] == "ollama"
            else timeout
        )
        try:
            return _run_one(
                candidate["backend"], candidate["model"], prompt, schema, attempt_timeout
            )
        except (BackendUnavailable, ModelOutputError) as exc:
            last_exc = exc
            if not getattr(exc, "config_error", False):
                all_config_errors = False
    assert last_exc is not None
    if isinstance(last_exc, BackendUnavailable) and not all_config_errors:
        # At least one backend was reachable but failed transiently — surface as
        # a transient (retryable) fault, not an actionable config error.
        raise BackendUnavailable(str(last_exc), config_error=False)
    raise last_exc

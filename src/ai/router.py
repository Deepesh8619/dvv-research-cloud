"""Model router — every engine calls AI through here, never a backend directly.

Backends: Ollama (local, private) and open-weight models on Groq, Cerebras and
Hugging Face. DVV uses open-source models only — no proprietary vendor is
reachable from this module. Routes are config, not code: each task name maps to
a backend and model, with confidence-gated escalation handled by callers.

Local models run in plain JSON mode with the schema described in the prompt —
full grammar-constrained decoding proved pathologically slow on low-RAM
machines. Outputs are validated in code instead; invalid output raises
ModelOutputError so callers can escalate or degrade.
"""

from __future__ import annotations

import json
import logging
import os
import re
import ssl
import time
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

# Output budget per call. Providers bill (and rate-limit on) prompt + schema +
# max_tokens, so an over-generous reservation is charged on EVERY request even
# though it is never used.
#
# Measured across the 74 questions of a real client questionnaire: median
# output 157 tokens, 95th percentile 287, largest 493. A 4096 reservation was
# 8.3x the worst real case and roughly TWO THIRDS of every bill — the single
# largest line item, larger than the prompt and schema combined.
#
# 1536 keeps ~3x headroom over the largest question actually observed. Raise it
# with LLM_MAX_OUTPUT_TOKENS if a genuinely huge grid ever truncates; a
# truncated response fails loudly as a JSON parse error rather than silently.
MAX_OUTPUT_TOKENS = int(os.environ.get("LLM_MAX_OUTPUT_TOKENS", "1536"))

logger = logging.getLogger(__name__)


def _ssl_context() -> ssl.SSLContext | None:
    """SSL context with certifi's CA bundle when available.

    macOS system Python ships without CA certificates wired up, so plain
    urlopen to any https API fails with CERTIFICATE_VERIFY_FAILED.
    certifi is installed as a transitive dep almost everywhere; fall back to
    default verification if it isn't.
    """
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return None


# A 429 is not "this backend is broken", it is "wait a moment". Providers say
# how long, either in Retry-After or inside the message ("try again in 14.69s").
_RETRY_AFTER_RE = re.compile(r"try again in ([\d.]+)\s*s", re.IGNORECASE)

# Bounded so a genuinely exhausted quota still fails instead of hanging forever.
_RATE_LIMIT_RETRIES = 3
_RATE_LIMIT_MAX_WAIT = 30.0


# A provider that says "try again in 14m10s" has exhausted a DAILY budget, not
# a per-minute one. Without this, every subsequent call re-tries it, burns the
# bounded in-call retries (~90s of sleeping), and only then falls through — so a
# 75-question document spends over an hour asleep on a backend that already
# told us to come back tomorrow.
#
# Anything longer than the in-call retry ceiling is treated as "this backend is
# done for now": it is skipped entirely until the time it named. Process-local
# and deliberately not persisted — a restart re-probing once is cheap, and a
# stale cooldown surviving a deploy is worse than one wasted call.
_backend_cooldown: dict[str, float] = {}


def backend_available_in(backend: str, model: str = "") -> float:
    """Seconds until this backend+model is worth trying again. 0 means now.

    Keyed on the MODEL too: providers meter per model, so one model running
    out says nothing about another's budget.
    """
    return max(0.0, _backend_cooldown.get(f"{backend}:{model}", 0.0) - time.time())


def note_backend_exhausted(backend: str, seconds: float, model: str = "") -> None:
    """Skip this backend+model until its quota window is expected to have reset."""
    _backend_cooldown[f"{backend}:{model}"] = time.time() + seconds
    logger.warning(
        f"{backend} [{model}] quota exhausted — skipping for {seconds / 60:.0f} min"
    )


def friendly_backend_error(message: str) -> str:
    """Turn a chain failure into something a researcher can act on.

    The raw summary is written for whoever is debugging the deployment:
    "ollama unreachable at http://localhost:11434: [Errno 111] Connection
    refused | groq: out of quota for another 20 min". A research lead reading
    that on an upload screen learns nothing they can use — Ollama is not
    something they have, and "quota" is not obviously "come back later".
    """
    low = (message or "").lower()
    if "out of quota" in low or "rate limit" in low or "tokens per day" in low:
        minutes = re.search(r"another (\d+) min", low)
        when = f" Try again in about {minutes.group(1)} minutes." if minutes else ""
        return (
            "The AI service has used up its allowance for now, so the document "
            f"could not be read.{when} Nothing was lost — upload it again once "
            "the allowance resets."
        )
    if "not set" in low and "key" in low:
        return (
            "No AI service is configured on the server, so documents cannot be "
            "read. This needs an administrator to set an API key."
        )
    return (
        "The AI service could not be reached, so the document could not be "
        "read. Nothing was lost — try uploading it again."
    )


def _retry_after_seconds(exc: urllib.error.HTTPError, detail: str) -> float | None:
    """How long the provider asked us to wait, if it said."""
    header = exc.headers.get("Retry-After") if exc.headers else None
    if header:
        try:
            return float(header)
        except ValueError:
            pass
    match = _RETRY_AFTER_RE.search(detail or "")
    return float(match.group(1)) if match else None


# A provider rejecting the model's own malformed tool call (Groq: "Failed to
# call a function ... failed_generation"; OpenAI-compatible: "tool_use_failed").
_MALFORMED_ANSWER = re.compile(r"failed to call a function|failed_generation|tool_use_failed", re.I)


def http_json(request: urllib.request.Request, timeout: int) -> dict:
    """urlopen with the certifi SSL context, returning parsed JSON.

    HTTP error responses re-raise as URLError with the API's own error
    message — "credit balance is too low" is actionable, "HTTP 400" is not.

    A 429 is retried in place rather than raised. Rate limits are TRANSIENT and
    self-describing: Groq's free tier caps tokens-per-minute, and one
    ~6k-token extraction prompt can exhaust an 8k/min budget, so a 75-question
    document rate-limits constantly. Treating that as "backend unavailable"
    burned the whole fallback chain and landed on a WEAKER model (or a dead
    one) for work the good model would happily have done a few seconds later.
    We honour the provider's own wait hint, bounded, then give up honestly.
    """
    attempt = 0
    malformed_retried = False
    while True:
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
            if exc.code == 429:
                hinted = _retry_after_seconds(exc, detail)
                # A wait longer than the in-call ceiling is a daily/large quota
                # window. Do not sleep through it — surface it so the caller can
                # put this backend on cooldown and move on.
                if hinted is not None and hinted > _RATE_LIMIT_MAX_WAIT:
                    raise QuotaExhausted(
                        f"HTTP 429: {detail or exc.reason}", retry_after=hinted
                    ) from exc
            if exc.code == 429 and attempt < _RATE_LIMIT_RETRIES:
                wait = hinted
                # No hint -> exponential backoff from 2s.
                wait = min(wait if wait is not None else 2.0 * (2**attempt),
                           _RATE_LIMIT_MAX_WAIT)
                attempt += 1
                logger.info(
                    f"rate limited, waiting {wait:.1f}s "
                    f"(attempt {attempt}/{_RATE_LIMIT_RETRIES})"
                )
                time.sleep(wait)
                continue
            if exc.code == 400 and _MALFORMED_ANSWER.search(detail):
                # The model answered, but its structured answer did not fit the
                # schema ("Failed to call a function"). That is not an outage:
                # it is random, and a second try usually fits. Filed as
                # "unreachable" it told an analyst no AI existed while it was
                # answering every few seconds.
                if not malformed_retried:
                    malformed_retried = True
                    continue
                raise ModelOutputError(f"the model's answer did not fit: {detail}") from exc
            raise urllib.error.URLError(
                f"HTTP {exc.code}: {detail or exc.reason}"
            ) from exc


DEFAULT_ROUTES: dict[str, dict[str, str]] = {
    # Data-file classification on upload. Was hardcoded to Ollama, which does
    # not exist on Render, so this task could never succeed in production.
    # Damage was limited by luck: classify_file short-circuits
    # .sav/.parquet/.xlsx/.xls and wide .csv on extension alone, which covers
    # most real uploads. Only AMBIGUOUS files (narrow CSVs, .txt) reach the
    # model, and those came back "unknown" at 0.0 confidence — silently,
    # because classify_file degrades rather than raising.
    "intake.classify": {"backend": "auto", "model": ""},
    # Verbatim coding rides the same open-model chain (Ollama → Groq → Cerebras) as
    # questionnaire extraction: the previous hardcoded backends meant coding
    # silently failed anywhere the one named backend was missing (Render has
    # no Ollama — production never coded a verbatim).
    "coding.apply": {"backend": "auto", "model": ""},
    "coding.codeframe": {"backend": "auto", "model": ""},
    # Two-pass questionnaire extraction (src/engines/questionnaire/extractor.py).
    # Judgment-heavy, hallucination-sensitive structured extraction. Unlike the
    # sibling tasks above these are NOT hardcoded to a provider: the sentinel
    # backend "auto" makes run_task walk the fallback chain
    # (_extraction_chain()) — the configured cloud backends, or Ollama alone on
    # an offline install — trying each until one succeeds.
    "questionnaire.extract": {"backend": "auto", "model": ""},
    "questionnaire.verify": {"backend": "auto", "model": ""},
    # Whole-document segmentation reconciliation (safety net over the regex
    # segmenter, src/engines/questionnaire/reconciler.py). Same "auto" chain as
    # extract/verify.
    "questionnaire.reconcile_segmentation": {"backend": "auto", "model": ""},
    # One reading of the study's instructions before any question is read
    # (src/engines/questionnaire/study_understanding.py).
    "questionnaire.understand": {"backend": "auto", "model": ""},
    # One question revised from an analyst's plain-language instruction
    # (src/engines/questionnaire/question_agent.py).
    "questionnaire.revise": {"backend": "auto", "model": ""},
    # A flagged question fixed after the whole document is read, with the
    # questions it names and its neighbours in view (second_look.py).
    "questionnaire.resolve": {"backend": "auto", "model": ""},
    # One read of the whole survey for problems between questions
    # (src/engines/questionnaire/logic_review.py).
    "questionnaire.logic_review": {"backend": "auto", "model": ""},
    "report.narrative": {"backend": "auto", "model": ""},
    "fielding.probe": {"backend": "auto", "model": ""},
    # study.chat routes through src/ai/navigator.py (multi-tool agent loop on
    # Groq, then Cerebras) rather than run_task — kept here so the task
    # inventory in one place stays truthful. study.chat.basic is its local
    # fallback: single shot, one tool max, free.
    "study.chat": {"backend": "groq", "model": "qwen/qwen3.8-27b"},
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


class QuotaExhausted(urllib.error.URLError):
    """A 429 whose wait hint is too long to sleep through (a daily budget).

    Subclasses URLError so every existing `except urllib.error.URLError` path
    in the per-backend generators keeps working unchanged; the extra
    `retry_after` is what lets the chain put the backend on cooldown instead of
    re-discovering the exhaustion on every subsequent call.
    """

    def __init__(self, msg: str, retry_after: float) -> None:
        super().__init__(msg)
        self.retry_after = retry_after


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
            "max_tokens": MAX_OUTPUT_TOKENS,
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
            # "required", not the named {"function": {"name": "emit"}} form: with
            # one tool they mean the same, but most open models on Bedrock
            # (Qwen3-235B, DeepSeek V3.2, GLM, Gemma) ignore the named form and
            # answer in plain text — verified live. "required" works everywhere.
            "tool_choice": "required",
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


def bedrock_chat_url() -> str:
    """Bedrock's chat-completions URL for the configured region.

    Defaults to the bedrock-runtime endpoint AWS recommends; BEDROCK_BASE_URL
    overrides it (e.g. the bedrock-mantle endpoint) without a code change.
    """
    region = os.environ.get("BEDROCK_REGION", "ap-south-1")
    base = os.environ.get(
        "BEDROCK_BASE_URL", f"https://bedrock-runtime.{region}.amazonaws.com/openai/v1"
    )
    return base.rstrip("/") + "/chat/completions"


def bedrock_model() -> str:
    """The Bedrock model ID for the configured endpoint (BEDROCK_MODEL overrides).

    The same model has a different ID on each endpoint — verified live: the
    bedrock-runtime endpoint wants the versioned ID (`...-v1:0`) and rejects the
    short form as "an invalid model identifier". If BEDROCK_BASE_URL points at
    another endpoint, set BEDROCK_MODEL to that endpoint's ID as well.
    """
    return os.environ.get("BEDROCK_MODEL") or BEDROCK_DEFAULT_MODEL


def _bedrock_generate(model: str, prompt: str, schema: dict, timeout: int) -> dict:
    """Structured extraction via Amazon Bedrock, in the client's own AWS region.

    Authenticated with a Bedrock API key belonging to that client's app
    identity, which may invoke this one model and nothing else.
    """
    api_key = os.environ.get("BEDROCK_API_KEY")
    if not api_key:
        raise BackendUnavailable("BEDROCK_API_KEY not set", config_error=True)
    return _openai_compatible_generate(
        bedrock_chat_url(), api_key, model, prompt, schema, timeout, "bedrock"
    )


def _cerebras_generate(model: str, prompt: str, schema: dict, timeout: int) -> dict:
    """Structured extraction via Cerebras' OpenAI-compatible API.

    Its own backend rather than reaching Cerebras through the HF router, since
    HF bills its own (very small) credits either way.

    Do NOT assume a free tier: verified 2026-08-29 that a fresh account returns
    HTTP 402 `payment_required` (param: quota) for every served model, contrary
    to third-party "free LLM API" roundups. Groq is the backend with a free tier
    that actually works today.

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




# Env vars that gate the cloud AI backends. Reported as presence booleans by the
# health/startup preflight so a missing key is caught at deploy — not at the
# first client upload (the production incident this preflight exists to prevent).
_LLM_KEY_ENV_VARS = (
    "BEDROCK_API_KEY",
    "GROQ_API_KEY",
    "CEREBRAS_API_KEY",
    "HF_TOKEN",
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


# Default models: open-weight, and none made by OpenAI (DVV policy). Chosen on
# evidence, 2026-09-16: the same copilot conversation run on five open models on
# Bedrock (Mumbai) — only Qwen3-32B and DeepSeek V3.2 looked every fact up and
# used the real link ID; Qwen3-32B was also the fastest and the cheapest
# ($0.18 / $0.71 per 1M tokens). Qwen3-235B and GLM-4.7 answered a question
# about the questionnaire without looking it up. Groq/Cerebras serve one Qwen
# each. Every value can be overridden per server (BEDROCK_MODEL etc.).
BEDROCK_DEFAULT_MODEL = "qwen.qwen3-32b-v1:0"
GROQ_DEFAULT_MODEL = "qwen/qwen3.8-27b"
CEREBRAS_DEFAULT_MODEL = "qwen-3.8-27b"
HF_DEFAULT_MODEL = "Qwen/Qwen3-235B-A22B-Instruct-2507"

# Model per provider for judgment-heavy structured extraction. DVV runs
# open-source models only; there is no proprietary vendor in this module.
_EXTRACTION_MODELS = {
    "ollama": os.environ.get("OLLAMA_EXTRACT_MODEL", "llama3.2:3b"),
    # Qwen (Alibaba; Apache-2.0; published on Hugging Face) on every provider —
    # no OpenAI-made model, by DVV policy. One family everywhere, so a fallback
    # changes WHO answers, not how the model behaves. These are the only
    # non-OpenAI chat models Groq and Cerebras serve (checked 2026-09-16).
    "groq": os.environ.get("GROQ_MODEL", GROQ_DEFAULT_MODEL),
    "huggingface": os.environ.get("HF_MODEL", HF_DEFAULT_MODEL),
    "cerebras": os.environ.get("CEREBRAS_MODEL", CEREBRAS_DEFAULT_MODEL),
}

# Open-weight-only mode. When on, the proprietary steps (OpenAI, Anthropic) are
# dropped from every fallback chain even if their keys happen to be present —
# so a stray key in the environment cannot silently route confidential client
# questionnaires to a proprietary vendor. Defaults ON: for market research the
# constraint is contractual (client NDAs), not a preference, and a default that
# has to be remembered is a default that gets forgotten.

# Ollama gets a short per-attempt timeout inside the fallback chain: a local
# model that hangs must not stall the whole request when a cloud backend could
# answer in seconds. A connection-refused (Ollama not running — the production
# state on Render) fails instantly regardless and falls straight through.
_OLLAMA_CHAIN_TIMEOUT = 30
# How long to stop trying a local model that just failed to serve. Long enough
# to carry a whole document without re-paying the timeout on every chunk, short
# enough that restarting Ollama is picked up within a few minutes.
_OLLAMA_COOLDOWN = float(os.getenv("OLLAMA_COOLDOWN_SECONDS", "300"))


# Additional Groq models to fall back to when the primary model's DAILY quota
# is spent. Quotas are metered PER MODEL, so each of these carries its own
# allowance — three models is three times the free daily capacity.
#
# This is a deliberate, NARROW exception to the same-model-everywhere rule that
# keeps output uniform across providers. The trade is explicit: a different
# model may extract slightly differently, but a degraded extraction a reviewer
# can see beats no extraction at all. Which model produced each result is
# recorded on the ExtractionResult, so the difference is never silent.
#
# Empty string disables the behaviour entirely.
def _fallback_models_from_env() -> list[str]:
    raw = os.environ.get("GROQ_FALLBACK_MODELS", "")
    return [m.strip() for m in raw.split(",") if m.strip()]


GROQ_FALLBACK_MODELS = _fallback_models_from_env()


def _local_extraction_opted_in() -> bool:
    return os.environ.get("DVV_EXTRACT_LOCAL", "").strip().lower() in {"1", "true", "yes"}


def _extraction_chain() -> list[dict[str, str]]:
    """Ordered backend fallback for judgment-heavy extraction: one brain per environment.

    The cloud chain is **Bedrock → Groq → Cerebras → Hugging Face**, open-weight
    models only, each included when its key is set. Ollama is used only when no
    cloud key is set (an offline install — it is then the whole chain), or when
    `DVV_EXTRACT_LOCAL=1` puts it first deliberately.

    It used to lead every chain. That made the brain environment-dependent: a
    laptop with Ollama running had `llama3.2:3b` deciding question types and
    routing, while the server, with no Ollama, had a 32B cloud model decide — so
    the same document produced different instruments in the two places, and a
    local failure's five-minute cooldown could switch brains MID-document. Every
    local trial was measuring the small model, not the parser. A 3B model is
    not a fallback for this work; it is a different, weaker answer.

    Resolved per call so tests and runtime see the current environment.
    """
    keys = llm_key_presence()
    cloud: list[dict[str, str]] = []
    # Bedrock first among the clouds: in-region (the NDA story), paid, no free-tier
    # rate limits. Groq and Cerebras remain as fallbacks when their keys are set.
    if keys.get("BEDROCK_API_KEY"):
        cloud.append({"backend": "bedrock", "model": bedrock_model()})
    if keys.get("GROQ_API_KEY"):
        primary = _EXTRACTION_MODELS["groq"]
        cloud.append({"backend": "groq", "model": primary})
        # Same provider, different model = a separate daily allowance.
        for model in GROQ_FALLBACK_MODELS:
            if model != primary:
                cloud.append({"backend": "groq", "model": model})
    if keys.get("CEREBRAS_API_KEY"):
        cloud.append({"backend": "cerebras", "model": _EXTRACTION_MODELS["cerebras"]})
    if keys.get("HF_TOKEN"):
        cloud.append({"backend": "huggingface", "model": _EXTRACTION_MODELS["huggingface"]})
    local = {"backend": "ollama", "model": _EXTRACTION_MODELS["ollama"]}
    if not cloud:
        return [local]
    if _local_extraction_opted_in():
        return [local, *cloud]
    return cloud


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
    # "auto" defers to _extraction_chain(), trying each backend until one succeeds.
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
    elif backend == "bedrock":
        content = _bedrock_generate(model, prompt, schema, timeout)
    elif backend == "huggingface":
        content = _huggingface_generate(model, prompt, schema, timeout)
    else:
        raise ValueError(f"unknown backend: {backend}")
    return AIResponse(content=content, backend=backend, model=model)


def run_private(prompt: str, schema: dict, timeout: int = 120) -> AIResponse:
    """For a client's own documents: Bedrock (in region, paid, not trained on), else a
    local model when no cloud key is set. Never a free tier — Groq and Hugging Face
    are skipped even when their keys are present."""
    keys = llm_key_presence()
    if keys.get("BEDROCK_API_KEY"):
        chain = [{"backend": "bedrock", "model": bedrock_model()}]
    elif not any(keys.values()):
        chain = [{"backend": "ollama", "model": _EXTRACTION_MODELS["ollama"]}]
    else:
        raise BackendUnavailable(
            "No private model is set up: client text goes only to Bedrock or a local model.",
            config_error=True)
    return _run_chain(chain, prompt, schema, timeout)


def _run_chain(
    chain: list[dict[str, str]], prompt: str, schema: dict, timeout: int
) -> AIResponse:
    """Walk a fallback chain, escalating on failure, raising only if all fail.

    A backend that fails to serve (BackendUnavailable) or returns unusable output
    (ModelOutputError) hands off to the next candidate. If the whole chain fails,
    EVERY step's failure is reported, labelled by backend.

    Reporting only the LAST error actively misleads. With a groq -> cerebras
    chain, a Groq failure followed by an unfunded Cerebras key surfaced as
    "Cerebras: Payment Required" while the real cause sat one line above it.
    That sent me down the wrong path twice; the first time this change was made
    it immediately disproved my own guess about the cause.

    `config_error` is still set only when EVERY step was a config fault, so the
    extractor's flag taxonomy keeps distinguishing "nothing is configured"
    (actionable) from "a configured backend was down" (transient).
    """
    if not chain:
        raise BackendUnavailable(
            "no AI backend available: run the local-ai profile (Ollama) or set "
            "BEDROCK_API_KEY / GROQ_API_KEY / CEREBRAS_API_KEY",
            config_error=True,
        )
    last_exc: Exception | None = None
    all_config_errors = True
    failures: list[str] = []
    for candidate in chain:
        backend = candidate["backend"]
        # Skip a backend that already told us it is out of quota, rather than
        # re-discovering it (and sleeping through the in-call retries) on every
        # single question of a 75-question document.
        cooling = backend_available_in(backend, candidate["model"])
        if cooling > 0:
            failures.append(
                f"{backend} [{candidate['model']}]: out of quota for another "
                f"{cooling / 60:.0f} min"
            )
            all_config_errors = False
            last_exc = BackendUnavailable(failures[-1])
            continue
        # Cap Ollama's attempt so a hung local model can't stall the chain —
        # but only when something comes after it. For a local-only deployment
        # Ollama IS the chain, and a CPU-served answer that takes 40s must be
        # waited for, not cut off with nothing left to fall back to.
        attempt_timeout = (
            min(timeout, _OLLAMA_CHAIN_TIMEOUT)
            if candidate["backend"] == "ollama" and candidate is not chain[-1]
            else timeout
        )
        try:
            return _run_one(
                candidate["backend"], candidate["model"], prompt, schema, attempt_timeout
            )
        except (BackendUnavailable, ModelOutputError) as exc:
            last_exc = exc
            failures.append(f"{backend}: {exc}")
            if not getattr(exc, "config_error", False):
                all_config_errors = False
            if backend == "ollama" and isinstance(exc, BackendUnavailable):
                # A local model that is down or wedged stays that way for the
                # rest of a document. Without this, EVERY chunk rediscovers it
                # and pays the attempt timeout again: ninety chunks at twenty
                # seconds is half an hour of a run spent waiting on a process
                # that answered nothing — which reads as a hang, not as a
                # fallback. Only when something follows it in the chain; for a
                # local-only deployment Ollama is all there is.
                note_backend_exhausted("ollama", _OLLAMA_COOLDOWN, candidate["model"])
            # A quota exhaustion propagates as BackendUnavailable wrapping a
            # QuotaExhausted; record the cooldown so the rest of the document
            # stops paying for it.
            cause = exc.__cause__
            if isinstance(cause, QuotaExhausted):
                note_backend_exhausted(backend, cause.retry_after, candidate['model'])
    assert last_exc is not None
    summary = "all backends failed — " + " | ".join(failures)
    # If any model actually answered (badly), say so: "unreachable" told an
    # analyst no AI existed while one was answering every few seconds.
    if any("did not fit" in f or "ModelOutputError" in f for f in failures) or not isinstance(
        last_exc, BackendUnavailable
    ):
        raise ModelOutputError(summary)
    raise BackendUnavailable(summary, config_error=all_config_errors)

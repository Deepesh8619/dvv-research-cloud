"""Which model is this deployment actually calling?

A provider deprecating a model arrives as an email naming model ids. Answering
"is one of those mine?" meant reading the source of whichever version happens to
be deployed — and both the provider and the model are overridable by environment
variable, so the code alone does not know.
"""

import api.main as main


def test_the_model_ids_are_reported_not_just_the_keys(monkeypatch):
    monkeypatch.setenv("BEDROCK_API_KEY", "k")
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    models = main._configured_models()
    assert models["bedrock"] == "qwen.qwen3-32b-v1:0"
    assert models["groq"].startswith("qwen")
    # A backend with no key is not reported: it would never be called.
    assert "cerebras" not in models


def test_an_override_is_what_gets_reported(monkeypatch):
    """The deployed model is a setting, not a constant — so reporting the
    constant would answer the deprecation question wrongly."""
    monkeypatch.setenv("BEDROCK_API_KEY", "k")
    monkeypatch.setenv("BEDROCK_MODEL", "qwen.qwen3-coder-30b-a3b-v1:0")
    assert main._configured_models()["bedrock"] == "qwen.qwen3-coder-30b-a3b-v1:0"


def test_the_local_and_embedding_models_are_always_reported(monkeypatch):
    """Neither needs a key, so neither shows up in `llm_keys` — and both are
    still models somebody may need to account for."""
    monkeypatch.delenv("BEDROCK_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    monkeypatch.setattr("src.engines.rag.embeddings._ollama", lambda texts, timeout: None)
    models = main._configured_models()
    assert models["local"] == "llama3.2:3b"
    # What answers, not the setting: with no Ollama, the lexical fallback.
    assert models["embeddings"] == "lexical-v1 (no embedding model reachable)"


def test_the_extracting_model_is_reported(monkeypatch):
    """Local and server must name the same brain, or their extractions differ."""
    monkeypatch.delenv("DVV_EXTRACT_LOCAL", raising=False)
    monkeypatch.setenv("BEDROCK_API_KEY", "k")
    assert main._configured_models()["extraction"] == "bedrock/qwen.qwen3-32b-v1:0"


def test_health_reports_the_embedder_that_answers_not_the_setting(monkeypatch):
    """The setting said nomic-embed-text while every search ran on the fallback."""
    from src.engines.rag import embeddings

    monkeypatch.setenv("DVV_EMBED_MODEL", "nomic-embed-text")
    monkeypatch.setattr(embeddings, "_ollama", lambda texts, timeout: None)
    assert embeddings.live_embedder() == "lexical-v1 (no embedding model reachable)"
    monkeypatch.setattr(embeddings, "_ollama", lambda texts, timeout: [[1.0, 0.0]])
    assert embeddings.live_embedder() == "ollama:nomic-embed-text"

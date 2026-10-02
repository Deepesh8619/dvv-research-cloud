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
    models = main._configured_models()
    assert models["local"] == "llama3.2:3b"
    assert models["embeddings"] == "nomic-embed-text"

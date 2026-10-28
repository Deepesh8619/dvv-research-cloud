"""Search quality must not fall. Runs on the lexical fallback (no model in CI),
so the numbers are deterministic; floors are today's measurement, rounded down."""

from pathlib import Path

from src.engines.rag.retrieval_eval import evaluate, load

CASES = Path(__file__).resolve().parents[2] / "evals" / "retrieval" / "cases.json"


def test_search_quality_holds_on_the_production_embedder(monkeypatch):
    # The lexical fallback, whatever this machine runs: that is production
    # today, and it makes the numbers the same everywhere.
    monkeypatch.setattr("src.engines.rag.embeddings._ollama", lambda texts, timeout: None)
    report = evaluate(load(CASES))
    assert report["embedder"] == "lexical-v1"  # CI has no Ollama; production today neither
    best = report["overall"]["hybrid+glossary"]
    assert best["mrr"] >= 0.85 and best["recall@3"] >= 0.9
    assert report["held_out"]["hybrid+glossary"]["mrr"] >= 0.6
    # Each part earns its place: hybrid beats either signal alone, the glossary beats hybrid.
    for mode in ("meaning", "words"):
        assert report["overall"]["hybrid"]["mrr"] >= report["overall"][mode]["mrr"]
    assert report["held_out"]["hybrid+glossary"]["mrr"] >= report["held_out"]["hybrid"]["mrr"]
    assert report["by_type"]["code"]["hybrid+glossary"]["recall@1"] == 1.0


def test_held_out_searches_are_kept_apart():
    cases = load(CASES)
    held = [q for q in cases["queries"] if q.get("held_out")]
    assert len(held) >= 10
    refs = {p["ref"] for p in cases["pieces"]}
    assert all(set(q["expect"]) <= refs for q in cases["queries"])

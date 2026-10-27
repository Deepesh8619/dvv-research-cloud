"""Scoring retrieval: does the right passage come back near the top?

For each labelled search, a hit at k is any expected passage among the first k
results; the reciprocal rank is 1 / the position of the first expected one.
Three rankings are scored on the same embeddings so they can be compared:
meaning only, words only (BM25), and the hybrid that production uses.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.engines.rag.embeddings import LEXICAL, embed, similarity
from src.engines.rag.glossary import expand
from src.engines.rag.hybrid import bm25, rank

#: hybrid+glossary is what production runs (store.search).
MODES = ("meaning", "words", "hybrid", "hybrid+glossary")


def load(path: Path) -> dict:
    return json.loads(Path(path).read_text())


def ranked(mode: str, query: str, qvec, ids, texts, refs, vecs, k: int = 10,
           model: str = LEXICAL) -> list[str]:
    if mode == "meaning":
        scores = {i: similarity(qvec, v) for i, v in zip(ids, vecs, strict=True)}
        return sorted(ids, key=lambda i: -scores[i])[:k]
    if mode == "words":
        scores = dict(zip(ids, bm25(query, texts), strict=True))
        return [i for i in sorted(ids, key=lambda i: -scores[i]) if scores[i] > 0][:k]
    meaning = {i: max(similarity(qvec, v), 0.0) for i, v in zip(ids, vecs, strict=True)}
    expanded = expand(query) if mode == "hybrid+glossary" else query
    return [i for i, _score, _how in rank(expanded, ids, texts, refs, meaning, k,
                                          codes_from=query)]


def evaluate(cases: dict) -> dict:
    """Recall@1, recall@3 and MRR per mode, overall and per query type."""
    pieces, queries = cases["pieces"], cases["queries"]
    ids = [p["ref"] for p in pieces]
    texts = [p["text"] for p in pieces]
    vecs, model = embed(texts, kind="document")
    qvecs, _ = embed([q["q"] for q in queries], kind="query")
    xvecs, _ = embed([expand(q["q"]) for q in queries], kind="query")
    per_query = []
    for q, qvec, xvec in zip(queries, qvecs, xvecs, strict=True):
        row = {"q": q["q"], "type": q["type"], "expect": q["expect"],
               "held_out": bool(q.get("held_out"))}
        for mode in MODES:
            vec = xvec if mode == "hybrid+glossary" else qvec
            got = ranked(mode, q["q"], vec, ids, texts, ids, vecs, model=model)
            first = next((n for n, r in enumerate(got, start=1) if r in q["expect"]), None)
            row[mode] = {"top3": got[:3], "first": first}
        per_query.append(row)

    def score(rows) -> dict:
        out = {}
        for mode in MODES:
            firsts = [r[mode]["first"] for r in rows]
            n = len(firsts) or 1
            out[mode] = {"recall@1": round(sum(1 for f in firsts if f == 1) / n, 3),
                         "recall@3": round(sum(1 for f in firsts if f and f <= 3) / n, 3),
                         "mrr": round(sum(1 / f for f in firsts if f) / n, 3)}
        return out

    types = sorted({r["type"] for r in per_query})
    held = [r for r in per_query if r["held_out"]]
    return {"embedder": model, "queries": len(per_query), "overall": score(per_query),
            "held_out": score(held) if held else None,
            "by_type": {t: score([r for r in per_query if r["type"] == t]) for t in types},
            "per_query": per_query}

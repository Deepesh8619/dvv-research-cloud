"""Hybrid retrieval: meaning, words and codes, fused into one ranking.

Vectors find "how much would you pay" for "price sensitivity". They are poor at
the exact things researchers type — "S3", "Q12a", "Nescafé" — which an
embedding blurs into its neighbours, and worse still when the embedder is the
lexical fallback. Three rankings are therefore made and fused:

  meaning  the vector store's similarity
  words    BM25 over the same pieces (term frequency, rarity, length-normalised)
  code     a question code typed in the query, matched to the piece's ref exactly

Reciprocal rank fusion combines them without having to calibrate one score
against another: each ranking contributes 1 / (k + rank). A code match is put
first outright — someone who typed "S3" means S3.

Pure functions; the caller (store.search) supplies the rows.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence

RRF_K = 60
K1, B = 1.5, 0.75

_TOKEN = re.compile(r"[0-9a-zà-ÿ]+(?:_[0-9a-zà-ÿ]+)*", re.IGNORECASE)
_STOP = frozenset(
    "a an and are as at be by did do does for from had has have how i in is it its of on "
    "or that the their them they this to was were what when where which who why will with "
    "you your about any question questions ask asks asked".split())


def tokens(text: str) -> list[str]:
    return [t for t in (m.group(0).lower() for m in _TOKEN.finditer(text or ""))
            if t not in _STOP]


def bm25(query: str, docs: Sequence[str]) -> list[float]:
    """BM25 score of every document for the query (0 when no term matches)."""
    terms = set(tokens(query))
    if not terms or not docs:
        return [0.0] * len(docs)
    bags = [Counter(tokens(d)) for d in docs]
    lengths = [sum(b.values()) for b in bags]
    avg = (sum(lengths) / len(lengths)) or 1.0
    n = len(docs)
    df = {t: sum(1 for b in bags if t in b) for t in terms}
    scores = []
    for bag, length in zip(bags, lengths, strict=True):
        s = 0.0
        for t in terms:
            f = bag.get(t, 0)
            if f:
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                s += idf * f * (K1 + 1) / (f + K1 * (1 - B + B * length / avg))
        scores.append(s)
    return scores


def codes_in(query: str, refs: Sequence[str]) -> set[str]:
    """Refs (question codes) the query names exactly: "S3" -> {"S3"}, case-insensitive.
    A verbatim's ref is "<qid>:<session>"; its question code is what counts."""
    typed = {t.lower() for t in _TOKEN.findall(query or "")}
    return {r for r in refs if r.split(":")[0].lower() in typed}


def fuse(ids: Sequence[str], meaning: dict[str, float], words: dict[str, float],
         code: set[str], k: int) -> list[tuple[str, float, list[str]]]:
    """(id, fused score, what matched) for the top k ids.

    `meaning` and `words` are raw scores per id; only positive ones rank.
    """
    fused: dict[str, float] = {}
    how: dict[str, list[str]] = {i: [] for i in ids}
    for label, scores in (("meaning", meaning), ("words", words)):
        ranked = sorted((i for i in ids if scores.get(i, 0) > 0), key=lambda i: -scores[i])
        for rank, i in enumerate(ranked, start=1):
            fused[i] = fused.get(i, 0.0) + 1.0 / (RRF_K + rank)
            how[i].append(label)
    for i in code:
        fused[i] = fused.get(i, 0.0) + 1.0  # above anything fusion alone can reach
        how[i].insert(0, "code")
    order = sorted(fused, key=lambda i: -fused[i])[:k]
    return [(i, round(fused[i], 4), how[i]) for i in order]


def rank(query: str, ids: Sequence[str], texts: Sequence[str], refs: Sequence[str],
         meaning: dict[str, float], k: int,
         codes_from: str | None = None) -> list[tuple[str, float, list[str]]]:
    """The whole hybrid ranking, given each piece's meaning score: the one
    function store.search and the retrieval eval both call. `codes_from` is
    the query as typed, when `query` has been expanded.

    Tried and dropped: a fourth "spelling" signal (the lexical vector beside a
    real embedder). It caught misspellings but outvoted synonyms — with
    nomic-embed-text, MRR fell 0.90 -> 0.76 overall and 0.79 -> 0.65 held-out.
    """
    words = dict(zip(ids, bm25(query, list(texts)), strict=True))
    named = codes_in(codes_from if codes_from is not None else query, list(refs))
    code = {i for i, ref in zip(ids, refs, strict=True) if ref in named}
    return fuse(ids, meaning, words, code, k)


def _demo() -> None:
    docs = {"S3": "S3. Which brands of coffee have you heard of? Answers: Alpha; Beta",
            "Q5": "Q5. How much would you be willing to pay for a jar of instant coffee?",
            "Q7": "Q7. How satisfied are you with the taste of Nescafé?"}
    ids = list(docs)
    words = dict(zip(ids, bm25("nescafé taste", list(docs.values())), strict=True))
    assert max(words, key=words.get) == "Q7" and words["Q5"] == 0
    # Meaning prefers Q5 for "price"; words prefer nothing; the code wins outright.
    meaning = {"Q5": 0.8, "Q7": 0.3, "S3": 0.1}
    fused = fuse(ids, meaning, words, codes_in("what does s3 ask", ids), 3)
    assert fused[0][0] == "S3" and fused[0][2][0] == "code"
    assert codes_in("compare q12a and Q5", ["Q12a", "Q5:sess_1", "Q50"]) == {"Q12a", "Q5:sess_1"}
    both = fuse(ids, {"Q7": 0.5, "Q5": 0.6}, words, set(), 2)
    # Agreement between meaning and words beats one strong vote.
    assert both[0][0] == "Q7" and both[0][2] == ["meaning", "words"]


if __name__ == "__main__":
    _demo()
    print("ok")

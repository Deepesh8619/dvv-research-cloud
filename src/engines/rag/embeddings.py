"""Turning a project's text into vectors, on this machine only.

Search by substring answers "which question contains the word price". A client
asks "which question covers price sensitivity", and the questionnaire says
"how much would you be willing to pay" — no word in common, and the substring
search returns nothing. Vectors are what close that gap.

Two ways to make them, in order:

* **A local open-weight embedding model** through Ollama (`nomic-embed-text` by
  default, Apache-2.0). Local because the text being embedded includes client
  questionnaires and respondent verbatims: free hosted tiers may train on what
  they are sent, so this content never leaves the server.
* **A lexical vector** computed here, with no model at all, when Ollama is not
  running — which is the production state today. It is an honest fallback, not
  a silent downgrade: it hashes words and character triples, so it tolerates
  spelling and word-order differences but knows NO synonyms. Every stored
  vector carries the name of what produced it, so the two are never compared
  with each other and the caller can say which one answered.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
import urllib.error
import urllib.request

#: What a vector was made by. Stored beside it; vectors from different makers
#: are never compared, and a change here re-indexes rather than mixing spaces.
LEXICAL = "lexical-v1"
LEXICAL_DIM = 256

_TOKEN = re.compile(r"[a-z0-9]+")
# Ollama is usually absent (production runs without it). One refused connection
# is instant, but a hung host is not — so a failure stops us trying for a while.
_COOLDOWN_SECONDS = float(os.getenv("DVV_EMBED_COOLDOWN_SECONDS", "300"))
_cooldown_until = 0.0


def embed(
    texts: list[str], kind: str = "document", timeout: int = 60
) -> tuple[list[list[float]], str]:
    """(one unit vector per text, the name of what made them).

    `kind` is "document" for something being indexed and "query" for something
    being looked up. It is not decoration: nomic-embed-text is TRAINED with a
    task prefix, and without it "net promoter score" ranked an age question
    above "how likely are you to recommend" — measured, not assumed.
    """
    if not texts:
        return [], LEXICAL
    vectors = _ollama([_prefixed(t, kind) for t in texts], timeout)
    if vectors is not None:
        return vectors, _model_name()
    return [lexical_vector(t) for t in texts], LEXICAL


def _model_name() -> str:
    return "ollama:" + os.getenv("DVV_EMBED_MODEL", "nomic-embed-text")


#: nomic's own task prefixes. Other models ignore text they were not trained
#: on, so an unknown model simply gets the text unchanged.
_PREFIXES = {"document": "search_document: ", "query": "search_query: "}


def _prefixed(text: str, kind: str) -> str:
    if "nomic" not in os.getenv("DVV_EMBED_MODEL", "nomic-embed-text"):
        return text
    return _PREFIXES.get(kind, "") + text


def _ollama(texts: list[str], timeout: int) -> list[list[float]] | None:
    """Vectors from the local model, or None if it is not there to answer."""
    global _cooldown_until
    if time.monotonic() < _cooldown_until:
        return None
    host = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    payload = json.dumps(
        {"model": os.getenv("DVV_EMBED_MODEL", "nomic-embed-text"), "input": texts}
    ).encode()
    request = urllib.request.Request(
        f"{host}/api/embed", data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            body = json.loads(response.read().decode())
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        _cooldown_until = time.monotonic() + _COOLDOWN_SECONDS
        return None
    vectors = body.get("embeddings")
    if not isinstance(vectors, list) or len(vectors) != len(texts):
        _cooldown_until = time.monotonic() + _COOLDOWN_SECONDS
        return None
    return [_normalise([float(x) for x in v]) for v in vectors]


def lexical_vector(text: str) -> list[float]:
    """A vector with no model: hashed words and character triples.

    Words carry the meaning that is there; the triples make it survive plurals
    and typos ("cleanser" / "cleansers", "recieve" / "receive"). Counts are
    square-rooted so one word repeated twenty times cannot dominate a question.
    """
    buckets = [0.0] * LEXICAL_DIM
    for token in _TOKEN.findall((text or "").lower()):
        buckets[_bucket(token)] += 1.0
        for i in range(len(token) - 2):
            buckets[_bucket(token[i : i + 3])] += 0.5
    return _normalise([math.sqrt(b) for b in buckets])


def _bucket(piece: str) -> int:
    return int.from_bytes(hashlib.blake2b(piece.encode(), digest_size=4).digest(), "big") % (
        LEXICAL_DIM
    )


def _normalise(vector: list[float]) -> list[float]:
    length = math.sqrt(sum(x * x for x in vector))
    return [x / length for x in vector] if length else vector


def similarity(a: list[float], b: list[float]) -> float:
    """Cosine of two unit vectors — a plain dot product, 0.0 if either is empty."""
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b, strict=True))

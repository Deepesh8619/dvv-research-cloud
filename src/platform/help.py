"""The app's own help (web/src/help/help.json), searchable by the Copilot.

The same entries the ⓘ buttons show, so the Copilot explains a screen in the
words the screen uses — and nothing it would have to invent.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

HELP_PATH = Path(__file__).resolve().parents[2] / "web" / "src" / "help" / "help.json"


@lru_cache(maxsize=1)
def _entries() -> list[dict]:
    try:
        raw = json.loads(HELP_PATH.read_text())
    except (OSError, ValueError):
        return []
    return [{"topic": key, **{k: str(v) for k, v in entry.items()}} for key, entry in raw.items()]


def search_help(query: str, limit: int = 5) -> list[dict]:
    """Entries ranked by how many of the query's words they hold; the title
    counts double. An empty query lists every topic's title."""
    entries = _entries()
    words = [w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) > 2]
    if not words:
        return [{"topic": e["topic"], "title": e.get("title", "")} for e in entries]

    def score(e: dict) -> int:
        body = " ".join(e.get(k, "") for k in ("what", "why", "how")).lower()
        title = f"{e['topic']} {e.get('title', '')}".lower()
        return sum(2 * (w in title) + (w in body) for w in words)

    ranked = sorted((e for e in entries if score(e)), key=score, reverse=True)
    return ranked[:limit]


if __name__ == "__main__":
    assert _entries(), "help.json not found"
    assert search_help("clean respondents"), "nothing about cleaning"
    assert len(search_help("")) == len(_entries())
    print("ok", len(_entries()), "entries")

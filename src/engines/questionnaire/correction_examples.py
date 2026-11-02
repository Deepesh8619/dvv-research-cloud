"""C2: similar cases a person here already settled, shown to the model as advice.

Pure functions, no database. Rows are dicts: source_text, before, after,
changed_fields (and study_id, used to leave out a study's own fix). An example
never sets a field; the model's answer goes through the same checks as before.
"""

import json

from src.engines.rag.hybrid import bm25

HEADER = "=== HOW PEOPLE HERE CORRECTED SIMILAR QUESTIONS ==="
_INTRO = (
    "These cases come from OTHER questions. Do not copy their question ids or answer "
    "codes. They show how people settled similar wording; the document in front of you "
    "decides.\n")
SOURCE_CLIP = 600
BLOCK_CAP = 3000


def _norm(text: str) -> str:
    return " ".join((text or "").split()).lower()


def similar(query: str, rows: list[dict], k: int, study_id: str | None = None) -> list[dict]:
    """Top k rows by BM25 on source_text; unrelated rows (score 0) are dropped. A row from
    `study_id` whose text is the query itself is left out: it would only echo a fix."""
    q = _norm(query)
    pool = [r for r in rows if r.get("source_text")
            and not (study_id and r.get("study_id") == study_id
                     and q and q in _norm(r["source_text"]))]
    scores = bm25(query, [r["source_text"] for r in pool])
    ranked = sorted(((s, i) for i, s in enumerate(scores) if s > 0), key=lambda t: (-t[0], t[1]))
    return [pool[i] for _, i in ranked[:k]]


def _example(row: dict) -> str:
    fields = row.get("changed_fields") or []
    before, after = row.get("before") or {}, row.get("after") or {}
    said = {f: before.get(f) for f in fields}
    right = {f: after.get(f) for f in fields}
    dump = lambda v: json.dumps(v, ensure_ascii=False, separators=(",", ":"), default=str)  # noqa: E731
    return (f"Text: {(row.get('source_text') or '')[:SOURCE_CLIP]}\n"
            f"reader said: {dump(said)}\ncorrect: {dump(right)}\n")


def fit(rows: list[dict]) -> list[dict]:
    """The leading rows whose examples fit the cap, whole ones only."""
    size, kept = len(HEADER) + len(_INTRO) + 2, []
    for row in rows:
        size += len(_example(row)) + 1
        if size > BLOCK_CAP:
            break
        kept.append(row)
    return kept


def prompt_block(rows: list[dict]) -> str:
    """The block for a prompt; "" for no rows. 3,000 characters at most."""
    kept = fit(rows)
    if not kept:
        return ""
    return f"{HEADER}\n{_INTRO}" + "".join("\n" + _example(r) for r in kept) + "\n"

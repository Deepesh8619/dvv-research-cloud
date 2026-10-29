"""What a teaching sentence is read against, so it is understood, not guessed.

A founder writes "the Spanish line is the question translated, not an answer".
Read alone, that sentence can only become prose appended to a model prompt —
which does nothing for the questions the deterministic reader already handles,
and those are most of them. Read WITH its context it can become what it
actually is: a convention the reader applies itself.

Three things are retrieved for it:

- the question as the DOCUMENT writes it, and what the reader made of it (type,
  codes, the evidence for each field) — so the mistake is visible;
- what the reader can be taught directly (`TEACHABLE`), with an example each;
- lessons already taught that resemble it, by meaning (the project embedder,
  or its lexical fallback) — so the same thing is not taught twice.

And one thing is checked on the way out (`grounded_pattern`): a convention the
model proposes must quote text that is really in this document. A pattern the
document does not contain would teach nothing and is dropped.
"""

from __future__ import annotations

import json
import re
from typing import Any

from src.engines.questionnaire.learned import validate_pattern

#: What the reader can be taught directly: kind -> (what it means, an example).
TEACHABLE: dict[str, tuple[str, str]] = {
    "type_signal": ("a marker stating the answer type (value: single, multi, numeric, "
                    "open_text, grid_single, rank, info …)", '"[SHOWCARD]" -> single'),
    "option_tag": ("a tag on one answer (value: terminate, exclusive, anchor)",
                   '"[SO]" -> terminate'),
    "randomize_signal": ("a marker meaning the answers are shuffled (no value)",
                         '"[SCRAMBLE]"'),
    "hidden_variable": ("wording that marks a variable the script computes and never asks "
                        "(no value)", '"SAMPLE SOURCE"'),
    "screen_out_range": ("a phrase before a number range that screens out everyone outside "
                         "it (no value)", '"CONTINUE IF AGES" before "18-60"'),
    "field_alias": ("a row label in a spec table naming a field (value: qid, type, base, "
                    "show, randomize, notes, text)", '"Asked of:" -> base'),
    "question_label": ("how this format writes a question label, copied exactly (no value)",
                       '"QGENDER."'),
}

#: How alike two lessons must be to count as "already taught", per embedder.
#: Lexical: a reworded lesson scored 0.53, unrelated ones at most 0.36 (measured).
# ponytail: the model threshold is nomic's usual band, not measured here; tune
# it on real lessons once the server has an embedder.
_SIMILAR_MIN = {"lexical": 0.45, "model": 0.75}


def question_read(question: dict | None) -> dict[str, Any]:
    """The question as written and as read, compact enough for a prompt."""
    if not question:
        return {}
    options = question.get("options") or []
    return {
        "qid": question.get("qid"),
        "as_written": (question.get("source_text") or "")[:1500],
        "read_as": {
            "type": question.get("type"),
            "text": question.get("text"),
            "options": [f"{o.get('code')}: {o.get('label')}" for o in options][:25],
            "rows": len(question.get("rows") or []),
            "columns": len(question.get("cols") or []),
            "flags": question.get("flags") or [],
            "evidence": question.get("extraction_evidence") or {},
        },
    }


def similar_lessons(instruction: str, rules: list[dict]) -> list[dict]:
    """Lessons already taught that mean much the same, best first (at most 3)."""
    from src.engines.rag.embeddings import LEXICAL, embed, similarity

    texts = [r.get("rule_text") or "" for r in rules]
    if not instruction.strip() or not any(texts):
        return []
    (query,), made_by = embed([instruction], kind="query")
    vectors, _ = embed(texts, kind="document")
    floor = _SIMILAR_MIN["lexical" if made_by == LEXICAL else "model"]
    scored = sorted(((similarity(query, v), r) for v, r in zip(vectors, rules, strict=True)),
                    key=lambda pair: -pair[0])
    return [{**r, "similarity": round(score, 2)} for score, r in scored[:3]
            if score >= floor]


def prompt_context(question: dict | None, similar: list[dict]) -> str:
    """The retrieved context, as prompt sections."""
    parts = []
    if read := question_read(question):
        parts.append("THE QUESTION AS THE DOCUMENT WRITES IT:\n" + read["as_written"])
        parts.append("WHAT THE READER MADE OF IT:\n" + json.dumps(read["read_as"], indent=1,
                                                                   ensure_ascii=False)[:2500])
    parts.append("WHAT THE READER CAN BE TAUGHT DIRECTLY (pattern_kind: meaning; example):\n"
                 + "\n".join(f"- {k}: {m}; e.g. {ex}" for k, (m, ex) in TEACHABLE.items()))
    if similar:
        parts.append("ALREADY TAUGHT, SIMILAR — do not teach it again; say so if it is the "
                     "same:\n" + "\n".join(f"- {s.get('rule_text')}" for s in similar))
    return "\n\n".join(parts) + "\n\n"


def grounded_pattern(kind: str | None, pattern: str | None, value: str | None,
                     document_texts: list[tuple[str, str]]) -> dict | None:
    """A proposed convention, kept only if it is valid AND the document contains it.

    `document_texts` is [(qid, source_text)] for every question. Returns the
    pattern with the questions it would touch, or None.
    """
    kind, pattern, value = (kind or "").strip(), (pattern or "").strip(), (value or "").strip()
    if not kind or not pattern or validate_pattern(kind, pattern, value) is not None:
        return None
    if kind == "question_label":
        needle = re.sub(r"\s+", " ", pattern).lower()
        hits = [q for q, text in document_texts if needle in (text or "").lower()]
    else:
        needle = pattern.strip("[]").strip().lower()
        hits = [q for q, text in document_texts if needle in (text or "").lower()]
    if not hits:
        return None  # the document does not contain it: teaching it would do nothing
    meaning, _ = TEACHABLE.get(kind, ("", ""))
    return {"kind": kind, "pattern": pattern, "value": value or None,
            "meaning": meaning.split(" (")[0], "affects": hits}

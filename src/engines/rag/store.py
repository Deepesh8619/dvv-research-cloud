"""Every project's content in the database, as vectors the copilot can search.

One row per piece of a project — a question with its answer list and its base,
the study overview — with the vector that lets "which question covers price
sensitivity" find "how much would you be willing to pay".

Two properties matter more than the retrieval quality:

* **It is never stale.** The index carries a fingerprint of the exact text it
  was built from. A re-read, a correction or an edit changes that fingerprint,
  and the next search rebuilds rather than answering from the old wording. A
  copilot quoting a question that was fixed yesterday is worse than one that
  says it cannot find it.
* **It is never mixed.** Vectors from different makers mean nothing to each
  other, so each row records what made it, and a change of maker rebuilds.

Search is a dot product over one project's rows in Python. A questionnaire is
hundreds of questions, not millions of documents.
`ponytail: brute-force scan; move to pgvector if a project ever gets large.`
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any

from src.engines.rag.embeddings import embed, similarity

logger = logging.getLogger(__name__)

MAX_CHUNK_CHARS = 2000  # a question with a long answer list, not a document

#: How many answer labels go into a question's chunk. A 40-brand list adds
#: little to what the question is ABOUT and crowds out its wording.
MAX_LABELS = 30


def chunks_for_survey(survey: Any) -> list[dict[str, str]]:
    """What is worth retrieving from one questionnaire: [{kind, ref, text}]."""
    chunks: list[dict[str, str]] = []
    brief = (getattr(survey, "metadata", None) or {}).get("study_overview")
    if brief:
        chunks.append({"kind": "brief", "ref": "study_overview", "text": _render(brief)})
    for question in getattr(survey, "questions", []) or []:
        chunks.append(
            {"kind": "question", "ref": question.qid, "text": _question_text(question)}
        )
    return chunks


def _question_text(question: Any) -> str:
    """The question as a person would read it: wording, answers, who is asked."""
    parts = [f"{question.qid}. {question.text or ''}".strip()]
    answer_list = (
        list(question.options or []) + list(question.rows or []) + list(question.cols or [])
    )
    labels = [o.label for o in answer_list if getattr(o, "label", None)]
    if labels:
        parts.append("Answers: " + "; ".join(labels[:MAX_LABELS]))
    base = (question.extraction_evidence or {}).get("show_if")
    if base:
        parts.append(f"Asked if: {base}")
    return "\n".join(parts)[:MAX_CHUNK_CHARS]


def _render(value: Any) -> str:
    if isinstance(value, dict):
        return "\n".join(f"{k}: {_render(v)}" for k, v in value.items() if v)[:MAX_CHUNK_CHARS]
    if isinstance(value, list):
        return "; ".join(_render(v) for v in value if v)[:MAX_CHUNK_CHARS]
    return str(value)[:MAX_CHUNK_CHARS]


def fingerprint(chunks: list[dict[str, str]]) -> str:
    """Identifies the exact content an index was built from."""
    joined = "\n\x1e".join(f"{c['kind']}\x1f{c['ref']}\x1f{c['text']}" for c in chunks)
    return hashlib.sha256(joined.encode()).hexdigest()[:32]


def ensure_index(db, study_id: str, survey: Any, force: bool = False) -> dict:
    """Index a project's content, or leave it alone if it is already current."""
    from api.db import ProjectChunkDB

    chunks = chunks_for_survey(survey)
    print_ = fingerprint(chunks)
    existing = db.query(ProjectChunkDB).filter(ProjectChunkDB.study_id == study_id).all()
    if existing and not force and all(row.fingerprint == print_ for row in existing):
        return {"indexed": len(existing), "model": existing[0].model, "rebuilt": False}

    vectors, model = embed([c["text"] for c in chunks])
    now = datetime.now(UTC).isoformat()
    for row in existing:
        db.delete(row)
    for chunk, vector in zip(chunks, vectors, strict=True):
        db.add(
            ProjectChunkDB(
                id=f"{study_id}:{chunk['kind']}:{chunk['ref']}",
                study_id=study_id,
                kind=chunk["kind"],
                ref=chunk["ref"],
                text=chunk["text"],
                model=model,
                vector_json=json.dumps([round(x, 6) for x in vector]),
                fingerprint=print_,
                created_at=now,
            )
        )
    db.commit()
    logger.info("[%s] indexed %d chunks with %s", study_id, len(chunks), model)
    return {"indexed": len(chunks), "model": model, "rebuilt": True}


def search(db, study_id: str, query: str, survey: Any = None, k: int = 5) -> dict:
    """The k pieces of this project closest in meaning to `query`.

    Rebuilds first when the index is missing, stale, or was made by a different
    embedder than the one answering now — comparing across makers would return
    confident nonsense.
    """
    from api.db import ProjectChunkDB

    text = (query or "").strip()
    if not text:
        return {"matches": [], "model": None, "note": "Empty query."}

    [vector], model = embed([text], kind="query")
    rows = db.query(ProjectChunkDB).filter(ProjectChunkDB.study_id == study_id).all()
    stale = rows and (
        rows[0].model != model
        or (survey is not None and rows[0].fingerprint != fingerprint(chunks_for_survey(survey)))
    )
    if survey is not None and (not rows or stale):
        ensure_index(db, study_id, survey, force=True)
        rows = db.query(ProjectChunkDB).filter(ProjectChunkDB.study_id == study_id).all()
    if not rows:
        return {"matches": [], "model": model,
                "note": "This project has nothing indexed yet."}
    if rows[0].model != model:
        return {"matches": [], "model": model,
                "note": f"Index was built by {rows[0].model} and cannot be searched "
                        f"with {model}; re-index this project."}

    scored = [
        (similarity(vector, json.loads(row.vector_json)), row)
        for row in rows
    ]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return {
        "model": model,
        "matches": [
            {"kind": row.kind, "ref": row.ref, "text": row.text, "score": round(score, 3)}
            for score, row in scored[:k]
            if score > 0
        ],
    }

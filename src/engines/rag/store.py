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

Rows are written and searched through `src.platform.vectors.get_store()`:
pgvector on Postgres when it is installed, a numpy dot product otherwise.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from typing import Any

from src.engines.rag.embeddings import embed
from src.platform.vectors import get_store

logger = logging.getLogger(__name__)

MAX_CHUNK_CHARS = 2000  # a question with a long answer list, not a document

#: The instrument. Rebuilt whole whenever its wording changes.
SURVEY_KINDS = ("question", "brief")
#: What one respondent typed. Added as it arrives, never rewritten.
VERBATIM = "verbatim"
#: What the project has learned about itself: QC findings on its data, and the
#: lessons taught about reading its documents. Rebuilt whole when they change.
CONTEXT_KINDS = ("finding", "lesson")
#: New verbatims embedded per call. A 2,000-complete study with five open-ends
#: is 10,000 of them, and one API request must not sit there embedding all of
#: them — the caller is told how many are left and asks again.
MAX_NEW_VERBATIMS = 500
#: A one-word answer ("nothing", "n/a") retrieves badly and says nothing.
MIN_VERBATIM_CHARS = 12

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
    """Index a project's questionnaire, or leave it alone if it is current.

    Only the instrument: respondent verbatims live in the same table but are
    indexed one at a time as they arrive (`ensure_verbatim_index`), and a
    re-read of the questionnaire must not throw them away.
    """
    from api.db import ProjectChunkDB

    chunks = chunks_for_survey(survey)
    print_ = fingerprint(chunks)
    existing = (
        db.query(ProjectChunkDB)
        .filter(ProjectChunkDB.study_id == study_id)
        .filter(ProjectChunkDB.kind.in_(SURVEY_KINDS))
        .all()
    )
    if existing and not force and all(row.fingerprint == print_ for row in existing):
        return {"indexed": len(existing), "model": existing[0].model, "rebuilt": False}

    vectors, model = embed([c["text"] for c in chunks])
    now = datetime.now(UTC).isoformat()
    store = get_store(db)
    for row in existing:
        db.delete(row)
    for chunk, vector in zip(chunks, vectors, strict=True):
        store.write(
            db,
            ProjectChunkDB(
                id=f"{study_id}:{chunk['kind']}:{chunk['ref']}",
                study_id=study_id,
                kind=chunk["kind"],
                ref=chunk["ref"],
                text=chunk["text"],
                model=model,
                fingerprint=print_,
                created_at=now,
            ),
            vector,
        )
    db.commit()
    logger.info("[%s] indexed %d chunks with %s", study_id, len(chunks), model)
    return {"indexed": len(chunks), "model": model, "rebuilt": True}


def context_chunks(db, study_id: str, profile_slug: str | None) -> list[dict[str, str]]:
    """The project's QC findings and the lessons taught about its documents."""
    import json as _json

    from api.db import CorrectionDB, ProfileRuleDB, get_study

    chunks: list[dict[str, str]] = []
    study = get_study(db, study_id)
    if study is not None and study.crosscheck_json:
        try:
            findings = _json.loads(study.crosscheck_json).get("findings") or []
        except (TypeError, ValueError):
            findings = []
        for n, f in enumerate(findings, start=1):
            text = (f"QC finding on {f.get('qid') or 'the data'} ({f.get('kind', '')}, "
                    f"{f.get('severity', '')}): {f.get('detail', '')}"
                    + (f" — {f['n_rows']} rows" if f.get("n_rows") else ""))
            chunks.append({"kind": "finding", "ref": f"finding-{n}",
                           "text": text[:MAX_CHUNK_CHARS]})
    if profile_slug:  # lessons about this document format, from every project that taught them
        for rule in db.query(ProfileRuleDB).filter(ProfileRuleDB.profile_slug == profile_slug,
                                                   ProfileRuleDB.active.is_(True)):
            means = rule.pattern_value or rule.pattern_kind
            how = f" (in the document, \"{rule.pattern}\" -> {means})" if rule.pattern else ""
            text = f"Taught rule for {profile_slug}: {rule.rule_text}{how}"
            chunks.append({"kind": "lesson", "ref": f"rule-{rule.id}",
                           "text": text[:MAX_CHUNK_CHARS]})
    for c in db.query(CorrectionDB).filter(CorrectionDB.study_id == study_id):
        about = f" about {c.question_id}" if c.question_id else ""
        text = f"Correction{about} ({c.status}): {c.instruction}"
        chunks.append({"kind": "lesson", "ref": f"correction-{c.id}",
                       "text": text[:MAX_CHUNK_CHARS]})
    return chunks


def ensure_context_index(db, study_id: str, profile_slug: str | None) -> dict:
    """Index the project's findings and lessons, or leave them if current."""
    from api.db import ProjectChunkDB

    chunks = context_chunks(db, study_id, profile_slug)
    print_ = fingerprint(chunks)
    existing = (db.query(ProjectChunkDB).filter(ProjectChunkDB.study_id == study_id)
                .filter(ProjectChunkDB.kind.in_(CONTEXT_KINDS)).all())
    if existing and all(r.fingerprint == print_ for r in existing) and len(existing) == len(chunks):
        return {"indexed": len(existing), "rebuilt": False}
    for row in existing:
        db.delete(row)
    if not chunks:
        db.commit()
        return {"indexed": 0, "rebuilt": True}
    vectors, model = embed([c["text"] for c in chunks])
    now = datetime.now(UTC).isoformat()
    store = get_store(db)
    for chunk, vector in zip(chunks, vectors, strict=True):
        store.write(db, ProjectChunkDB(
            id=f"{study_id}:{chunk['kind']}:{chunk['ref']}", study_id=study_id,
            kind=chunk["kind"], ref=chunk["ref"], text=chunk["text"], model=model,
            fingerprint=print_, created_at=now), vector)
    db.commit()
    return {"indexed": len(chunks), "rebuilt": True}


def ensure_verbatim_index(db, study_id: str, survey: Any, force: bool = False) -> dict:
    """Embed the open-end answers this project has collected but not indexed.

    Verbatims arrive one respondent at a time and never change once given, so
    this adds rather than rebuilds: an answer already indexed is skipped, and a
    call on a project with nothing new does no work at all.

    Only COMPLETES are indexed. A screened-out respondent's half-answer is not
    part of the study's findings, and a copilot quoting one as "what people
    said" would be reporting somebody the study excluded on purpose.
    """
    from api.db import (
        ProjectChunkDB,
        answers_by_session,
        list_fielding_links_for_study,
        list_respondent_sessions,
    )

    open_ends = {
        q.qid: (q.text or "")
        for q in getattr(survey, "questions", []) or []
        if str(getattr(q.type, "value", q.type)) == "open_text"
    }
    if not open_ends:
        return {"indexed": 0, "remaining": 0, "model": None,
                "note": "This questionnaire has no open-ended questions."}

    known = {
        row.id
        for row in db.query(ProjectChunkDB.id)
        .filter(ProjectChunkDB.study_id == study_id)
        .filter(ProjectChunkDB.kind == VERBATIM)
        .all()
    }
    if force:
        for row in (
            db.query(ProjectChunkDB)
            .filter(ProjectChunkDB.study_id == study_id)
            .filter(ProjectChunkDB.kind == VERBATIM)
            .all()
        ):
            db.delete(row)
        known = set()

    pending: list[dict[str, str]] = []
    for link in list_fielding_links_for_study(db, study_id):
        completes = {
            s.id for s in list_respondent_sessions(db, link.id) if s.status_code == 10
        }
        for session_id, answers in answers_by_session(db, link.id).items():
            if session_id not in completes:
                continue
            for qid, wording in open_ends.items():
                said = answers.get(qid)
                if not isinstance(said, str) or len(said.strip()) < MIN_VERBATIM_CHARS:
                    continue
                ref = f"{qid}:{session_id}"
                if f"{study_id}:{VERBATIM}:{ref}" in known:
                    continue
                # The question travels with the answer: "it was too greasy"
                # retrieves for "texture complaints" only next to what was asked.
                said_with_question = f"{qid}. {wording}\nAnswer: {said.strip()}"
                pending.append({"kind": VERBATIM, "ref": ref,
                                "text": said_with_question[:MAX_CHUNK_CHARS]})

    batch, remaining = pending[:MAX_NEW_VERBATIMS], max(0, len(pending) - MAX_NEW_VERBATIMS)
    if not batch:
        return {"indexed": 0, "remaining": 0, "model": None}
    vectors, model = embed([c["text"] for c in batch])
    now = datetime.now(UTC).isoformat()
    store = get_store(db)
    for chunk, vector in zip(batch, vectors, strict=True):
        store.write(
            db,
            ProjectChunkDB(
                id=f"{study_id}:{VERBATIM}:{chunk['ref']}",
                study_id=study_id,
                kind=VERBATIM,
                ref=chunk["ref"],
                text=chunk["text"],
                model=model,
                fingerprint=fingerprint([chunk]),
                created_at=now,
            ),
            vector,
        )
    db.commit()
    logger.info("[%s] indexed %d verbatims with %s", study_id, len(batch), model)
    return {"indexed": len(batch), "remaining": remaining, "model": model}


def search(
    db, study_id: str, query: str, survey: Any = None, k: int = 5,
    kinds: tuple[str, ...] = SURVEY_KINDS,
) -> dict:
    """The k pieces of this project closest in meaning to `query`.

    Rebuilds first when the index is missing, stale, or was made by a different
    embedder than the one answering now — comparing across makers would return
    confident nonsense.
    """
    from api.db import ProjectChunkDB

    text = (query or "").strip()
    if not text:
        return {"matches": [], "model": None, "note": "Empty query."}

    def load():
        return (
            db.query(ProjectChunkDB)
            .filter(ProjectChunkDB.study_id == study_id)
            .filter(ProjectChunkDB.kind.in_(kinds))
            .all()
        )

    [vector], model = embed([text], kind="query")
    rows = load()
    wrong_maker = bool(rows) and rows[0].model != model
    stale = wrong_maker or (
        rows
        and survey is not None
        and VERBATIM not in kinds
        and rows[0].fingerprint != fingerprint(chunks_for_survey(survey))
    )
    if survey is not None and (not rows or stale):
        if VERBATIM in kinds:
            ensure_verbatim_index(db, study_id, survey, force=wrong_maker)
        else:
            ensure_index(db, study_id, survey, force=True)
        rows = load()
    if not rows:
        return {"matches": [], "model": model,
                "note": "This project has nothing indexed yet."}
    if rows[0].model != model:
        return {"matches": [], "model": model,
                "note": f"Index was built by {rows[0].model} and cannot be searched "
                        f"with {model}; re-index this project."}

    # Hybrid: the vector store's nearest pieces, BM25 over every piece, and an
    # exact question code, fused by reciprocal rank (src/engines/rag/hybrid.py).
    from src.engines.rag.hybrid import bm25, codes_in, fuse

    rows = [r for r in rows if r.model == model]
    by_id = {r.id: r for r in rows}
    scored = get_store(db).search(db, study_id, vector, kinds=kinds, k=max(k * 4, 20),
                                  model=model)
    meaning = {row.id: score for score, row in scored if score > 0}
    ids = list(by_id)
    words = dict(zip(ids, bm25(text, [by_id[i].text for i in ids]), strict=True))
    named = codes_in(text, [by_id[i].ref for i in ids])
    code = {i for i in ids if by_id[i].ref in named}
    return {
        "model": model,
        "matches": [
            {"kind": by_id[i].kind, "ref": by_id[i].ref, "text": by_id[i].text,
             "score": round(meaning.get(i, 0.0), 3), "rank": rank, "matched_by": how,
             # What an answer cites, so a reader can find the passage it rests on.
             "cite": f"{by_id[i].kind}:{by_id[i].ref}"}
            for i, rank, how in fuse(ids, meaning, words, code, k)
        ],
    }

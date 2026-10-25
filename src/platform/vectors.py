"""Where project vectors are written and searched.

One interface, two stores: numpy over a study's rows (works on every backend),
and pgvector's cosine index on Postgres when the extension, the column and a
768-wide vector are all there. `vector_json` is always written, so the numpy
path and a rebuild never depend on the extension.
"""

from __future__ import annotations

import json
import weakref
from typing import Protocol

import numpy as np
from sqlalchemy import inspect, text

PG_DIM = 768  # nomic-embed-text; the lexical fallback (256) stays on numpy


class VectorStore(Protocol):
    def search(self, db, study_id: str, vector: list[float], *, kinds: tuple[str, ...],
               k: int, model: str) -> list[tuple[float, object]]: ...

    def write(self, db, row, vector: list[float]) -> None: ...


def _to_json(vector: list[float]) -> str:
    return json.dumps([round(x, 6) for x in vector])


def _literal(vector: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


def _rows(db, study_id: str, kinds: tuple[str, ...], model: str):
    from api.db import ProjectChunkDB

    return (
        db.query(ProjectChunkDB)
        .filter(ProjectChunkDB.study_id == study_id)
        .filter(ProjectChunkDB.kind.in_(kinds))
        .filter(ProjectChunkDB.model == model)
        .all()
    )


class NumpyStore:
    def search(self, db, study_id, vector, *, kinds, k, model):
        rows = [r for r in _rows(db, study_id, kinds, model) if r.vector_json]
        vectors = [json.loads(r.vector_json) for r in rows]
        keep = [i for i, v in enumerate(vectors) if len(v) == len(vector)]
        if not keep:
            return []
        scores = np.array([vectors[i] for i in keep]) @ np.array(vector)
        order = np.argsort(-scores, kind="stable")[:k]
        return [(float(scores[i]), rows[keep[i]]) for i in order]

    def write(self, db, row, vector):
        row.vector_json = _to_json(vector)
        db.add(row)


class PgVectorStore(NumpyStore):
    def search(self, db, study_id, vector, *, kinds, k, model):
        if len(vector) != PG_DIM:
            return super().search(db, study_id, vector, kinds=kinds, k=k, model=model)
        where = "study_id = :s AND model = :m AND kind IN :kinds"
        args = {"s": study_id, "m": model, "kinds": tuple(kinds)}
        from sqlalchemy import bindparam

        missing = db.execute(
            text(f"SELECT 1 FROM project_chunks WHERE {where} AND embedding IS NULL LIMIT 1")
            .bindparams(bindparam("kinds", expanding=True)), args,
        ).first()
        if missing:  # rows written before the column existed: numpy still sees them
            return super().search(db, study_id, vector, kinds=kinds, k=k, model=model)
        found = db.execute(
            text(
                f"SELECT id, 1 - (embedding <=> CAST(:q AS vector)) AS score "
                f"FROM project_chunks WHERE {where} "
                f"ORDER BY embedding <=> CAST(:q AS vector) LIMIT :k"
            ).bindparams(bindparam("kinds", expanding=True)),
            {**args, "q": _literal(vector), "k": k},
        ).all()
        from api.db import ProjectChunkDB

        by_id = {r.id: r for r in db.query(ProjectChunkDB).filter(
            ProjectChunkDB.id.in_([f[0] for f in found]))}
        return [(float(score), by_id[i]) for i, score in found if i in by_id]

    def write(self, db, row, vector):
        super().write(db, row, vector)
        if len(vector) == PG_DIM:
            db.flush()
            db.execute(
                text("UPDATE project_chunks SET embedding = CAST(:q AS vector) WHERE id = :id"),
                {"q": _literal(vector), "id": row.id},
            )


_chosen: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def get_store(db) -> VectorStore:
    """pgvector when this database has it, numpy otherwise. Cached per engine."""
    engine = db.get_bind()
    if engine not in _chosen:
        has_column = engine.dialect.name == "postgresql" and any(
            c["name"] == "embedding"
            for c in inspect(engine).get_columns("project_chunks")
        )
        _chosen[engine] = PgVectorStore() if has_column else NumpyStore()
    return _chosen[engine]


def near_duplicates(
    db, study_id: str, kind: str = "verbatim", threshold: float = 0.95,
    model: str | None = None,
) -> list[tuple[str, str, float]]:
    """Pairs of refs whose vectors are closer than `threshold`, best first.

    ponytail: full pairwise matrix in memory; chunk it if one study passes ~20k verbatims.
    """
    from api.db import ProjectChunkDB

    rows = (
        db.query(ProjectChunkDB)
        .filter(ProjectChunkDB.study_id == study_id, ProjectChunkDB.kind == kind)
        .order_by(ProjectChunkDB.id)
        .all()
    )
    rows = [r for r in rows if r.vector_json]
    if model is None and rows:
        model = rows[0].model
    rows = [r for r in rows if r.model == model]
    if len(rows) < 2:
        return []
    matrix = np.array([json.loads(r.vector_json) for r in rows])
    matrix = matrix / np.maximum(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-12)
    scores = matrix @ matrix.T
    i, j = np.where(np.triu(scores, k=1) >= threshold)
    pairs = [(rows[a].ref, rows[b].ref, round(float(scores[a, b]), 4))
             for a, b in zip(i, j, strict=True)]
    return sorted(pairs, key=lambda p: -p[2])

"""The vector store interface: numpy ranking, near-duplicates, backend choice."""

import json
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.db import Base, ProjectChunkDB
from src.platform.vectors import NumpyStore, PgVectorStore, get_store, near_duplicates

VECS = {
    "a": [1.0, 0.0, 0.0],
    "b": [0.9, 0.1, 0.0],
    "c": [0.0, 1.0, 0.0],
    "d": [0.0, 0.0, 1.0],
}


def _norm(v):
    n = sum(x * x for x in v) ** 0.5
    return [x / n for x in v]


def _fill(db, kind="verbatim", vecs=VECS, model="m"):
    store = get_store(db)
    for ref, v in vecs.items():
        store.write(db, ProjectChunkDB(id=f"S:{kind}:{ref}", study_id="S", kind=kind, ref=ref,
                                       text=ref, model=model, fingerprint="f"), _norm(v))
    db.commit()


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def test_sqlite_gets_the_numpy_store(db):
    assert isinstance(get_store(db), NumpyStore)
    assert not isinstance(get_store(db), PgVectorStore)


def test_numpy_ranks_like_the_old_loop(db):
    _fill(db)
    q = _norm([1.0, 0.2, 0.0])
    got = get_store(db).search(db, "S", q, kinds=("verbatim",), k=3, model="m")
    old = sorted(
        ((sum(x * y for x, y in zip(q, json.loads(r.vector_json), strict=True)), r.ref)
         for r in db.query(ProjectChunkDB)),
        reverse=True,
    )[:3]
    assert [r.ref for _, r in got] == [ref for _, ref in old]
    assert [round(s, 5) for s, _ in got] == [round(s, 5) for s, _ in old]


def test_search_ignores_other_models_and_kinds(db):
    _fill(db, model="other")
    _fill(db, kind="question")
    got = get_store(db).search(db, "S", _norm([1, 0, 0]), kinds=("verbatim",), k=3, model="m")
    assert got == []


def test_near_duplicates_finds_the_planted_pair_only(db):
    _fill(db)
    pairs = near_duplicates(db, "S", threshold=0.95)
    assert [(a, b) for a, b, _ in pairs] == [("a", "b")]


@pytest.mark.skipif(not os.getenv("DVV_TEST_PG"), reason="needs DVV_TEST_PG with pgvector")
def test_pgvector_order_matches_numpy():
    engine = create_engine(os.environ["DVV_TEST_PG"])
    Base.metadata.create_all(bind=engine)
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.execute(text(
            "ALTER TABLE project_chunks ADD COLUMN IF NOT EXISTS embedding vector(768)"))
        conn.execute(text("DELETE FROM project_chunks WHERE study_id = 'S'"))
    db = sessionmaker(bind=engine)()
    try:
        pad = lambda v: _norm(list(v) + [0.0] * (768 - len(v)))  # noqa: E731
        vecs = {ref: pad(v) for ref, v in VECS.items()}
        store = get_store(db)
        assert isinstance(store, PgVectorStore)
        for ref, v in vecs.items():
            store.write(db, ProjectChunkDB(id=f"S:verbatim:{ref}", study_id="S", kind="verbatim",
                                           ref=ref, text=ref, model="m", fingerprint="f"), v)
        db.commit()
        q = pad([1.0, 0.2, 0.0])
        kw = dict(kinds=("verbatim",), k=4, model="m")
        pg = store.search(db, "S", q, **kw)
        np_ = NumpyStore().search(db, "S", q, **kw)
        assert [r.ref for _, r in pg] == [r.ref for _, r in np_]
    finally:
        db.execute(text("DELETE FROM project_chunks WHERE study_id = 'S'"))
        db.commit()
        db.close()

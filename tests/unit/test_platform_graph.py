import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.db import Base, GraphEdgeDB, GraphNodeDB
from src.platform.graph import (
    delete_study_graph,
    impact,
    lineage,
    link,
    node_id,
    unlink_kind,
    upsert_node,
)


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/g.db")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def chain(db, study="s1"):
    kinds = ("data_file", "question", "variable", "cell", "sentence")
    ids = {k: upsert_node(db, study, k, k) for k in kinds}
    link(db, study, ids["variable"], ids["question"], "maps_to")
    link(db, study, ids["question"], ids["data_file"], "from")
    link(db, study, ids["cell"], ids["variable"], "computed_from")
    link(db, study, ids["sentence"], ids["cell"], "cites")
    db.commit()
    return ids


def test_upsert_twice_one_row_version_bumps_only_on_change(db):
    a = upsert_node(db, "s1", "q", "Q1", "L", {"a": 1})
    upsert_node(db, "s1", "q", "Q1", "L", {"a": 1})
    db.commit()
    assert a == node_id("s1", "q", "Q1") == "s1:q:Q1"
    assert db.query(GraphNodeDB).count() == 1
    assert db.get(GraphNodeDB, a).version == 1
    upsert_node(db, "s1", "q", "Q1", "L2", {"a": 1})
    db.commit()
    assert db.get(GraphNodeDB, a).version == 2


def test_link_idempotent_and_unlink_kind(db):
    a, b = upsert_node(db, "s1", "x", "a"), upsert_node(db, "s1", "x", "b")
    assert link(db, "s1", a, b, "k") == link(db, "s1", a, b, "k") == f"{a}|k|{b}"
    db.commit()
    assert db.query(GraphEdgeDB).count() == 1
    unlink_kind(db, "s1", a, "k")
    db.commit()
    assert db.query(GraphEdgeDB).count() == 0


def test_lineage_depths(db):
    ids = chain(db)
    out = lineage(db, "s1", ids["cell"])
    got = [(r["kind"], r["depth"]) for r in out]
    assert got == [("variable", 1), ("question", 2), ("data_file", 3)]
    assert out[0]["via"] == "computed_from"
    assert set(out[0]) == {"id", "kind", "ref", "label", "props", "depth", "via"}


def test_impact_finds_sentence_through_cell(db):
    ids = chain(db)
    out = impact(db, "s1", ids["question"])
    assert [(r["kind"], r["depth"]) for r in out] == [("variable", 1), ("cell", 2), ("sentence", 3)]


def test_cycle_terminates(db):
    a, b = upsert_node(db, "s1", "x", "a"), upsert_node(db, "s1", "x", "b")
    link(db, "s1", a, b, "k")
    link(db, "s1", b, a, "k")
    db.commit()
    out = lineage(db, "s1", a)
    assert sorted((r["id"], r["depth"]) for r in out) == [(a, 2), (b, 1)]


def test_max_depth(db):
    ids = chain(db)
    assert [r["depth"] for r in lineage(db, "s1", ids["cell"], max_depth=2)] == [1, 2]


def test_other_studies_isolated(db):
    ids = chain(db)
    chain(db, "s2")
    link(db, "s2", ids["cell"], "s2:data_file:data_file", "x")  # cross-study edge ignored
    db.commit()
    assert {r["id"].split(":")[0] for r in lineage(db, "s1", ids["cell"])} == {"s1"}
    assert delete_study_graph(db, "s2") == 10
    db.commit()
    assert db.query(GraphNodeDB).filter_by(study_id="s2").count() == 0
    assert len(lineage(db, "s1", ids["cell"])) == 3

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.db import Base
from src.engines.tabs.provenance import question_node
from src.platform import graph


def test_a_multi_select_option_table_finds_its_question_whatever_the_id_looks_like():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    for qid in ("Q3", "AWR_CONSIDER"):
        graph.upsert_node(db, "s", "question", qid)
    db.commit()
    assert question_node(db, "s", "AWR_CONSIDERr1").ref == "AWR_CONSIDER"
    assert question_node(db, "s", "Q3r12").ref == "Q3"
    assert question_node(db, "s", "Q3").ref == "Q3"
    assert question_node(db, "s", "NOPEr1") is None


def test_the_cell_step_shows_the_interval():
    from src.engines.tabs.provenance import provenance
    t = {"id": "t1", "stub": "Q1", "filter": None,
         "columns": [{"label": "Total", "base": 100.0, "unweighted_base": 100,
                      "effective_base": 100.0}],
         "rows": [{"label": "Yes", "pct": [50.0]}]}
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    out = provenance(sessionmaker(bind=engine)(), "s", {}, t, 0, 0, removed={}, data_file={})
    assert "50% (95% interval 40%–60%, effective base 100)" in out["steps"][0]["detail"]

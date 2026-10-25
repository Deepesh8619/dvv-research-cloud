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

"""The answers behind a respondent's flags."""

import polars as pl

from src.core.models import DataMap, Variable, VarType
from src.engines.qc.evidence import evidence


def test_each_flag_shows_its_question_and_the_answer_in_words():
    grid = [Variable(name=f"G_{i}", label="Rate each", type=VarType.SINGLE, question_ref="G",
                     question_text="Rate each", values={3: '${"Agree3" if (x) else "y"}', 1: "No"})
            for i in range(4)]
    dm = DataMap([*grid, Variable(name="Q5", label="Why?", type=VarType.TEXT, question_ref="Q5",
                                  question_text="Why did you choose it?")])
    df = pl.DataFrame({**{f"G_{i}": [3, 1] for i in range(4)}, "Q5": ["asdkjh qwe", "fine"]})
    e = evidence(df, dm, 0, [{"check": "gibberish", "detail": "Q5"},
                             {"check": "straightliner", "detail": "same answer across a grid"},
                             {"check": "speeder", "detail": "short interview (qtime)"}])
    gib, line, fast = e["items"]
    assert gib["answers"] == [{"column": "Q5", "question": "Why did you choose it?",
                               "answer": "asdkjh qwe"}]
    assert line["answers"][0]["answer"] == "all 4 rows answered “Agree”"  # script label cleaned
    assert fast["answers"] == []

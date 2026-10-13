import polars as pl
import pytest

from src.core.models import DataMap, Variable, VarType
from src.core.survey_definition import Expression, Question, SurveyDefinition
from src.engines.qc.impute import plan_imputation


def _dm(t=VarType.NUMERIC):
    return DataMap([Variable(name="A", type=t, question_ref="A")])


def _survey(routed):
    q = Question(qid="A", text="a")
    if routed:
        q.show_if = Expression(op="eq", qid="B", value=1)
    return SurveyDefinition(study_id="s", questions=[q])


def test_median_skips_missing():
    df = pl.DataFrame({"A": [1, 2, None, 100]})
    plan = plan_imputation(df, "A", "median", "missing", datamap=_dm(), survey=_survey(False))
    assert plan == [{"key": "row:2", "variable": "A", "old": None, "new": 2}]


def test_hot_deck_deterministic_and_class():
    df = pl.DataFrame({"A": [5, None, 7, None], "g": ["x", "y", "y", "x"]})
    kw = {"datamap": _dm(), "survey": _survey(False)}
    runs = [plan_imputation(df, "A", "hot_deck", "missing", "g", **kw) for _ in range(2)]
    assert runs[0] == runs[1]
    assert [(p["key"], p["new"]) for p in runs[0]] == [("row:3", 5)]  # row 1 has no y donor
    plain = plan_imputation(df, "A", "hot_deck", "missing", **kw)
    assert [(p["key"], p["new"]) for p in plain] == [("row:1", 5), ("row:3", 7)]


def test_out_of_range():
    df = pl.DataFrame({"A": [1, 2, 99, None]})
    plan = plan_imputation(df, "A", "hot_deck", "out_of_range", datamap=_dm(), max=10)
    assert [(p["key"], p["old"], p["new"]) for p in plan] == [("row:2", 99, 2)]


def test_refusals():
    df = pl.DataFrame({"A": [1, None]})
    with pytest.raises(ValueError, match="categorical"):
        plan_imputation(df, "A", "mean", "missing", datamap=_dm(VarType.SINGLE),
                        survey=_survey(False))
    with pytest.raises(ValueError, match="skip logic"):
        plan_imputation(df, "A", "median", "missing", datamap=_dm(), survey=_survey(True))
    with pytest.raises(ValueError, match="unavailable"):
        plan_imputation(df, "A", "median", "missing", datamap=_dm())

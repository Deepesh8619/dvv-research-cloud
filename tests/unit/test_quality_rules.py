from datetime import datetime

import polars as pl

from src.core.models import DataMap, Variable, VarType
from src.core.survey_definition import Question, QuestionType, SurveyDefinition
from src.engines.qc.rules import rules_from_survey, run_rules, validate_rules

DF = pl.DataFrame(
    {
        "trap": [3, 1, None],
        "fake": [1, 0, 0],
        "brand": [9, 2, 2],
        "age": [30, 30, 30],
        "yob": [1995, 1980, None],
        "q1": [5, 5, 1],
        "q2": [5, 1, 1],
        "vStartTime": [datetime(2026, 1, 1)] * 3,
    }
)
DM = DataMap([Variable(name=c, type=VarType.NUMERIC) for c in DF.columns])


def go(rule):
    assert validate_rules([rule], DM) == []
    return run_rules(DF, [rule], lambda i: i)


def test_trap():
    f = go({"kind": "trap", "variable": "trap", "pass_codes": [3]})
    f = go({"kind": "trap", "variable": "trap", "pass_codes": [1]})
    assert [(x["key"], x["check"]) for x in f] == [(0, "trap_failed")]  # null skipped


def test_red_herring():
    assert [x["key"] for x in go({"kind": "red_herring", "variables": ["fake"]})] == [0]
    r = go({"kind": "red_herring", "variable": "brand", "codes": [9]})
    assert [(x["key"], x["check"]) for x in r] == [(0, "red_herring")]


def test_flag_if():
    rule = {
        "kind": "flag_if",
        "why": "q1 and q2 both 5",
        "expr": {"op": "and", "children": [
            {"op": "eq", "qid": "q1", "value": 5}, {"op": "eq", "qid": "q2", "value": 5}]},
    }
    f = go(rule)
    assert [(x["key"], x["detail"]) for x in f] == [(0, "q1 and q2 both 5")]


def test_age_birth_year():
    f = go({"kind": "age_birth_year", "age": "age", "birth_year": "yob"})
    assert [x["key"] for x in f] == [1]  # 2026-1980 != 30; null row skipped


def test_range():
    f = go({"kind": "range", "variable": "q1", "min": 2, "max": 9})
    assert [(x["key"], x["check"]) for x in f] == [(2, "out_of_range")]


def test_no_rules_no_flags():
    assert run_rules(DF, [], lambda i: i) == []


def test_validate_rejects_missing_variable_and_kind():
    p = validate_rules(
        [{"kind": "range", "variable": "nope", "min": 0, "max": 1}, {"kind": "x"}], DM
    )
    assert len(p) == 2 and "nope" in p[0] and "unknown kind" in p[1]


def test_rules_from_survey():
    s = SurveyDefinition(study_id="s", questions=[
        Question(qid="q1", text="n", type=QuestionType.NUMERIC, validation={"min": 0, "max": 9}),
        Question(qid="q2", text="n", type=QuestionType.NUMERIC),
    ])
    assert rules_from_survey(s) == [{"kind": "range", "variable": "q1", "min": 0, "max": 9}]


def test_validate_rejects_missing_required_fields_and_run_skips():
    rules = [
        {"kind": "range", "variable": "q1"},
        {"kind": "trap", "variable": "trap"},
    ]
    p = validate_rules(rules, DM)
    assert "rule 0" in p[0] and "min or max" in p[0]
    assert "rule 1" in p[1] and "pass_codes" in p[1]
    assert run_rules(DF, rules, lambda i: i) == []

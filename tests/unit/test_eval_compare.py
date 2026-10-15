# ruff: noqa: E501
"""The comparison harness, on a tiny synthetic questionnaire (no client data)."""

import importlib.util
import json
import os
from pathlib import Path

import docx
import pytest

_spec = importlib.util.spec_from_file_location(
    "eval_compare", Path(__file__).resolve().parents[2] / "scripts" / "eval_compare.py"
)
_env = dict(os.environ)  # the module switches AI off at import; do not leak that into other tests
ec = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ec)
os.environ.clear()
os.environ.update(_env)


@pytest.fixture(autouse=True)
def _restore_env():
    before = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(before)

PARAS = [
    "S0. Are you the head of the household?", "1. Yes", "2. No [TERMINATE]",
    "S6. Which of these shops have you bought skincare from in the past 6 months? [ASK IF S0=1]",
    "1. Retailer A", "2. Retailer B", "3. Retailer C", "4. None of these [EXCLUSIVE]",
    "Q3. Where do you usually buy skincare? Select one.", "1. A national chain", "2. Online",
]


def _o(*labels):
    return [{"code": i + 1, "label": x} for i, x in enumerate(labels)]


def gold_doc(path: Path) -> dict:
    return {"doc_id": "tiny", "source_file": str(path), "questions": [
        {"qid": "S0", "text": "Are you the head of the household?", "type": "single",
         "options": _o("Yes", "No"), "terminate_if": [{"when": {"qid": "S0", "in": [2]}}]},
        {"qid": "S6", "type": "multi", "show_if": {"qid": "S0", "in": [1]},
         "text": "Which of these shops have you bought skincare from in the past 6 months?",
         "options": _o("Retailer A", "Retailer B", "Retailer C", "None of these")},
        {"qid": "Q3", "text": "Where do you usually buy skincare?", "type": "single",
         "options": _o("A national chain", "Online", "Other shop")},
        {"qid": "Q9", "text": "Not in the document at all", "type": "single", "options": _o("a", "b")},
    ], "defects": [{"qid": "Q9", "kind": "unknown_reference", "detail": "x"}]}


@pytest.fixture
def tiny(tmp_path):
    d = docx.Document()
    for p in PARAS:
        d.add_paragraph(p)
    path = tmp_path / "tiny.docx"
    d.save(path)
    return path, gold_doc(path)


def test_verdicts_per_layer(tiny):
    path, gold = tiny
    res = ec.run_document(path, gold)
    by = {i["qid"]: i for i in res["items"]}
    assert by["S0"]["layer1"]["verdict"] == "MATCH" and by["S0"]["layer2"]["verdict"] == "MATCH"
    assert by["Q9"]["layer1"]["verdict"] == "MISSING" and not by["Q9"]["flagged"]
    assert "3" in by["Q3"]["layer1"]["diffs"][0]["detail"]  # code 3 missing, named exactly
    assert by["Q3"]["layer2"]["verdict"] == "OPTION_DIFF"
    assert res["defects"][0]["result"] == "SILENT"
    assert res["scores"]["layer1"]["silent_wrong_pct"] > 0
    assert any(p["verdict"] == "MISSING" for p in res["patterns"])


def test_baseline_reports_regression_and_fix(tiny):
    path, gold = tiny
    res = ec.run_document(path, gold)
    base = json.loads(json.dumps(res, default=str))
    for it in base["items"]:  # pretend yesterday S0 was wrong and Q3 was right
        if it["qid"] == "S0":
            it["layer1"]["verdict"] = "OPTION_DIFF"
        if it["qid"] == "Q3":
            it["layer1"]["verdict"] = "MATCH"
    cmp = ec.diff_baseline(res, base)
    assert [r["key"] for r in cmp["regressions"]] == ["Q3#1"]
    assert [f["key"] for f in cmp["fixes"]] == ["S0#1"]


def test_logic_counterexample_is_concrete(tiny):
    path, gold = tiny
    res = ec.run_document(path, gold)
    s6 = next(i for i in res["items"] if i["qid"] == "S6")
    logic = [d for d in s6["layer1"]["diffs"] if d["verdict"] == "LOGIC_DIFF"]
    # the parser read [ASK IF S0=1] or it did not; either way the report is concrete
    assert not logic or "{S0:" in logic[0]["detail"]


def test_gold_eval_and_behaviour_agree_on_equal_conditions():
    e = {"op": "and", "args": [{"qid": "A", "in": [1, 2]}, {"qid": "B", "none_selected": [3]}]}
    assert ec.gold_eval(e, {"A": 1, "B": [1]}) and not ec.gold_eval(e, {"A": 1, "B": [3]})
    assert not ec.gold_eval(e, {"A": 5, "B": []})
    gold_by = {"A": {"type": "single", "options": _o("x", "y", "z")}}
    from src.core.survey_definition import Expression
    same = Expression(op="in", qid="A", values=[1, 2])
    other = Expression(op="in", qid="A", values=[1])
    g = {"qid": "A", "in": [1, 2]}
    assert ec.behaviour("show_if", "s", g, same, None, gold_by, {}, None)[0] == []
    diffs = ec.behaviour("show_if", "s", g, other, None, gold_by, {}, None)[0]
    assert diffs and "{A: 2}" in diffs[0]["detail"]


def test_matching_renamed_duplicates_and_fuzzy_text():
    from src.core.survey_definition import Question
    pq = [Question(qid="q1", text="a"), Question(qid="Q1_2", text="b"),
          Question(qid="Z9", text="How often do you use each of these products every week")]
    gold = [{"qid": "Q1", "text": "a"}, {"qid": "Q1", "text": "b"},
            {"qid": "Q7", "text": "How often do you use each of these products every week?"}]
    m = ec.match_questions(gold, pq)
    assert [m[i][0].qid for i in range(3)] == ["q1", "Q1_2", "Z9"]
    assert m[1][1] in {"renamed_dup", "text"} and m[2][1].startswith(("fuzzy", "text"))


def test_refuses_when_ai_is_not_off(monkeypatch):
    monkeypatch.setattr(ec, "llm_key_presence", lambda: {"GROQ_API_KEY": True})
    with pytest.raises(ec.Refuse):
        ec.assert_ai_off()


def test_refuses_output_inside_repo():
    with pytest.raises(SystemExit):
        ec.check_out(ec.REPO / "scratch_out")

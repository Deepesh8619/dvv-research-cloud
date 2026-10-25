"""Paths, transitions and exits recorded by the dry run (Logic X-ray data)."""

from __future__ import annotations

import pickle
import time

from src.core.survey_definition import (
    Block,
    Expression,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
)
from src.engines.fielding.dryrun import simulate


def _single(qid: str, **kw) -> Question:
    return Question(
        qid=qid, text=qid, type=QuestionType.SINGLE,
        options=[Option(code=1, label="a"), Option(code=2, label="b")], **kw,
    )


def _survey() -> SurveyDefinition:
    qs = [
        _single("Q1", terminate_if=Expression.from_dict({"op": "eq", "qid": "Q1", "value": 2})),
        _single("Q2"),
        _single("Q3", show_if=Expression.from_dict({"op": "eq", "qid": "Q2", "value": 1})),
    ]
    return SurveyDefinition(
        study_id="s", title="t", questions=qs,
        blocks=[Block(block_id="B1", question_ids=["Q1", "Q2", "Q3"])],
    )


def test_paths_match_a_hand_count() -> None:
    n = 400
    r = simulate(_survey(), n=n, seed=7)
    d = r.dispositions
    by = {(tuple(p["qids"]), p["disposition"]): p["n"] for p in r.paths}
    assert set(by) <= {
        (("Q1",), "terminate"), (("Q1", "Q2"), "complete"), (("Q1", "Q2", "Q3"), "complete"),
    }
    assert sum(by.values()) == n
    assert by[(("Q1",), "terminate")] == d["terminate"]
    assert sum(v for (q, _), v in by.items() if len(q) > 1) == d["complete"]

    t = {(x["from"], x["to"]): x["n"] for x in r.transitions}
    assert t[("Q1", "TERMINATE")] == d["terminate"]
    assert t[("Q1", "Q2")] == d["complete"]
    assert t[("Q2", "Q3")] + t[("Q2", "END")] == d["complete"]
    assert t[("Q3", "END")] == t[("Q2", "Q3")]
    assert [x["n"] for x in r.transitions] == sorted((x["n"] for x in r.transitions), reverse=True)

    assert r.exits == [{"qid": "Q1", "disposition": "terminate", "n": d["terminate"]}]
    assert r.to_dict()["paths"] == r.paths
    pickle.dumps(r)


def test_q3_is_on_a_path_exactly_when_q2_was_answered_1() -> None:
    """Hand count from each respondent's own answers: the paths must say who saw Q3."""
    r = simulate(_survey(), n=400, seed=11, collect=True)
    saw_q3 = sum(1 for answers, _ in r.respondents if answers.get("Q2") == 1)
    on_path = sum(p["n"] for p in r.paths if "Q3" in p["qids"])
    assert saw_q3 == on_path > 0
    assert sum(p["n"] for p in r.paths if p["qids"] == ["Q1", "Q2"]) == sum(
        1 for answers, _ in r.respondents if answers.get("Q2") == 2)


def test_thousand_respondents_under_a_second() -> None:
    start = time.perf_counter()
    simulate(_survey(), n=1000)
    assert time.perf_counter() - start < 1.0

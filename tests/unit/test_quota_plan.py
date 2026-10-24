"""The quota plan: shares of a total, checked, turned into link cells."""

from src.core.survey_definition import Block, Option, Question, QuestionType, SurveyDefinition
from src.engines.fielding.quota_plan import cells, problems

SURVEY = SurveyDefinition(
    study_id="s", title="T", source="authored", status="approved", version=1,
    blocks=[Block(block_id="B", question_ids=["G", "OE"])],
    questions=[Question(qid="G", text="Gender?", type=QuestionType.SINGLE,
                        options=[Option(code=1, label="Man"), Option(code=2, label="Woman")]),
               Question(qid="OE", text="Why?", type=QuestionType.OPEN_TEXT)])


def test_shares_become_rounded_up_limits():
    plan = {"completes": 301, "cells": [
        {"name": "Women", "qid": "G", "codes": [2], "share_pct": 50},
        {"name": "Men", "qid": "G", "codes": [1], "share_pct": 50}]}
    assert problems(plan, SURVEY) == []
    women, _men = cells(plan)
    assert women == {"name": "Women", "condition": {"op": "eq", "qid": "G", "value": 2},
                     "limit": 151}


def test_a_plan_that_cannot_work_says_why():
    plan = {"completes": 0, "cells": [
        {"name": "Women", "qid": "G", "codes": [2], "share_pct": 70},
        {"name": "Men", "qid": "G", "codes": [1], "share_pct": 40},
        {"name": "Why", "qid": "OE", "codes": [1], "share_pct": 10},
        {"name": "Ghost", "qid": "G", "codes": [9], "share_pct": 10}]}
    found = problems(plan, SURVEY)
    assert "Say how many completes the study needs." in found
    assert "Why: pick a single-choice question." in found
    assert "Ghost: pick answers of G." in found
    assert "G: shares add up to 120%, more than 100%." in found

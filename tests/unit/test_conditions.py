"""Conditions in plain words, compiled against the questionnaire or refused."""

import pytest

from src.core.survey_definition import Block, Option, Question, QuestionType, SurveyDefinition
from src.engines.questionnaire.conditions import compile_condition, describe_condition

SURVEY = SurveyDefinition(
    study_id="s", source="x", status="draft",
    blocks=[Block(block_id="B", question_ids=["QAGE", "S2", "S5c"])],
    questions=[
        Question(qid="QAGE", text="age", type=QuestionType.NUMERIC),
        Question(qid="S2", text="x",
                 options=[Option(code=1, label="Yes"), Option(code=2, label="No")]),
        Question(qid="S5c", text="x", options=[Option(code=1, label="DSL Internet"),
                                               Option(code=2, label="Fiber Internet")]),
    ])


@pytest.mark.parametrize("said, means", [
    ("QAGE < 18", "QAGE < 18"),
    ("terminate if qage less than 18", "QAGE < 18"),
    ("QAGE >= 65", "QAGE > 64"),
    ("QAGE between 18 and 34", "QAGE > 17 and QAGE < 35"),
    ("S2 = 1, 2", "S2 = 1, 2"),
    ("S5c is Fiber", "S5c = 2"),
    ("S2 != 1", "S2 is not 1"),
    ("S2 = 2 or QAGE < 18", "S2 = 2 or QAGE < 18"),
    ("S2 = 1 and QAGE between 18 and 34", "S2 = 1 and (QAGE > 17 and QAGE < 35)"),
])
def test_plain_words_compile(said, means):
    assert describe_condition(compile_condition(said, SURVEY)) == means


@pytest.mark.parametrize("said, why", [
    ("X9 = 1", "no question X9"),
    ("S2 = 5", "S2 has no answer coded 5; its codes are 1=Yes, 2=No"),
    ("S5c = Internet", "matches 2 answers"),
    ("QAGE < young", "needs a number"),
    ("whatever", "write it as QID op value"),
])
def test_what_does_not_compile_says_what_to_write(said, why):
    with pytest.raises(ValueError, match=why):
        compile_condition(said, SURVEY)

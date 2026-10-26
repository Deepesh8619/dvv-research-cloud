"""S9's screen-out, coded: rows of S8 and of S9 itself, read by grid row."""

from src.core.survey_definition import Block, Option, Question, QuestionType
from tests.integration.test_fielding_routes import (  # noqa: F401  (fixture)
    _make_questionnaire,
    _make_study,
    _sample_survey,
    client,
)


def test_a_screen_out_on_grid_rows_of_an_earlier_grid_and_its_own_saves(client):  # noqa: F811
    test_client, sf, _ = client
    _make_study(sf)
    survey = _sample_survey("study_f1")
    rows = [Option(code=1, label="Bar & Grill"), Option(code=2, label="Sports Bars")]
    cols = [Option(code=1, label="Past 30 days"), Option(code=6, label="Never")]
    for qid in ("S8", "S9"):
        survey.questions.append(Question(qid=qid, text=qid, type=QuestionType.GRID_SINGLE,
                                         rows=rows, cols=cols))
    survey.blocks.append(Block(block_id="B2", question_ids=["S8", "S9"]))
    _make_questionnaire(sf, survey=survey)
    never = [{"op": "eq", "qid": q, "row": r, "value": 6} for q in ("S8", "S9") for r in (1, 2)]
    res = test_client.patch("/api/studies/study_f1/questionnaire/questions/S9",
                            json={"terminate_if": {"op": "and", "children": never}})
    assert res.status_code == 200, res.text
    last = res.json()["terminate_if"]["children"][3]
    assert last == {"op": "eq", "qid": "S9", "row": 2, "value": 6}

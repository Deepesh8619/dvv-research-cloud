"""The logic map's proof route works on a draft questionnaire, before approval."""

from src.core.survey_definition import Expression, Option, Question, QuestionType
from tests.integration.test_fielding_routes import (  # noqa: F401  (fixture)
    _make_questionnaire,
    _make_study,
    _sample_survey,
    client,
)


def test_the_proof_covers_a_draft_and_names_an_unreachable_question(client):  # noqa: F811
    test_client, sf, _ = client
    _make_study(sf)
    survey = _sample_survey("study_f1")
    survey.status = "draft"
    survey.questions.append(Question(
        qid="Q9", text="Only for a code that does not exist", type=QuestionType.SINGLE,
        options=[Option(code=1, label="a")],
        show_if=Expression(op="eq", qid="Q2", value=7)))
    survey.blocks[0].question_ids.append("Q9")
    _make_questionnaire(sf, survey=survey)
    body = test_client.get("/api/studies/study_f1/questionnaire/logic").json()
    assert [u["qid"] for u in body["proof"]["unreachable"]] == ["Q9"]
    assert "examples" not in body["proof"]

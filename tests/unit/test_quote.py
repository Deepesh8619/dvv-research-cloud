"""A quote read from the questionnaire: length range, screeners, starts and cost."""

from src.core.survey_definition import (
    Expression,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
)
from src.engines.fielding.quote import price, question_seconds, quote


def _survey() -> SurveyDefinition:
    age = Question(qid="S1", type=QuestionType.NUMERIC, text="What is your age?",
                   terminate_if=Expression(op="lt", qid="S1", value=18,
                                           verbatim_text="TERMINATE IF S1 < 18"))
    grid = Question(qid="Q5", type=QuestionType.GRID_SINGLE, text="Rate each brand",
                    rows=[Option(i, f"Brand {i}") for i in range(1, 11)],
                    cols=[Option(i, str(i)) for i in range(1, 6)])
    why = Question(qid="Q6", type=QuestionType.OPEN_TEXT, text="Why?",
                   show_if=Expression(op="eq", qid="S1", value=30))
    hidden = Question(qid="HID_AGE", type=QuestionType.INFO, text="CODE AGE",
                      flags=["DERIVED_VARIABLE"], options=[Option(1, "18-34")])
    return SurveyDefinition(study_id="s", questions=[age, grid, why, hidden],
                            metadata={"study_overview": {"sample_size": 500, "loi_minutes": 1}})


def test_a_grid_takes_longer_than_a_single_question_and_hidden_variables_take_nothing():
    s = _survey()
    assert question_seconds(s.questions[1]) > question_seconds(s.questions[0])
    q = quote(s)
    assert q["n_questions"] == 3
    assert q["length"]["low"] < q["length"]["minutes"] < q["length"]["high"]
    assert q["length"]["longest"][0]["qid"] == "Q5"
    assert q["length"]["stated_minutes"] == 1


def test_screeners_are_listed_in_the_order_respondents_meet_them():
    assert [s["qid"] for s in quote(_survey())["screeners"]] == ["S1"]


def test_cost_needs_incidence_and_uses_only_the_rates_given():
    assert quote(_survey())["cost"] is None  # no incidence, no guess
    c = price(10.0, 500, 25, None)
    assert c["starts_needed"] == 2000 and "cost_per_complete" not in c
    c = price(10.0, 500, 25, rate_per_minute=0.2, min_cpi=5, project_fee=1000)
    assert c["cost_per_complete"] == 8.0  # 0.2 x 10 min / 0.25 incidence
    assert c["total"] == 500 * 8.0 + 1000

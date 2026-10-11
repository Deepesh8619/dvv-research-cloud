"""One question, fixed with an agent, in front of the analyst."""

from src.ai.router import AIResponse, BackendUnavailable
from src.core.survey_definition import Option, Question, QuestionType
from src.engines.questionnaire import question_agent as qa
from src.engines.questionnaire.qa_loop import qa_check

_SUM_SOURCE = "\n".join([
    "[BASE: ALL] What proportion fall into each category? Must sum to 100%. "
    "[0-100, MUST SUM TO 100%]",
    " | % | ",
    "Talk therapy alone |  | ",
    "1st or 2nd oral antidepressant trial |  | ",
    "Other (specify) |  | ",
])


def _stored_sum_question():
    """TRD2 Q2 as an older reader stored it: one answer, separators on screen."""
    return Question(
        qid="Q2", type=QuestionType.SINGLE,
        text="What proportion fall into each category? Must sum to 100%.",
        options=[Option(2, "Talk therapy alone |  |"),
                 Option(3, "1st or 2nd oral antidepressant trial |  |"),
                 Option(4, "Other (specify) |  |")],
        source_text=_SUM_SOURCE, extraction_method="prose_directives",
    )


def test_qa_notices_a_sum_the_survey_does_not_apply():
    q = _stored_sum_question()
    assert "sum_not_applied" in [i.check for i in qa_check(q, {"Q2": q})]


def test_the_right_fix_comes_first_and_leaves_qa_nothing():
    q = _stored_sum_question()
    best = qa.suggest_repairs(q, {"Q2": q})[0]
    assert best["question"]["type"] == "numeric"
    assert best["question"]["validation"]["sum"] == 100
    assert best["qa_after"] == []
    assert q.type is QuestionType.SINGLE  # a suggestion changes nothing


def test_a_question_that_swallowed_the_next_ones_sum_table_is_not_turned_into_boxes():
    q = Question(qid="Q7", type=QuestionType.SINGLE,
                 text="How do you approach the referral discussion? Select one.",
                 options=[Option(1, "I recommend"), Option(2, "I defer")],
                 source_text="Q7. How do you approach the referral discussion? Select one.\n"
                             "I recommend\nI defer\n" + _SUM_SOURCE)
    assert all(s["question"]["type"] != "numeric" for s in qa.suggest_repairs(q, {"Q7": q}))


def _model(content):
    def run(task, prompt, schema, **kwargs):
        assert task == "questionnaire.revise"
        return AIResponse(content=content, backend="test", model="m")
    return run


def test_the_agent_follows_the_analysts_words_but_cannot_invent_answers(monkeypatch):
    monkeypatch.setattr(qa, "run_task", _model({
        "type": "numeric",
        "rows": [{"code": 1, "label": "Talk therapy alone"},
                 {"code": 2, "label": "Ketamine infusion"}],
        "validation": {"sum": 100},
        "explanation": "Made it one number per row adding to 100.",
    }))
    q = _stored_sum_question()
    out = qa.revise(q, "these must add up to 100", {"Q2": q})
    assert [r["label"] for r in out["question"]["rows"]] == ["Talk therapy alone"]
    assert out["dropped"] == ["Ketamine infusion"]
    assert out["source"] == "agent (test/m)"


def test_an_unreachable_agent_says_so(monkeypatch):
    def down(*a, **k):
        raise BackendUnavailable("no backend")
    monkeypatch.setattr(qa, "run_task", down)
    q = _stored_sum_question()
    assert "No AI model could be reached" in qa.revise(q, "fix it", {"Q2": q})["error"]


def test_a_made_up_type_is_refused(monkeypatch):
    monkeypatch.setattr(qa, "run_task", _model({"type": "slider", "explanation": "x"}))
    q = _stored_sum_question()
    assert "does not exist" in qa.revise(q, "make it a slider", {"Q2": q})["error"]

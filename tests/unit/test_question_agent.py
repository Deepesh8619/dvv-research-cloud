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
    monkeypatch.setattr(qa, "run_task", _model({"type": "hologram", "explanation": "x"}))
    q = _stored_sum_question()
    assert "does not exist" in qa.revise(q, "make it a hologram", {"Q2": q})["error"]


def test_a_slider_is_kept_on_record_as_a_slider(monkeypatch):
    monkeypatch.setattr(qa, "run_task", _model({"type": "slider", "explanation": "x"}))
    q = _stored_sum_question()
    out = qa.revise(q, "make it a slider", {"Q2": q})
    assert out["question"]["validation"]["intended_type"] == "slider"


# --- Using a named question's answers; not losing rows; flat limits -----------

def _q2_and_q4():
    q2 = Question(qid="Q2", type=QuestionType.NUMERIC, text="What share came from each place?",
                  rows=[Option(1, "Home"), Option(2, "Work"), Option(3, "Cafe")],
                  validation={"sum": 100})
    q4 = Question(qid="Q4", type=QuestionType.OPEN_TEXT,
                  text="Of the [INSERT Q2 RESPONSE] cups, what percent had milk?",
                  source_text="Of the [INSERT Q2 RESPONSE] cups, what percent had milk? ____%",
                  validation={"techniques": {"pipes": [{"source_qid": "Q2"}]}})
    return q2, q4


def test_the_agent_may_reuse_the_answers_of_a_question_the_analyst_names(monkeypatch):
    """"Show Q2's categories here" is a request, not an invention."""
    q2, q4 = _q2_and_q4()
    seen = {}

    def model(task, prompt, schema, **kwargs):
        seen["prompt"] = prompt
        return AIResponse(content={
            "type": "numeric", "explanation": "One box per Q2 category.",
            "rows": [{"code": 1, "label": "Home"}, {"code": 2, "label": "Work"},
                     {"code": 3, "label": "Cafe"}],
            "total": 100, "box_min": 0, "box_max": 100,
        }, backend="test", model="m")

    monkeypatch.setattr(qa, "run_task", model)
    out = qa.revise(q4, "Show each category from Q2 as a number box adding up to 100.",
                    {"Q2": q2, "Q4": q4})
    assert "rows: 1=Home; 2=Work; 3=Cafe" in seen["prompt"]
    assert [r["label"] for r in out["question"]["rows"]] == ["Home", "Work", "Cafe"]
    assert out["dropped"] == []
    assert {k: out["question"]["validation"][k] for k in ("sum", "min", "max")} == {
        "sum": 100, "min": 0, "max": 100}


def test_rows_the_analyst_did_not_ask_to_remove_are_kept(monkeypatch):
    """Told to keep the rows, the model returned one of two; the other vanished."""
    q2, q4 = _q2_and_q4()
    q4.type, q4.rows = QuestionType.NUMERIC, [Option(1, "Home"), Option(2, "Work")]
    monkeypatch.setattr(qa, "run_task", _model({
        "type": "numeric", "text": "What percent had milk?", "explanation": "Reworded.",
        "rows": [{"code": 1, "label": "Home"}],
    }))
    out = qa.revise(q4, "Reword the question; keep the rows.", {"Q2": q2, "Q4": q4})
    assert [r["label"] for r in out["question"]["rows"]] == ["Home", "Work"]
    assert "kept all 2 rows" in out["explanation"]


def test_a_malformed_answer_is_reported_as_such_not_as_no_ai(monkeypatch):
    from src.ai.router import ModelOutputError

    def bad(*a, **k):
        raise ModelOutputError("the model's answer did not fit")
    monkeypatch.setattr(qa, "run_task", bad)
    q2, q4 = _q2_and_q4()
    assert "answered, but not in a form" in qa.revise(q4, "fix it", {"Q2": q2, "Q4": q4})["error"]

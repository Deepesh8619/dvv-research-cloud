"""Problems between questions: exact checks in QA, an AI second opinion on top.

Measured on a synthetic survey with seven planted errors and a clean copy
(qwen3.8-27b): exact checks found 5/7 with no false alarms; the reviewer found
the other 2. These tests pin the parts that must not drift.
"""

from src.ai.router import AIResponse, BackendUnavailable
from src.core.survey_definition import Expression as E
from src.core.survey_definition import Option as O
from src.core.survey_definition import Question as Q
from src.core.survey_definition import QuestionType as T
from src.core.survey_definition import SurveyDefinition
from src.engines.questionnaire import logic_review
from src.engines.questionnaire.qa_loop import qa_check, run_qa


def yn():
    return [O(1, "Yes"), O(2, "No")]


def checks(survey, qid):
    by = {q.qid: q for q in survey.questions}
    return [i.check for i in qa_check(by[qid], by)]


def test_a_condition_on_a_later_question_is_caught_exactly():
    s = SurveyDefinition(study_id="s", questions=[
        Q("Q13", "How do you brew?", T.SINGLE, options=yn(), show_if=E("eq", "Q14", 1)),
        Q("Q14", "Do you own a machine?", T.SINGLE, options=yn()),
    ])
    assert "condition_on_later_question" in checks(s, "Q13")


def test_a_condition_on_an_answer_that_does_not_exist_is_caught_exactly():
    s = SurveyDefinition(study_id="s", questions=[
        Q("Q7", "How satisfied?", T.SINGLE, options=[O(1, "Yes"), O(2, "Neutral"), O(3, "No")]),
        Q("Q8", "Why?", T.OPEN_TEXT, show_if=E("eq", "Q7", 5)),
    ])
    assert "condition_on_missing_answer" in checks(s, "Q8")
    s.questions[1].show_if = E("eq", "Q7", 3)
    assert "condition_on_missing_answer" not in checks(s, "Q8")


def test_pipes_from_later_or_possibly_unasked_questions():
    pipe = {"techniques": {"pipes": [{"source_qid": "Q13"}]}}
    later = SurveyDefinition(study_id="s", questions=[
        Q("Q6", "Why [INSERT Q13 RESPONSE]?", T.OPEN_TEXT, validation=pipe),
        Q("Q13", "Which?", T.SINGLE, options=yn()),
    ])
    assert "pipe_from_later_question" in checks(later, "Q6")

    gated = E("in", "Q11", values=[1, 2])
    maybe = SurveyDefinition(study_id="s", questions=[
        Q("Q11", "How often?", T.SINGLE, options=yn()),
        Q("Q13", "Which?", T.SINGLE, options=yn(), show_if=gated),
        Q("Q17", "You said [INSERT Q13 RESPONSE]...", T.OPEN_TEXT, validation=pipe),
    ])
    assert "pipe_may_be_missing" in checks(maybe, "Q17")
    maybe.questions[2].show_if = E("in", "Q11", values=[1, 2])  # same people
    assert "pipe_may_be_missing" not in checks(maybe, "Q17")


def _survey():
    return SurveyDefinition(study_id="s", questions=[
        Q("S1", "Do you drink coffee?", T.SINGLE, options=yn(), terminate_if=E("eq", "S1", 2)),
        Q("Q10", "Do you drink decaf?", T.SINGLE, options=yn(), show_if=E("eq", "S1", 2)),
        Q("Q11", "How often do you buy beans?", T.SINGLE, options=yn()),
    ])


def _model(findings):
    def run(task, prompt, schema, **kwargs):
        assert task == "questionnaire.logic_review"
        return AIResponse(content={"findings": findings}, backend="test", model="m")
    return run


def test_the_outline_carries_what_connects_questions():
    text = logic_review.outline(_survey())
    assert 'asked if: S1 eq 2 "No"' in text and 'screens out if: S1 eq 2 "No"' in text


def test_findings_must_quote_the_outline_and_name_real_questions(monkeypatch):
    monkeypatch.setattr(logic_review, "run_task", _model([
        {"qids": ["Q10", "S1"], "severity": "error",
         "problem": "Q10 is only for people S1 screens out.",
         "evidence": 'Q10 [single 2 answers] "Do you drink decaf?" ... asked if: S1 eq 2 "No"'},
        {"qids": ["Q99"], "severity": "error", "problem": "invented",
         "evidence": "Q99 asks about tea"},
        {"qids": ["Q11"], "severity": "warning", "problem": "made-up quote",
         "evidence": "Q11 asks how many cups per day at the office"},
    ]))
    result = logic_review.review(_survey())
    assert [f["qids"] for f in result["findings"]] == [["Q10", "S1"]]
    assert result["claims_dropped_unsupported"] == 2


def test_what_qa_already_reports_is_not_repeated(monkeypatch):
    survey = _survey()
    survey.questions[2].show_if = E("eq", "S1", 7)  # QA: an answer S1 does not offer
    run_qa(survey)
    monkeypatch.setattr(logic_review, "run_task", _model([
        {"qids": ["Q11"], "severity": "error", "problem": "condition can never be met",
         "evidence": "Q11 [single 2 answers]"},
    ]))
    result = logic_review.review(survey)
    assert result["findings"] == [] and result["already_reported_by_qa"] == 1


def test_an_unreachable_model_leaves_no_findings_and_says_so(monkeypatch):
    def down(*a, **k):
        raise BackendUnavailable("no backend")
    monkeypatch.setattr(logic_review, "run_task", down)
    result = logic_review.review(_survey())
    assert result["findings"] == [] and "unavailable" in result

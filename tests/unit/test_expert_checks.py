"""Expert checks: each rule fires on the fault and stays quiet on good wording."""

from src.core.survey_definition import Block, Option, Question, QuestionType, SurveyDefinition
from src.engines.questionnaire.expert_checks import check, check_question
from src.engines.questionnaire.library import TEMPLATES, build_template


def _opts(*labels):
    return [Option(code=i, label=x) for i, x in enumerate(labels, 1)]


def test_each_fault_is_named():
    leading = Question(qid="A", text="Don't you agree our service is great?",
                       type=QuestionType.SINGLE, options=_opts("Yes", "No"))
    assert check_question(leading)[0]["weight"] == "fix"
    two = Question(qid="B", text="How satisfied are you with the price and quality?",
                   type=QuestionType.SINGLE, options=_opts("Satisfied", "Dissatisfied"))
    assert "two things at once" in check_question(two)[0]["note"]
    multi = Question(qid="C", text="Which do you use?", type=QuestionType.MULTI,
                     options=_opts("X", "Y"))
    assert "None of these" in check_question(multi)[0]["note"]
    lean = Question(qid="D", text="How was it?", type=QuestionType.SINGLE,
                    options=_opts("Excellent", "Very good", "Good", "Poor"))
    assert check_question(lean)[0]["note"].startswith("The scale leans one way (3 positive")
    long_list = Question(qid="E", text="Which brand?", type=QuestionType.SINGLE,
                         options=_opts(*[f"Brand {c}" for c in "ABCDEFGHI"]))
    assert "fixed order" in check_question(long_list)[0]["note"]


def test_good_wording_and_every_template_are_quiet():
    fine = Question(qid="F", text="How satisfied or dissatisfied are you overall?",
                    type=QuestionType.SINGLE,
                    options=_opts("Very dissatisfied", "Somewhat dissatisfied",
                                  "Neither satisfied nor dissatisfied", "Somewhat satisfied",
                                  "Very satisfied"))
    assert check_question(fine) == []
    inputs = {"organisation": "D", "purpose": "p", "contact": "c@x.in", "category": "coffee",
              "brands": "A\nB", "subject": "A", "attributes": "Taste", "concept": "c",
              "product": "p", "currency": "₹", "items": "\n".join(f"I{i}" for i in range(8)),
              "question": "Why?", "specialties": "GP"}
    for t in TEMPLATES:
        qs = build_template(t, inputs, set())
        s = SurveyDefinition(study_id="s", title="T", source="authored", status="draft", version=1,
                             blocks=[Block(block_id="B", question_ids=[q.qid for q in qs])],
                             questions=qs)
        assert check(s)["notes"] == [], t

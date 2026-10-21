"""Adverse events in open ends, and the notice respondents agree to."""

from src.core.survey_definition import (
    Block,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
    evaluate,
)
from src.engines.qc.compliance import check_notice, classify, is_consent, notice_questions


def test_an_event_someone_had_is_possible_and_an_opinion_is_a_mention_or_nothing():
    assert classify("My patient developed a severe rash after the second dose.")[0] == "possible"
    assert classify("One patient was hospitalised for a week.")[0] == "possible"
    assert classify("I had headaches since starting it")[0] == "possible"
    assert classify("It did not work for him at all")[0] == "possible"
    # Real verbatims from a physician study: opinions, not events.
    opinion = "Expand on visual hallucinations and whether that is a Normal side effect."
    assert classify(opinion)[0] == "mention"
    assert classify("We need hard data on risk factors for adverse reactions")[0] == "mention"
    assert classify("efficacy and safety") is None
    # Denied: nobody had it.
    assert classify("Patients had no side effects at all.")[0] == "mention"
    # The event sentence is the one returned, not the whole answer.
    hit = classify("Good product overall. My client got dizzy after taking it.")
    assert hit[0] == "possible" and hit[2] == "My client got dizzy after taking it."


def _survey(*questions: Question) -> SurveyDefinition:
    qs = list(questions)
    return SurveyDefinition(study_id="s", title="T", source="authored", status="draft", version=1,
                            blocks=[Block(block_id="B1", question_ids=[q.qid for q in qs])],
                            questions=qs)


def test_a_physician_questionnaire_without_a_notice_misses_every_item():
    survey = _survey(
        Question(qid="S1", text="How many patients with TRD do you treat each month?",
                 type=QuestionType.NUMERIC),
        Question(qid="S2", text="Which medications do you prescribe? Adverse effect profile: "
                 "most common TEAEs reported were headache", type=QuestionType.OPEN_TEXT),
    )
    check = check_notice(survey)
    assert check["health_study"] and not check["complete"]
    assert {i["item"] for i in check["items"] if not i["present"]} == {
        "consent", "purpose", "data", "withdraw", "grievance", "board", "adverse_events"}


def test_the_written_notice_satisfies_its_own_check_and_a_no_terminates():
    added = notice_questions("DVV Research", "understanding depression treatment",
                             "privacy@dvv.example", health=True)
    survey = _survey(*added, Question(qid="S1", text="Which patients do you treat with this drug "
                                      "or therapy?", type=QuestionType.OPEN_TEXT))
    check = check_notice(survey)
    assert check["complete"], [i for i in check["items"] if not i["present"]]
    consent = added[1]
    assert is_consent(consent) and check["consent_questions"] == ["CONSENT"]
    assert evaluate(consent.terminate_if, {"CONSENT": 2})
    assert not evaluate(consent.terminate_if, {"CONSENT": 1})
    # A non-health study gets no adverse-event paragraph.
    plain = notice_questions("DVV", "ad research", "a@b.co", health=False)[0].text
    assert "pharmacovigilance" not in plain


def test_consent_in_its_own_words_counts_without_a_mark():
    q = Question(qid="C", text="Do you agree to participate in this study?",
                 type=QuestionType.SINGLE, options=[Option(code=1, label="Yes")])
    assert is_consent(q)

"""Pipes the link can fill, and bases that point at "the question above".

All from the TRD referrer study:

    Q3  Are you familiar with "TRD"?  Yes / No
    Q4  [BASE: ANSWERED YES ABOVE] Of the [INSERT Q2 RESPONSE] MDD patients...
    Q5  [BASE: ANSWERED NO ABOVE] For the remainder of this survey, TRD is...
        Of the [INSERT Q2 RESPONSE] MDD patients...   <- Q4 again

Q2 is a sum-to-100 question: eight numbers, not one.
"""

from src.core.survey_definition import Option, Question, QuestionType, SurveyDefinition
from src.engines.questionnaire import schema
from src.engines.questionnaire.qa_loop import qa_check
from src.engines.questionnaire.relative_bases import resolve_relative_bases
from src.renderers.surveyjs_compiler import _compile_question

_Q4_TEXT = ("Of the [INSERT Q2 RESPONSE] MDD patients seen in the past month, what percent "
            "would you classify as having TRD? Fill in.")


def _study():
    q2 = Question(qid="Q2", type=QuestionType.NUMERIC, text="What proportion fall into each?",
                  rows=[Option(1, "Talk therapy alone"), Option(2, "ECT")],
                  validation={"sum": 100})
    q3 = Question(qid="Q3", type=QuestionType.SINGLE, text="Are you familiar with TRD?",
                  options=[Option(1, "Yes"), Option(2, "No")])
    q4 = Question(qid="Q4", type=QuestionType.NUMERIC, text=_Q4_TEXT,
                  source_text=f"[BASE: ANSWERED YES ABOVE] {_Q4_TEXT}",
                  validation={"techniques": {
                      "pipes": [{"source_qid": "Q2", "verbatim": "INSERT Q2"}]}})
    q5 = Question(qid="Q5", type=QuestionType.NUMERIC,
                  text="For the remainder of this survey, TRD is defined as patients with MDD.",
                  source_text="[BASE: ANSWERED NO ABOVE] For the remainder of this survey, TRD is "
                              f"defined as patients with MDD.\n{_Q4_TEXT} [0-100 RESPONSE]")
    return SurveyDefinition(study_id="s", questions=[q2, q3, q4, q5])


def test_answered_yes_above_means_the_nearest_question_offering_yes():
    survey = _study()
    assert resolve_relative_bases(survey) == ["Q4", "Q5"]
    q4, q5 = survey.questions[2], survey.questions[3]
    assert (q4.show_if.qid, q4.show_if.value) == ("Q3", 1)
    assert (q5.show_if.qid, q5.show_if.value) == ("Q3", 2)  # skips Q4, which offers no "No"


def test_a_pointing_base_that_cannot_be_resolved_is_flagged_not_dropped():
    survey = _study()
    survey.questions[1].options = [Option(1, "Aware"), Option(2, "Not aware")]
    resolve_relative_bases(survey)
    q4 = survey.questions[2]
    assert q4.show_if is None
    assert schema.FLAG_AMBIGUOUS_ROUTING in q4.flags
    assert "ANSWERED YES ABOVE" in q4.validation["routing_verbatim"]


def test_piping_a_whole_sum_question_is_flagged_because_the_link_prints_object_object():
    survey = _study()
    by = {q.qid: q for q in survey.questions}
    found = {i.check: i for i in qa_check(by["Q4"], by)}
    assert "pipe_cannot_fill" in found and "[object Object]" in found["pipe_cannot_fill"].detail


def test_one_named_box_of_a_sum_question_can_be_piped():
    survey = _study()
    by = {q.qid: q for q in survey.questions}
    q4 = by["Q4"]
    q4.text = "Of your [INSERT Q2 RESPONSE FOR ECT] ECT patients, what share returned?"
    assert "{Q2.2}" in _compile_question(q4, by)["title"]
    assert "pipe_cannot_fill" not in [i.check for i in qa_check(q4, by)]


def test_a_second_copy_of_a_question_inside_another_is_flagged():
    survey = _study()
    by = {q.qid: q for q in survey.questions}
    found = {i.check: i for i in qa_check(by["Q5"], by)}
    assert found["repeats_another_question"].evidence == "Q4"


def test_questions_that_share_a_stem_are_not_repeats():
    """S9/S10 and Q33/Q33a in Vivo share "Which of the following best describes you?" —
    a false alarm on the real document when this check was first written."""
    s9 = Question(qid="S9", type=QuestionType.MULTI,
                  options=[Option(1, "A"), Option(2, "B")],
                  text="Which of the following best describes you? Select all that apply.",
                  source_text="S9. Which of the following best describes you? "
                              "Select all that apply.")
    s10 = Question(qid="S10", type=QuestionType.SINGLE, options=[Option(1, "A"), Option(2, "B")],
                   text="Which of the following best describes you and your household now?")
    by = {"S9": s9, "S10": s10}
    assert "repeats_another_question" not in [i.check for i in qa_check(s9, by)]


def test_a_base_naming_codes_of_the_previous_question_is_applied():
    from src.core.survey_definition import Option, Question, QuestionType, SurveyDefinition
    from src.engines.questionnaire.relative_bases import resolve_relative_bases

    familiar = Question(qid="Q9", text="How familiar?", type=QuestionType.SINGLE,
                        options=[Option(code=c, label=f"L{c}") for c in range(1, 6)])
    follow = Question(qid="Q10", text="Perception?", type=QuestionType.MULTI,
                      source_text="[BASE SELECTS 1-4 IN PREVIOUS QUESTION] Which best ...",
                      options=[Option(code=1, label="Good")])
    odd = Question(qid="Q11", text="Why?", type=QuestionType.OPEN_TEXT,
                   source_text="[BASE: CODES 7 OR 8 ABOVE] Why?")
    survey = SurveyDefinition(study_id="s", questions=[familiar, follow, odd])
    assert resolve_relative_bases(survey) == ["Q10"]
    assert (follow.show_if.qid, follow.show_if.values) == ("Q9", [1, 2, 3, 4])
    # Q10 offers no 7 or 8: left in words for a person, never guessed.
    assert odd.show_if is None and "AMBIGUOUS_ROUTING" in odd.flags


def test_multiple_selected_and_any_row_bases():
    from src.core.survey_definition import (
        Option,
        Question,
        QuestionType,
        SurveyDefinition,
        evaluate,
    )
    from src.engines.questionnaire.relative_bases import resolve_counted_bases

    s6 = Question(qid="S6", text="Insurance?", type=QuestionType.MULTI,
                  options=[Option(code=c, label=f"I{c}") for c in (1, 2, 3)]
                  + [Option(code=99, label="Don't know", exclusive=True)])
    s7 = Question(qid="S7", text="Primary?", type=QuestionType.SINGLE,
                  source_text="S7.[ASK IF MULTIPLE AT S6 SELECTED] Which is your primary?")
    tool = Question(qid="GA_TOOL", text="Use?", type=QuestionType.GRID_SINGLE,
                    rows=[Option(code=1, label="A"), Option(code=2, label="B")],
                    cols=[Option(code=c, label=str(c)) for c in range(1, 6)])
    trust = Question(qid="GA_TRUST", text="Trust?", type=QuestionType.GRID_SINGLE,
                     rows=[Option(code=1, label="A"), Option(code=2, label="B")],
                     extraction_evidence={
                         "base": "Among who: Ask if GA_TOOL = 3-5 for at least one option. "
                                 "Only show options familiar with from GA_TOOL"})
    s = SurveyDefinition(study_id="s", questions=[s6, s7, tool, trust])
    assert resolve_counted_bases(s) == ["S7", "GA_TRUST"]
    assert evaluate(s7.show_if, {"S6": [1, 3]}) and not evaluate(s7.show_if, {"S6": [2]})
    assert evaluate(trust.show_if, {"GA_TOOL": {1: 1, 2: 4}})
    assert not evaluate(trust.show_if, {"GA_TOOL": {1: 1, 2: 2}})
    # Each row only where the same row was rated 3-5.
    assert evaluate(trust.rows[1].show_if, {"GA_TOOL": {2: 4}})
    assert not evaluate(trust.rows[0].show_if, {"GA_TOOL": {1: 1}})

"""DA_SHARE: "[Numeric OE]%" over a response list, wording says it adds up to 100%.

Read as an answer list it was exclusive options with no total and no flag. It
is one number box per line, and the respondent link must enforce the total.
"""

from src.core.survey_definition import Option, Question, QuestionType
from src.engines.questionnaire import constant_sum
from src.engines.questionnaire.qa_loop import qa_check
from src.renderers.surveyjs_compiler import _compile_question

_SOURCE = (
    "Q Name: DA_SHARE\nWhat percentage of your budget do you spend on each of the "
    "providers below? The percentages you use must add up to 100%.\n"
    "Response List:\n[Numeric OE: 1-digit minimum]%\n1: Google Ads\n2: Meta Ads\n"
    "98: Other Digital Ad Platforms:\n"
)


def _q(source=_SOURCE, text=None):
    return Question(
        qid="DA_SHARE", type=QuestionType.NUMERIC, source_text=source,
        text=text or "What percentage of your budget do you spend on each of the "
        "providers below? The percentages you use must add up to 100%.",
        options=[Option(1, "Google Ads"), Option(2, "Meta Ads"),
                 Option(98, "Other Digital Ad Platforms:", exclusive=True, anchored=True)],
    )


def test_numeric_oe_list_with_a_stated_total_becomes_boxes_that_sum_to_it():
    q = _q()
    survey = type("S", (), {"questions": [q], "metadata": {}})()
    assert constant_sum.apply(survey) == ["DA_SHARE"]
    assert [r.code for r in q.rows] == [1, 2, 98] and q.options == []
    assert q.validation["sum"] == 100
    # the respondent link enforces it
    rule = _compile_question(q, {"DA_SHARE": q})["validators"][0]["expression"]
    assert rule == "{DA_SHARE.1} + {DA_SHARE.2} + {DA_SHARE.98} = 100"


def test_a_stated_total_other_than_100_is_honoured():
    q = _q(text="Split your 40 points. The numbers must add up to 40.")
    assert constant_sum.numeric_list_sum(q) == (40, False)


def test_options_with_a_percent_in_the_wording_stay_unchanged():
    q = Question(
        qid="Q5", type=QuestionType.SINGLE, text="Is your discount 20% or more?",
        source_text="Q5. Is your discount 20% or more?\n1: Yes\n2: No\n",
        options=[Option(1, "Yes"), Option(2, "No")],
    )
    survey = type("S", (), {"questions": [q], "metadata": {}})()
    assert constant_sum.apply(survey) == []
    assert q.type is QuestionType.SINGLE and not q.rows and len(q.options) == 2


def test_numeric_oe_list_without_a_stated_total_stays_unchanged():
    q = _q(text="What percentage do you spend on each?")
    assert constant_sum.numeric_list_sum(q) is None


def test_qa_flags_a_constant_sum_that_has_no_total():
    q = _q()
    q.rows, q.options = q.options, []
    assert "sum_total_missing" in [i.check for i in qa_check(q, {"DA_SHARE": q})]
    q.validation = {"sum": 100}
    assert "sum_total_missing" not in [i.check for i in qa_check(q, {"DA_SHARE": q})]

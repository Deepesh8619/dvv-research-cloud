"""A constant sum written as a table, the way the TRD questionnaire wrote it.

    [0-100, MUST SUM TO 100%]
     | % |
    Talk therapy alone |  |

Read as anything else, the respondent saw the wording and nothing to fill in.
"""

import pytest

from src.core.survey_definition import QuestionType
from src.engines.questionnaire import constant_sum
from src.engines.questionnaire import extractor as ex
from src.intake.questionnaire_loader import DocumentBlock, segment_into_questions

_TABLE_TEXT = (
    "[BASE: ALL] What proportion fall into each category? Must sum to 100%. "
    "[0-100, MUST SUM TO 100%]\n"
    " | % | \n"
    "Talk therapy alone |  | \n"
    "1st or 2nd oral antidepressant trial |  | \n"
    "Other (specify) |  | "
)
_ITEMS = ["Talk therapy alone", "1st or 2nd oral antidepressant trial", "Other (specify)"]


def test_a_table_of_empty_boxes_with_a_stated_sum_is_a_constant_sum():
    assert constant_sum.read(_TABLE_TEXT) == (_ITEMS, 100, False)


def test_an_empty_table_without_a_stated_sum_is_not_one():
    """Grids are empty tables too; only the sum makes this a constant sum."""
    plain = _TABLE_TEXT.replace("Must sum to 100%. ", "").replace("[0-100, MUST SUM TO 100%]", "")
    assert constant_sum.read(plain) is None


def test_the_table_is_read_without_asking_a_model(monkeypatch):
    monkeypatch.setattr(ex, "run_task", lambda *a, **k: pytest.fail("asked a model"))
    rows = [["", "%", ""]] + [[item, "", ""] for item in _ITEMS]
    blocks = [
        DocumentBlock("paragraph", "Q2. What proportion fall into each category? "
                      "Must sum to 100%. [0-100, MUST SUM TO 100%]", 0),
        DocumentBlock("table", "\n".join(" | ".join(r) for r in rows), 1, rows=rows),
    ]
    chunk = next(c for c in segment_into_questions(blocks) if c.candidate_qid == "Q2")
    q = ex.extract_question(chunk).question
    assert q.type is QuestionType.NUMERIC
    assert [r.label for r in q.rows] == _ITEMS
    assert q.validation["sum"] == 100


def test_at_least_a_total_is_a_floor_not_an_exact_sum():
    """A patient can receive two treatments, so the shares total at least 100."""
    from src.core.survey_definition import Question
    from src.renderers.surveyjs_compiler import _compile_question

    text = _TABLE_TEXT.replace("MUST SUM TO 100%", "MUST ADD TO AT LEAST 100 PERCENT")
    text = text.replace("Must sum to 100%. ", "")
    items, total, at_least = constant_sum.read(text)
    assert (total, at_least) == (100, True)

    q = Question(qid="Q9", text="What percentage received each?", type=QuestionType.SINGLE)
    constant_sum.make_constant_sum(q, items, total, at_least)
    assert q.validation["sum_min"] == 100 and "sum" not in q.validation
    rule = _compile_question(q, {"Q9": q})["validators"][0]["expression"]
    assert rule.endswith(">= 100")

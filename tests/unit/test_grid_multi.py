"""Multi-answer grids: "for each type of pharmacy… select all that apply".

Vivo Q14 and Q21 were read as one answer per row, so a respondent could tick
only one concern per pharmacy type and the rest were never collected.
"""

import random
from types import SimpleNamespace

import polars as pl

from src.core.models import DataMap
from src.core.survey_definition import (
    Block,
    Expression,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
    evaluate,
)
from src.engines.fielding.data_report import data_field_report
from src.engines.fielding.dryrun import _answer as synthetic_answer
from src.engines.fielding.field_report import summarise
from src.engines.fielding.materialize import ALL_STATUSES, materialize_fielding
from src.engines.questionnaire.extractor import promote_multi_answer_grids
from src.engines.questionnaire.linker import QuestionVariableLink
from src.renderers.surveyjs_compiler import compile_to_surveyjs

ROWS = [Option(1, "Delivery pharmacy"), Option(2, "In-person pharmacy")]
COLS = [Option(1, "Timing"), Option(2, "Security"), Option(3, "Privacy")]


def _q21(text="What concerns do you have about each? Select all that apply") -> Question:
    return Question(qid="Q21", type=QuestionType.GRID_SINGLE, text=text,
                    rows=list(ROWS), cols=list(COLS))


def _survey(q: Question) -> SurveyDefinition:
    return SurveyDefinition(study_id="s", blocks=[Block(block_id="B", question_ids=[q.qid])],
                            questions=[q])


def test_a_grid_worded_select_all_takes_several_answers_per_row():
    s = _survey(_q21())
    promote_multi_answer_grids(s)
    assert s.questions[0].type is QuestionType.GRID_MULTI


def test_one_answer_per_row_stays_a_rating_grid():
    s = _survey(_q21("Select all that apply — one answer for each row"))
    promote_multi_answer_grids(s)
    assert s.questions[0].type is QuestionType.GRID_SINGLE


def test_the_link_flags_the_grid_and_the_data_has_one_column_per_cell():
    q = _q21()
    q.type = QuestionType.GRID_MULTI
    element = compile_to_surveyjs(_survey(q))["pages"][0]["elements"][0]
    assert element["type"] == "matrix" and element["multiAnswer"] is True

    sessions = [SimpleNamespace(id=i, status_code=10, started_at=None, completed_at=None,
                                entry_params={}, block_order=None) for i in ("a", "b")]
    answers = {"a": {"Q21": {"1": [1, 3], "2": [2]}}, "b": {"Q21": {"1": [2]}}}
    datamap, frame = materialize_fielding(_survey(q), sessions, answers, ALL_STATUSES)
    assert list(frame["Q21r1c1"]) == [1, 0]
    assert list(frame["Q21r1c3"]) == [1, 0]
    assert frame["Q21r2c2"].tolist()[0] == 1 and frame["Q21r2c2"].isna().tolist()[1]
    assert datamap["Q21r1c1"].values == {0: "Not selected", 1: "Selected"}


def test_a_condition_on_a_row_means_that_answer_was_ticked():
    answer = {"Q21": {"1": [1, 3]}}
    assert evaluate(Expression(op="eq", qid="Q21", row="1", value=3), answer)
    assert not evaluate(Expression(op="eq", qid="Q21", row="1", value=2), answer)


def test_the_topline_counts_each_cell_and_the_dry_run_ticks_several():
    q = _q21()
    q.type = QuestionType.GRID_MULTI
    card = summarise(q, [{"Q21": {"1": [1, 3]}}, {"Q21": {"1": [1]}}])
    assert card["rows"][0]["counts"] == [2, 0, 1]
    made = synthetic_answer(q, random.Random(1), None)
    assert set(made) == {"1", "2"} and all(isinstance(v, list) and v for v in made.values())


def test_an_uploaded_export_reads_the_cells_back_as_answers():
    q = _q21()
    q.type = QuestionType.GRID_MULTI
    names = ["Q21r1c1", "Q21r1c2", "Q21r1c3"]
    df = pl.DataFrame({"Q21r1c1": [1, 0], "Q21r1c2": [0, 1], "Q21r1c3": [1, 0]})
    report = data_field_report(df, DataMap(), _survey(q),
                               [QuestionVariableLink(qid="Q21", variable_names=names)])
    card = next(c for c in report["questions"] if c["qid"] == "Q21")
    assert card["rows"][0]["counts"] == [1, 1, 1]

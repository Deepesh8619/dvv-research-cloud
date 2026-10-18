"""Bipolar grids: two opposite statements per row, read from the document's table.

Bubble Q41 came out as a grid whose rows were "0=Agree fully with" and "Theme".
"""

from types import SimpleNamespace

from src.ai.router import AIResponse
from src.core.survey_definition import Block, Option, Question, QuestionType, SurveyDefinition
from src.engines.fielding.materialize import ALL_STATUSES, materialize_fielding
from src.engines.questionnaire import schema
from src.engines.questionnaire import second_look as sl
from src.engines.questionnaire.bipolar import FLIP_VARIABLE, apply_flip, read_table
from src.engines.questionnaire.question_agent import revise_with_context
from src.renderers.surveyjs_compiler import compile_to_surveyjs

SCALE = [Option(c, str(c)) for c in range(0, 11)]
SOURCE = "\n".join([
    "Q41. Now you'll see pairs of opposite statements about skincare.",
    "0=Agree fully with this statement | 1 | 2 | 3 | 4 | 5=Neutral | 6 | 7 | 8 | 9 | 10=Agree",
    " | [PN: HALF SEE LEFT AND RIGHT STATEMENTS FLIPPED] | [PN: SAME] | Theme [DO NOT SHOW]",
    "Q41_1 | I stick with brands I know | I look for new skincare | Loyalty vs. Exploration",
    "Q41_2 | Skincare solves problems | Skincare is about feeling | Functional vs. Emotional",
    "Q41_3 | I keep it simple | I enjoy premium touches | Minimalist vs. Indulgent",
])


def _misread() -> Question:
    return Question(qid="Q41", type=QuestionType.GRID_SINGLE,
                    text="Now you'll see pairs of opposite statements about skincare.",
                    rows=[Option(1, "0=Agree fully with"), Option(2, "Theme")],
                    cols=list(SCALE), source_text=SOURCE)


def _survey(q: Question) -> SurveyDefinition:
    return SurveyDefinition(study_id="s", blocks=[Block(block_id="B", question_ids=[q.qid])],
                            questions=[q])


def test_pairs_are_read_from_the_table_and_the_hidden_theme_is_left_out():
    after = read_table(_misread())
    assert [(r.code, r.label) for r in after.rows] == [
        (1, "I stick with brands I know | I look for new skincare"),
        (2, "Skincare solves problems | Skincare is about feeling"),
        (3, "I keep it simple | I enjoy premium touches"),
    ]
    assert after.validation["bipolar"] == {"flip_half": True}


def test_a_one_column_table_is_plain_rows_not_pairs():
    q = _misread()
    q.qid, q.text = "Q9", "How much do you agree with each?"
    q.source_text = "Q9. How much do you agree?\nQ9_1 | Price matters\nQ9_2 | Quality matters"
    after = read_table(q)
    assert [r.label for r in after.rows] == ["Price matters", "Quality matters"]
    assert "bipolar" not in (after.validation or {})


def test_the_second_look_fixes_the_misread_grid_by_rule():
    q = _misread()
    sl.second_look(_survey(q), use_model=False)
    r = q.validation["resolution"]
    assert r["status"] == "fixed" and "3 pairs" in r["summary"]
    assert len(q.rows) == 3


def test_the_flipped_arm_swaps_sides_and_reverses_the_scale_so_codes_keep_their_meaning():
    q = read_table(_misread())
    compiled = compile_to_surveyjs(_survey(q))
    grid = compiled["pages"][0]["elements"][0]
    assert grid["rows"][0]["leftText"] == "I stick with brands I know"
    flipped = apply_flip(compiled, ["Q41"])["pages"][0]["elements"][0]
    assert flipped["rows"][0]["leftText"] == "I look for new skincare"
    assert [c["value"] for c in flipped["columns"]][:2] == [10, 9]
    assert grid["columns"][0]["value"] == 0  # the original is untouched


def test_the_arm_each_respondent_saw_is_in_the_data():
    q = read_table(_misread())
    sessions = [SimpleNamespace(id=s, status_code=10, started_at=None, completed_at=None,
                                entry_params={"bipolar_flip": arm}, block_order=None)
                for s, arm in (("a", "1"), ("b", "2"))]
    datamap, frame = materialize_fielding(_survey(q), sessions,
                                          {"a": {"Q41": {"1": 3}}, "b": {"Q41": {"1": 7}}},
                                          ALL_STATUSES)
    assert datamap[FLIP_VARIABLE].values == {
        1: "Statements as written", 2: "Left and right flipped"}
    assert list(frame[FLIP_VARIABLE]) == [1, 2]


def test_a_kind_the_link_cannot_run_is_kept_on_record_not_squeezed(monkeypatch):
    q = Question(qid="Q6", type=QuestionType.MULTI, text="Which brands for each product?",
                 options=[Option(1, "Acme")], source_text="Q6. Which brands for each product?"
                 " Acme Fizz Cleansers Toners")

    def fake(task, prompt, schema_, **_):
        return AIResponse({"type": "grid_multi", "rows": [{"code": 1, "label": "Cleansers"},
                                                          {"code": 2, "label": "Toners"}],
                           "cols": [{"code": 1, "label": "Acme"}, {"code": 2, "label": "Fizz"}],
                           "explanation": "Brands per product."}, "bedrock", "qwen3")

    monkeypatch.setattr("src.engines.questionnaire.question_agent.run_task", fake)
    out = revise_with_context(q, "fix it", {"Q6": q}, [])
    after = Question.from_dict(out["question"])
    assert after.validation["intended_type"] == "grid_multi"
    assert schema.FLAG_TYPE_NOT_SUPPORTED in after.flags

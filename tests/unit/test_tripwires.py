"""Tripwires: a confident reading the document itself contradicts gets a second look.

Bubble Q41 was read as a grid whose rows were "0=Agree fully with" and "Theme"
with no flag, so neither the second look nor the model ever saw it. Vivo Q13
carried its wrapped scale lines as three extra rows.
"""

from src.ai.router import AIResponse
from src.core.survey_definition import Option, Question, QuestionType, SurveyDefinition
from src.engines.questionnaire import second_look as sl
from src.engines.questionnaire.consistency import reading_contradicts_document

SCALE = [Option(c, str(c)) for c in range(0, 11)]

Q41_SOURCE = "\n".join([
    "Q41. Now you'll see pairs of opposite statements about skincare.",
    "0=Agree fully with",
    "this statement | 1 | 2 | 3 | 4 | 5=Neutral | 6 | 7 | 8 | 9 | 10=Agree fully",
    " | [PN: RANDOMIZE] | [PN: RANDOMIZE] | Theme [DO NOT SHOW]",
    "Q41_1 | I stick with the skincare brands I know | I'm always looking for new | Loyalty",
    "Q41_2 | Skincare is about solving real problems | It is about how I feel | Fun",
    "Q41_3 | I keep it simple | I enjoy premium touches | Minimal",
])


def _q41() -> Question:
    return Question(qid="Q41", type=QuestionType.GRID_SINGLE,
                    text="Now you'll see pairs of opposite statements about skincare.",
                    rows=[Option(1, "0=Agree fully with"), Option(2, "Theme")],
                    cols=list(SCALE), source_text=Q41_SOURCE)


def _checks(q: Question) -> set[str]:
    return {i.check for i in reading_contradicts_document(q, {q.qid: q})}


def test_rows_that_are_a_scale_label_or_heading_and_a_row_count_the_document_contradicts():
    assert _checks(_q41()) == {"GRID_ROWS_NOT_STATEMENTS", "ROWS_DIFFER_FROM_DOCUMENT"}


def test_a_scale_the_reading_cut_short():
    q = _q41()
    q.rows = [Option(1, "I stick with the skincare brands I know"),
              Option(2, "Skincare is about solving real problems"),
              Option(3, "I keep it simple")]
    q.cols = SCALE[:8]
    assert _checks(q) == {"SCALE_CUT_SHORT"}


def test_a_table_read_as_a_list_is_caught():
    q = _q41()
    q.type, q.rows, q.cols = QuestionType.SINGLE, [], []
    q.options = [Option(1, "Loyalty"), Option(2, "Fun"), Option(3, "Minimal")]
    assert "ROWS_DIFFER_FROM_DOCUMENT" in _checks(q)


def test_a_correct_grid_trips_nothing():
    q = Question(qid="Q7", type=QuestionType.GRID_SINGLE, text="Rate each brand",
                 rows=[Option(1, "Acme"), Option(2, "Fizz")], cols=SCALE[1:6],
                 source_text="Q7. Rate each brand\n1 | 2 | 3 | 4 | 5\nAcme\nFizz")
    assert _checks(q) == set()


def test_wrapped_scale_lines_read_as_rows_are_removed_by_rule():
    cols = [Option(1, "Within the past 3 months"), Option(2, "Within the past 6 months")]
    q = Question(qid="Q13", type=QuestionType.GRID_SINGLE, text="When did you last use each?",
                 rows=[Option(1, "1=Within the past 3 months | 2= Within the past"),
                       Option(2, "6 months (but not in the past 3 months) | 3=Within the past"),
                       Option(3, "CVS"), Option(4, "Walgreens")],
                 cols=cols, source_text="Q13. When did you last use each?")
    sl.second_look(SurveyDefinition(study_id="s", questions=[q]), use_model=False)
    assert q.validation["resolution"]["status"] == "fixed"
    assert [r.label for r in q.rows] == ["CVS", "Walgreens"]


def test_a_tripped_grid_goes_to_the_model_and_its_fix_is_offered_when_the_tripwire_clears(
        monkeypatch):
    seen = {}

    def fake(task, prompt, schema_, **_):
        seen["prompt"] = prompt
        return AIResponse({
            "type": "grid_single",
            "rows": [{"code": 1, "label": "I stick with the skincare brands I know"},
                     {"code": 2, "label": "Skincare is about solving real problems"},
                     {"code": 3, "label": "I keep it simple"}],
            "cols": [{"code": o.code, "label": o.label} for o in SCALE],
            "explanation": "The rows are the statement pairs Q41_1 and Q41_2.",
        }, "bedrock", "qwen3")

    monkeypatch.setattr("src.engines.questionnaire.question_agent.run_task", fake)
    q = _q41()
    # No `Q41_n |` table to read by rule: the statements are plain lines.
    q.source_text = "\n".join(line.split(" | ")[1] if line.startswith("Q41_") else line
                              for line in Q41_SOURCE.splitlines())
    sl.second_look(SurveyDefinition(study_id="s", questions=[q]), use_model=True)
    assert "I keep it simple" in seen["prompt"]  # the model was shown the document
    r = q.validation["resolution"]
    assert r["status"] == "ready"
    assert [row["code"] for row in r["proposed"]["rows"]] == [1, 2, 3]

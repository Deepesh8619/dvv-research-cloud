"""`reread_question`: replay the current deterministic parsers, no model call.

The endpoint-level behaviour (what counts as an improvement, 422 on none) is
covered in tests/integration/test_reread_and_split_routes.py; this is the pure
function itself, including the table-rows-from-source_text rebuild.
"""

from __future__ import annotations

from src.core.survey_definition import Question, QuestionType
from src.engines.questionnaire.extractor import reread_question


def test_a_question_with_no_source_text_cannot_be_reread():
    assert reread_question(Question(qid="Q1", text="Anything")) is None


def test_text_with_no_recognisable_label_cannot_be_reread():
    # No "Q1." / "S3." style label and no candidate_qid to fall back on —
    # parse_prose_question has nothing to anchor a question to.
    q = Question(qid="", text="", source_text="just some loose prose, no label at all")
    q.qid = ""
    assert reread_question(q) is None


def test_a_grids_rows_and_columns_are_rebuilt_from_the_pipe_joined_table_lines():
    # A generic (non Q-Name) table is serialized one row per line, cells joined
    # by " | " (questionnaire_loader's plain pipe-join fallback) — the same
    # shape `_rebuild_table_rows` splits back apart.
    rows = [
        ["", "", "Very likely", "Somewhat likely", "Not likely"],
        ["1", "Product A", "", "", ""],
        ["2", "Product B", "", "", ""],
    ]
    table_text = "\n".join(" | ".join(r) for r in rows)
    source = f"G1. How likely are you to buy each product?\n{table_text}"
    old = Question(
        qid="G1", text="How likely are you to buy each product?",
        type=QuestionType.SINGLE, options=[], source_text=source,
    )

    new = reread_question(old)

    assert new is not None
    assert new.type == QuestionType.GRID_SINGLE
    assert [o.label for o in (new.rows or [])] == ["Product A", "Product B"]
    assert [o.label for o in (new.cols or [])] == ["Very likely", "Somewhat likely", "Not likely"]

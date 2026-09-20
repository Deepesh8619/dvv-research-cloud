"""`reread_question`: replay the current deterministic parsers, no model call.

The endpoint-level behaviour (what counts as an improvement, 422 on none) is
covered in tests/integration/test_reread_and_split_routes.py; this is the pure
function itself, including the table-rows-from-source_text rebuild.
"""

from __future__ import annotations

from src.core.survey_definition import Option, Question, QuestionType
from src.engines.questionnaire.extractor import reread_question
from src.engines.questionnaire.learned import LearnedPattern


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


def test_a_client_specific_option_tag_stays_unread_without_a_taught_pattern_and_reads_with_one():
    # "[SCREENER FAIL]" means nothing to the built-in option-tag table (it is
    # neither "TERMINATE" nor "SCREEN OUT") — a founder has to teach it. This
    # is the core of the bug: a taught pattern must be able to reach THIS
    # question on THIS re-read, not only the next document uploaded.
    source = "S2. Are you currently employed?\nYes\nNo [SCREENER FAIL]"
    old = Question(
        qid="S2", text="Are you currently employed?", type=QuestionType.SINGLE,
        options=[Option(code=1, label="Yes"), Option(code=2, label="No [SCREENER FAIL]")],
        source_text=source,
    )

    untaught = reread_question(old)
    assert untaught is not None
    assert untaught.terminate_if is None

    taught = reread_question(
        old, [LearnedPattern("option_tag", "[SCREENER FAIL]", "terminate")]
    )
    assert taught is not None
    assert taught.terminate_if is not None
    assert taught.terminate_if.values == [2]

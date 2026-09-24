"""Which block a respondent meets first, and the record that it happened.

    Block order: Rotate the presentation of the two blocks evenly
    (50% see Google Ads first, 50% see Meta Ads first).

Whichever brand is asked about first is rated differently. Fielded in a fixed
order every respondent meets Google first, so the Google-versus-Meta difference
the study exists to measure carries an order effect nobody can separate out —
the data looks complete and the finding is unsound.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.core.survey_definition import Block, Option, Question, SurveyDefinition
from src.engines.fielding.materialize import ALL_STATUSES, materialize_fielding
from src.engines.fielding.rotation import (
    ROTATION_VARIABLE,
    arms,
    assign,
    order_pages,
    rotating_blocks,
)
from src.renderers.surveyjs_compiler import compile_to_surveyjs

YES_NO = [Option(code=1, label="Yes"), Option(code=2, label="No")]


def _survey(rotating: bool = True) -> SurveyDefinition:
    questions = [
        Question(qid=q, text=f"{q}?", options=list(YES_NO))
        for q in ("S1", "GA1", "M1", "Z9")
    ]
    survey = SurveyDefinition(
        study_id="T",
        questions=questions,
        blocks=[
            Block(block_id="blk_main", label="", question_ids=["S1"]),
            Block(block_id="blk_google_ads", label="Google Ads", question_ids=["GA1"]),
            Block(block_id="blk_meta_ads", label="Meta Ads", question_ids=["M1"]),
            Block(block_id="blk_rest", label="", question_ids=["Z9"]),
        ],
    )
    if rotating:
        survey.metadata["blocks_from_note"] = {
            "block_ids": ["blk_google_ads", "blk_meta_ads"],
            "rotation": "Rotate the presentation of the two blocks evenly",
        }
    return survey


# --- assignment -------------------------------------------------------------


def test_each_respondent_takes_the_arm_that_is_behind():
    """Least fill, not a coin flip.

    A fair coin on 500 respondents lands 54/46 often enough to bias the very
    comparison the rotation exists to protect. Balanced by construction.
    """
    available = arms(_survey())
    assert assign({}, available) == "blk_google_ads"
    assert assign({"blk_google_ads": 1}, available) == "blk_meta_ads"
    assert assign({"blk_google_ads": 5, "blk_meta_ads": 5}, available) == "blk_google_ads"
    assert assign({"blk_google_ads": 6, "blk_meta_ads": 5}, available) == "blk_meta_ads"


def test_assigning_alternates_and_stays_within_one():
    """Fifty respondents, and the split never drifts."""
    available = arms(_survey())
    counts: dict[str, int] = {}
    for _ in range(50):
        counts[assign(counts, available)] = counts.get(assign(counts, available), 0) + 1
    assert abs(counts["blk_google_ads"] - counts["blk_meta_ads"]) <= 1


# --- ordering ---------------------------------------------------------------


def test_only_the_rotating_pages_move():
    """The screener before them and the rest after keep their places."""
    compiled = compile_to_surveyjs(_survey())
    assert compiled["rotatingPages"] == ["blk_google_ads", "blk_meta_ads"]

    google_first = [p["name"] for p in order_pages(compiled, "blk_google_ads")["pages"]]
    meta_first = [p["name"] for p in order_pages(compiled, "blk_meta_ads")["pages"]]

    assert google_first == ["blk_main", "blk_google_ads", "blk_meta_ads", "blk_rest"]
    assert meta_first == ["blk_main", "blk_meta_ads", "blk_google_ads", "blk_rest"]


def test_every_page_survives_the_rotation():
    """A reorder that loses a page loses a question nobody is asked."""
    compiled = compile_to_surveyjs(_survey())
    for arm in arms(_survey()):
        names = [p["name"] for p in order_pages(compiled, arm)["pages"]]
        assert sorted(names) == sorted(p["name"] for p in compiled["pages"])


# --- a study that asked for nothing ----------------------------------------


def test_a_study_with_no_rotation_is_untouched():
    """Every study that came before this must compile byte-identically."""
    plain = _survey(rotating=False)
    assert rotating_blocks(plain) == []
    assert "rotatingPages" not in compile_to_surveyjs(plain)


# --- the record -------------------------------------------------------------


def _session(sid: str, arm: str | None):
    return SimpleNamespace(
        id=sid, status_code=10, started_at=f"2026-09-24T10:0{sid}:00",
        completed_at=f"2026-09-24T10:1{sid}:00", entry_params={}, block_order=arm,
    )


def test_the_arm_is_delivered_as_a_coded_variable():
    """The column that makes rotating worth doing.

    Rotate without recording it and the analyst is exactly where fixed order
    left them: they see the brand difference and cannot tell how much is order.
    """
    survey = _survey()
    sessions = [_session("1", "blk_google_ads"), _session("2", "blk_meta_ads")]
    answers = {"1": {"GA1": 1, "M1": 2}, "2": {"GA1": 2, "M1": 1}}

    datamap, frame = materialize_fielding(survey, sessions, answers, ALL_STATUSES)

    variable = datamap[ROTATION_VARIABLE]
    # Coded with labels, so it tabulates like any other single-coded variable.
    assert variable.values == {1: "Google Ads", 2: "Meta Ads"}
    assert list(frame[ROTATION_VARIABLE]) == [1, 2]


def test_a_study_that_does_not_rotate_gains_no_column():
    """Nothing else collects a column full of blanks."""
    survey = _survey(rotating=False)
    sessions = [_session("1", None)]
    datamap, frame = materialize_fielding(survey, sessions, {"1": {"GA1": 1}}, ALL_STATUSES)

    assert ROTATION_VARIABLE not in {v.name for v in datamap.variables}
    assert ROTATION_VARIABLE not in frame.columns


def test_assign_refuses_when_there_is_nothing_to_assign():
    with pytest.raises(ValueError):
        assign({}, [])

"""Coding persistence: CodingRun -> JSON artifact (codeframe + review queue).

The artifact is what the review-queue API serves and what a reviewer's
decisions are recorded against — it must round-trip through json and put the
worst-confidence verbatims first.
"""

from __future__ import annotations

import json

import polars as pl

from src.engines.coding.codeframe import Code, CodeFrame
from src.engines.coding.runner import CodingRun, coding_run_to_artifact


def _run() -> CodingRun:
    codeframe = CodeFrame(
        question_var="OE1",
        codes=[
            Code(id="PRICE", label="Price complaints", description="too expensive"),
            Code(id="QUALITY", label="Quality", description="build quality"),
        ],
    )
    df = pl.DataFrame(
        {
            "respondent_id": [1, 2, 3],
            "OE1": ["too pricey", "loved it", "cheap plastic feel"],
            "codes": [["PRICE"], [], ["QUALITY"]],
            "coding_confidence": [0.95, 0.4, 0.6],
            "coding_reasoning": ["clear", "unclear", "borderline"],
            "needs_coding_review": [False, True, True],
        }
    )
    return CodingRun(
        question_var="OE1",
        codeframe=codeframe,
        df_coded=df,
        n_auto_coded=1,
        n_needs_review=2,
        confidence_mean=0.65,
    )


def test_artifact_round_trips_and_sorts_review_queue_worst_first():
    artifact = coding_run_to_artifact(_run())
    # Must survive json round-trip untouched (it's persisted with json.dumps).
    artifact = json.loads(json.dumps(artifact))

    assert artifact["question_var"] == "OE1"
    assert [c["id"] for c in artifact["codeframe"]["codes"]] == ["PRICE", "QUALITY"]
    assert artifact["summary"]["n_needs_review"] == 2

    queue = artifact["review_queue"]
    # Only the two flagged rows, worst confidence first, with the verbatim text.
    assert [item["respondent_id"] for item in queue] == [2, 3]
    assert queue[0]["confidence"] == 0.4
    assert queue[0]["verbatim"] == "loved it"
    assert queue[1]["suggested_codes"] == ["QUALITY"]
    assert all(item["status"] == "pending" for item in queue)

"""A grid whose columns are questions is several questions sharing a brand list.

    Which of these skincare brands have you ....
                    Q2a. ever heard of?   Q2b. Ever purchased from?
    Bubble                 [ ]                     [ ]
    Anua                   [ ]                     [ ]

The document lays Q2a and Q2b side by side because they share the brand list.
Read as one single-choice grid, each brand allowed ONE of "heard of" and
"purchased" — as if having bought a brand meant never having heard of it — and
the respondent was shown "Q2a." and "Q2b.", labels that mean nothing to them.

Each column is its own multi-select over the rows, with the stem completed by
the column's wording: "Which of these skincare brands have you ever heard of?".
That is also the data a scripter delivers — Q2a_1..n and Q2b_1..n, one column
per brand per question — not one grid variable whose value says which of two
unrelated things a respondent did.

Only a grid whose EVERY column carries its own question label is split. A
column headed "Very satisfied" is an answer, and the grid stays a grid.
"""

from __future__ import annotations

import re
from dataclasses import replace

from src.core.survey_definition import Question, QuestionType, SurveyDefinition
from src.engines.questionnaire.loops import _replace

# "Q2a. ever heard of?", "Q2b) Ever purchased from?", "S10A: Used in P3M"
_SUBQUESTION_LABEL = re.compile(
    r"^\s*(?P<qid>[A-Za-z]{1,4}\d+[A-Za-z]{0,2}(?:_\d+)?)\s*[.:)]\s+(?P<text>\S.*)$"
)
# "have you ....", "have you…" — a stem the column finishes.
_TRAILING_ELLIPSIS = re.compile(r"\s*(?:\.{2,}|…)\s*$")


def column_question(label: str) -> tuple[str, str] | None:
    """(qid, wording) when a column header is a question label, else None."""
    match = _SUBQUESTION_LABEL.match(label or "")
    return (match.group("qid"), match.group("text").strip()) if match else None


def without_question_label(label: str) -> str:
    """The header as a respondent should read it: "Q2a. ever heard of?" → "ever heard of?"."""
    parsed = column_question(label)
    return parsed[1] if parsed else label


def split_subquestion_grids(survey: SurveyDefinition) -> list[dict]:
    """Replace every grid whose columns are questions with one question per column."""
    applied: list[dict] = []
    for grid in list(survey.questions):
        if grid.type is not QuestionType.GRID_SINGLE or not grid.rows:
            continue
        parsed = [column_question(c.label) for c in grid.cols or []]
        if len(parsed) < 2 or None in parsed:
            continue
        qids = [qid for qid, _ in parsed]  # type: ignore[misc]
        if len(set(qids)) != len(qids) or any(q in survey for q in qids):
            continue  # a clash with a real question is not ours to resolve
        parts = [_part(grid, qid, wording) for qid, wording in parsed]  # type: ignore[misc]
        _replace(survey, grid, parts)
        applied.append({"qid": grid.qid, "split_into": qids})
    if applied:
        survey.metadata["subquestion_grids"] = applied
        survey._rebuild_index()
    return applied


def _part(grid: Question, qid: str, wording: str) -> Question:
    stem = _TRAILING_ELLIPSIS.sub("", grid.text or "").strip()
    if stem and stem[-1] not in "?.:" and wording[:1].isupper() and not wording[1:2].isupper():
        wording = wording[0].lower() + wording[1:]  # "have you" + "Ever purchased"
    text = f"{stem} {wording}".strip() if stem else wording
    return Question(
        qid=qid,
        text=text,
        type=QuestionType.MULTI,
        options=[replace(row) for row in grid.rows or []],
        required=grid.required,
        randomize_options=grid.randomize_options,
        show_if=grid.show_if,
        terminate_if=grid.terminate_if,
        piping_refs=list(grid.piping_refs or []),
        validation=dict(grid.validation or {}),
        source_text=grid.source_text,
        source_location=grid.source_location,
        extraction_method="grid_subquestion_split",
        extraction_evidence={**grid.extraction_evidence, "split_from": grid.qid},
        flags=list(grid.flags or []),
    )

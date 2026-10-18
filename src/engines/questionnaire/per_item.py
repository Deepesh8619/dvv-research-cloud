"""A question asked about EACH of several things is a grid, not one answer.

    Q4a. [ONLY SHOW THOSE AWARE OF AT Q3] Looking again at these pharmacies
         you've heard of before — have you ever personally used each one?
         Yes
         No

Two answers, thirteen pharmacies. Held as a single question that is one
column in the data file where there should be thirteen, and a respondent
asked about all of them with one click. The scale is right; what was lost is
the things being rated.

The shape is only converted when the answers are a SCALE — Yes/No, degrees of
agreement, a frequency. When the answers are the items themselves ("which of
these pharmacies have you seen advertising for", answered with the pharmacy
list) nothing is wrong and nothing is touched. That distinction is what keeps
this from turning ordinary multi-selects into grids with no rows.

Rows are left empty on purpose: `list_sources.fill_lists` runs next and takes
them from the list the wording points at, which is where they are stated.
"""

from __future__ import annotations

from src.core.survey_definition import GRID_TYPES, QuestionType, SurveyDefinition
from src.engines.questionnaire.definition_audit import _PER_ITEM
from src.engines.questionnaire.list_sources import _looks_like_a_scale

#: A scale a respondent reads in one glance. Longer than this and the answers
#: are a list of things, whatever they look like.
_MAX_SCALE_POINTS = 11

_CONVERTIBLE = {QuestionType.SINGLE, QuestionType.MULTI, QuestionType.DROPDOWN}


def apply(survey: SurveyDefinition) -> list[str]:
    """Turn per-item questions holding a scale into grids. Returns the qids."""
    changed: list[str] = []
    for question in survey.questions:
        # A grid holding its scale as OPTIONS, with no columns, is the same
        # shape wearing a different type.
        grid_without_columns = question.type in GRID_TYPES and not question.cols
        if (question.type not in _CONVERTIBLE and not grid_without_columns) or question.rows:
            continue
        if not _PER_ITEM.search(question.text or ""):
            continue
        options = list(question.options or [])
        if not (2 <= len(options) <= _MAX_SCALE_POINTS):
            continue
        if not _looks_like_a_scale(question):
            continue  # the answers ARE the items: nothing was lost
        question.type = QuestionType.GRID_SINGLE
        question.cols = options
        question.rows = []  # filled next, from the list the wording points at
        question.options = []
        question.extraction_evidence["type"] = (
            f"asked about EACH of several things, answered on a {len(options)}-point "
            "scale — a grid, with its rows stated elsewhere"
        )
        changed.append(question.qid)
    if changed:
        survey.metadata["per_item_grids"] = changed
    return changed

"""Numbers that must add up: one box per item, one total.

    Q37. Thinking about your monthly skincare spend, roughly what percentage
         goes to each of the following?
         ___ % Brands I already know and trust
         ___ % Brands that are new to me or I'm trying for the first time
         Total: 100% [PN: MUST SUM TO 100]

Two numbers, one per line, that must total 100. Read as an answer list it was
a single choice between two blanks and "Total: 100%" — or, typed as a grid, a
grid with no rows. Either way the respondent could not give an answer and the
data file had no column for the two shares.

It is a number question with ROWS: one box, and one data column, per item, and
a rule that they sum to the total the document states.
"""

from __future__ import annotations

import re

from src.core.survey_definition import Option, QuestionType, SurveyDefinition
from src.engines.questionnaire import schema

# "___ % Brands I already know and trust", "____ Hours on weekdays"
_BOX_LINE = re.compile(r"^\s*_{2,}\s*%?\s*(?P<label>\S.*?)\s*$")
# "MUST SUM TO 100", "should add up to 100%", "Total: 100%"
_TOTAL = re.compile(
    r"(?:SUM\s+TO|ADD\s+UP\s+TO|TOTAL\s*[:=]?)\s*(?P<n>\d{1,4})\s*%?", re.I
)
_MIN_ITEMS = 2


def apply(survey: SurveyDefinition) -> list[str]:
    """Turn every "___ % item" list into a constant-sum number question."""
    changed = []
    for q in survey.questions:
        lines = (q.source_text or "").splitlines()
        items = [m.group("label") for line in lines if (m := _BOX_LINE.match(line))]
        if len(items) < _MIN_ITEMS:
            continue
        total = _TOTAL.search(q.source_text or "")
        q.type = QuestionType.NUMERIC
        q.rows = [Option(code=i, label=label) for i, label in enumerate(items, start=1)]
        q.options = []
        q.cols = None
        validation = dict(q.validation or {})
        validation["min"] = 0
        if total:
            validation["sum"] = int(total.group("n"))
            validation["max"] = int(total.group("n"))
        q.validation = validation
        q.extraction_evidence["type"] = (
            f"{len(items)} boxes to fill"
            + (f", totalling {total.group('n')}" if total else "")
        )
        if schema.FLAG_EMPTY_OPTIONS in q.flags:
            q.flags.remove(schema.FLAG_EMPTY_OPTIONS)
        changed.append(q.qid)
    if changed:
        survey.metadata["constant_sums"] = changed
    return changed

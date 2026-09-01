"""Check collected data against the questionnaire it was collected under.

This is the check a DP shop actually gets paid for: the questionnaire is the
contract, the data file is what came back, and the job is to find every row
where they disagree before it reaches a client.

Nothing here calls a model. "Q7 declares codes 1-5 and the data contains 6" is
arithmetic, not judgement, and a finding a human cannot reproduce by hand is
worse than no finding at all. Each discrepancy therefore carries the count and
a few example row numbers so a researcher can open the file and look.

Four checks, one per promise a question makes about its own data:

    undeclared_code    a value outside the question's declared option codes
    single_multi       more than one response to a single-answer question
    out_of_range       a numeric answer outside the stated range
    terminate_violated a completed respondent the screen-out should have removed

`show_if` routing is deliberately NOT re-checked here — `linker.cross_validate_
routing` already owns that and reports it in its own shape.

A note on trust: the terminate check is only as good as the extracted
`terminate_if`. Before September 2026 those conditions evaluated false for
every respondent, so this check would have reported a clean bill of health on
any file, for ever. That is why `checked` is reported alongside the findings —
"nothing found" and "nothing looked at" must never read the same.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.core.survey_definition import (
    Question,
    QuestionType,
    QuestionVariableLink,
    SurveyDefinition,
    evaluate,
)
from src.engines.questionnaire.linker import _read_rows, _row_value

# How many offending row numbers to keep per finding. Enough to start looking,
# not so many that a systematically broken column produces a megabyte of noise.
MAX_EXAMPLES = 5

# Codes that appear in almost every real questionnaire as system values without
# being written into the response list. Flagging these would bury the real
# findings, which is how a check stops being read at all.
DEFAULT_SYSTEM_CODES = frozenset({-1, 0, 77, 88, 97, 98, 99, 998, 999, -99, -98})


@dataclass
class Discrepancy:
    """One disagreement between the questionnaire and the data."""

    qid: str
    kind: str
    severity: str  # "error" | "warning"
    detail: str
    n_rows: int = 0
    variables: list[str] = field(default_factory=list)
    example_rows: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "qid": self.qid,
            "kind": self.kind,
            "severity": self.severity,
            "detail": self.detail,
            "n_rows": self.n_rows,
            "variables": self.variables,
            "example_rows": self.example_rows,
        }


@dataclass
class DiscrepancyReport:
    """Findings plus what was actually examined.

    `checked` and `skipped` matter as much as `findings`: a report that only
    listed problems would let a questionnaire nobody could link to the data
    pass as spotless.
    """

    findings: list[Discrepancy] = field(default_factory=list)
    n_rows: int = 0
    checked: dict[str, int] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(f.severity == "error" for f in self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "n_rows": self.n_rows,
            "checked": self.checked,
            "skipped": self.skipped,
            "findings": [f.to_dict() for f in self.findings],
        }


def _as_number(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _bound_text(value: float | None) -> str:
    """A range bound as a researcher would write it: 18, not 18.0."""
    if value is None:
        return "any"
    return str(int(value)) if value == int(value) else str(value)


def _declared_codes(question: Question) -> set[int]:
    return {o.code for o in question.options or []}


def _numeric_range(question: Question) -> tuple[float | None, float | None]:
    """The stated min/max for a numeric question, if it has one."""
    validation = question.validation or {}
    lo = validation.get("min", validation.get("minimum"))
    hi = validation.get("max", validation.get("maximum"))
    return _as_number(lo), _as_number(hi)


def _selected_codes(row: dict, variables: list[str], question: Question) -> list[int]:
    """Codes this respondent chose, as declared codes rather than positions.

    A single-column question stores the code itself. A multi explodes into one
    dummy per option, and `_row_value` returns 1-based positions — those are
    translated back through the option list, because a position is meaningless
    to anyone reading the finding.
    """
    value = _row_value(row, variables)
    if value is None:
        return []
    if not isinstance(value, list):
        number = _as_number(value)
        return [int(number)] if number is not None and number == int(number) else []
    codes = [o.code for o in question.options or []]
    return [codes[i - 1] for i in value if 1 <= i <= len(codes)]


def check_against_questionnaire(
    survey: SurveyDefinition,
    links: list[QuestionVariableLink],
    parquet_path: Path,
    system_codes: frozenset[int] = DEFAULT_SYSTEM_CODES,
) -> DiscrepancyReport:
    """Compare a data file against the questionnaire that produced it.

    Returns an empty report (not an error) when the data file does not exist —
    a questionnaire can be reviewed and approved long before any data arrives.
    """
    report = DiscrepancyReport()
    if not Path(parquet_path).exists():
        report.skipped.append("no data file")
        return report

    by_qid = {q.qid: q for q in survey.questions}
    link_map = {link.qid: link.variable_names for link in links if link.variable_names}
    if not link_map:
        report.skipped.append("no question could be linked to a variable")
        return report

    columns = sorted({name for names in link_map.values() for name in names})
    rows = _read_rows(Path(parquet_path), columns)
    report.n_rows = len(rows)
    if not rows:
        report.skipped.append("data file has no rows")
        return report

    counts = {"codes": 0, "single": 0, "range": 0, "terminate": 0}

    for qid, variables in sorted(link_map.items()):
        question = by_qid.get(qid)
        if question is None:
            continue

        declared = _declared_codes(question)
        if declared:
            counts["codes"] += 1
            offenders: dict[int, list[int]] = {}
            for index, row in enumerate(rows, start=1):
                for code in _selected_codes(row, variables, question):
                    if code not in declared and code not in system_codes:
                        offenders.setdefault(code, []).append(index)
            for code, hits in sorted(offenders.items()):
                report.findings.append(
                    Discrepancy(
                        qid=qid,
                        kind="undeclared_code",
                        severity="error",
                        detail=(
                            f"Value {code} appears in the data but {qid} declares "
                            f"only {sorted(declared)}."
                        ),
                        n_rows=len(hits),
                        variables=variables,
                        example_rows=hits[:MAX_EXAMPLES],
                    )
                )

        if question.type is QuestionType.SINGLE and len(variables) > 1:
            counts["single"] += 1
            hits = [
                index
                for index, row in enumerate(rows, start=1)
                if len(_selected_codes(row, variables, question)) > 1
            ]
            if hits:
                report.findings.append(
                    Discrepancy(
                        qid=qid,
                        kind="single_multi",
                        severity="error",
                        detail=(
                            f"{qid} is a single-answer question but some respondents "
                            "have more than one response recorded."
                        ),
                        n_rows=len(hits),
                        variables=variables,
                        example_rows=hits[:MAX_EXAMPLES],
                    )
                )

        low, high = _numeric_range(question)
        if question.type is QuestionType.NUMERIC and (low is not None or high is not None):
            counts["range"] += 1
            hits = []
            for index, row in enumerate(rows, start=1):
                number = _as_number(_row_value(row, variables))
                if number is None or int(number) in system_codes:
                    continue
                if (low is not None and number < low) or (high is not None and number > high):
                    hits.append(index)
            if hits:
                bounds = f"{_bound_text(low)} to {_bound_text(high)}"
                report.findings.append(
                    Discrepancy(
                        qid=qid,
                        kind="out_of_range",
                        severity="error",
                        detail=f"{qid} allows {bounds} but some answers fall outside it.",
                        n_rows=len(hits),
                        variables=variables,
                        example_rows=hits[:MAX_EXAMPLES],
                    )
                )

    for qid, question in sorted(by_qid.items()):
        if question.terminate_if is None:
            continue
        referenced = question.terminate_if.referenced_qids()
        if not referenced or any(ref not in link_map for ref in referenced):
            # Cannot honestly evaluate it, so say so rather than pass it.
            report.skipped.append(f"{qid}: screen-out references an unlinked question")
            continue
        counts["terminate"] += 1
        hits = []
        for index, row in enumerate(rows, start=1):
            answers = {
                ref: _row_value(row, link_map[ref])
                for ref in referenced
                if link_map.get(ref)
            }
            if evaluate(question.terminate_if, answers):
                hits.append(index)
        if hits:
            report.findings.append(
                Discrepancy(
                    qid=qid,
                    kind="terminate_violated",
                    severity="error",
                    detail=(
                        f"{qid} screens out these respondents, but they are present "
                        "in the completed data."
                    ),
                    n_rows=len(hits),
                    variables=link_map.get(qid, []),
                    example_rows=hits[:MAX_EXAMPLES],
                )
            )

    report.checked = {k: v for k, v in counts.items() if v}
    # Worst first: a researcher reads the top of this list and stops.
    report.findings.sort(key=lambda f: (-f.n_rows, f.qid))
    return report

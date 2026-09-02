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

from src.core.models import DataMap
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


def _datamap_codes(
    datamap: DataMap | None, variables: list[str], qid: str = ""
) -> set[int] | None:
    """The codes the DATAMAP says a question's columns can hold.

    None means the datamap does not describe this as a coded question — an open
    numeric or text column carries no code list. That distinction is the
    difference between a useful check and an unusable one: on a real client
    file, comparing raw ages against a questionnaire's option list produced 14
    separate "undeclared code" findings for ages 32 to 45, all of them correct
    data. The datamap is the authority on what a column actually holds.
    """
    if datamap is None:
        return None
    by_name = {v.name: v for v in datamap.variables}
    codes: set[int] = set()
    described = False
    for name in variables:
        variable = by_name.get(name)
        if variable is None:
            continue
        # A multi-select punch column IS the option: `<parent>r98` is code 98,
        # and its value labels are only {0: Unchecked, 1: Checked}. Reading
        # just the value labels made every punch beyond 1 look unscripted.
        suffix = name[len(qid):].lstrip("rR") if qid and name.startswith(qid) else ""
        if suffix.isdigit():
            described = True
            codes.add(int(suffix))
        elif variable.values:
            described = True
            codes |= set(variable.values.keys())
    return codes if described else None


def check_against_questionnaire(
    survey: SurveyDefinition,
    links: list[QuestionVariableLink],
    parquet_path: Path,
    system_codes: frozenset[int] = DEFAULT_SYSTEM_CODES,
    datamap: DataMap | None = None,
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

    counts = {"codes": 0, "single": 0, "range": 0, "terminate": 0, "mask": 0}

    for qid, variables in sorted(link_map.items()):
        question = by_qid.get(qid)
        if question is None:
            continue

        declared = _declared_codes(question)
        # Without a datamap the questionnaire is the only authority we have, so
        # judge against it. With one, the datamap wins: it describes what the
        # column actually holds, and `None` from it means "open response", not
        # "unknown".
        scripted = (
            _datamap_codes(datamap, variables, qid) if datamap is not None else declared
        )
        datamap_says_open = datamap is not None and scripted is None

        # The questionnaire says this question has options, but the datamap
        # says the column holds an open number. That is a reading disagreement
        # worth ONE finding — not one per distinct value in the file.
        if declared and datamap_says_open:
            report.findings.append(
                Discrepancy(
                    qid=qid,
                    kind="type_mismatch",
                    severity="warning",
                    detail=(
                        f"{qid} was read as a coded question with options "
                        f"{sorted(declared)[:8]}, but the data map describes "
                        "its column as an open response. The extracted option "
                        "list is probably wrong."
                    ),
                    variables=variables,
                )
            )
        elif declared and datamap is not None and scripted is not None:
            # Punches vs questionnaire: what was scripted against what was asked.
            missing_in_data = sorted(declared - scripted - system_codes)
            extra_in_data = sorted(scripted - declared - system_codes)
            if missing_in_data:
                report.findings.append(
                    Discrepancy(
                        qid=qid,
                        kind="option_not_scripted",
                        severity="warning",
                        detail=(
                            f"{qid} offers {missing_in_data} in the questionnaire but "
                            "the data map has no punch for them."
                        ),
                        variables=variables,
                    )
                )
            if extra_in_data:
                report.findings.append(
                    Discrepancy(
                        qid=qid,
                        kind="punch_not_in_questionnaire",
                        severity="warning",
                        detail=(
                            f"{qid} has punches {extra_in_data} in the data map that "
                            "the questionnaire never offers."
                        ),
                        variables=variables,
                    )
                )

        if scripted:
            counts["codes"] += 1
            # Judge the data against what was SCRIPTED. A value outside the
            # scripted punch list is a genuine data defect; a value outside the
            # extracted option list may only mean the extraction was wrong,
            # which the type/punch findings above already say once.
            offenders: dict[int, list[int]] = {}
            for index, row in enumerate(rows, start=1):
                for code in _selected_codes(row, variables, question):
                    if code not in scripted and code not in system_codes:
                        offenders.setdefault(code, []).append(index)
            if offenders:
                # ONE finding per question, listing the offending values. A
                # separate finding per value turned a single broken column into
                # fourteen entries and buried everything else.
                total = sorted({r for hits in offenders.values() for r in hits})
                values = ", ".join(str(c) for c in sorted(offenders)[:10])
                report.findings.append(
                    Discrepancy(
                        qid=qid,
                        kind="undeclared_code",
                        severity="error",
                        detail=(
                            f"Value(s) {values} appear in the data but are not in "
                            f"the scripted punch list for {qid}."
                        ),
                        n_rows=len(total),
                        variables=variables,
                        example_rows=total[:MAX_EXAMPLES],
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

    # --- Masking -----------------------------------------------------------
    # A question whose options are masked from an earlier answer may only hold
    # codes that respondent actually chose there. A mis-scripted mask shows the
    # wrong list to real people and is invisible in the output — no other check
    # in the pipeline can catch it.
    for qid, variables in sorted(link_map.items()):
        question = by_qid.get(qid)
        if question is None:
            continue
        masks = ((question.validation or {}).get("techniques") or {}).get("masks") or []
        for mask in masks:
            if mask.get("mode") != "selected":
                continue  # full_list / not_selected need the source's option list, not answers
            source = mask.get("source_qid")
            source_vars = link_map.get(source)
            source_question = by_qid.get(source)
            if not source_vars or source_question is None:
                report.skipped.append(
                    f"{qid}: masked from {source}, which is not linked to a variable"
                )
                continue
            counts["mask"] += 1
            hits = []
            for index, row in enumerate(rows, start=1):
                chosen = set(_selected_codes(row, source_vars, source_question))
                if not chosen:
                    continue  # never answered the source; nothing to mask against
                shown = set(_selected_codes(row, variables, question))
                if shown - chosen - set(system_codes):
                    hits.append(index)
            if hits:
                report.findings.append(
                    Discrepancy(
                        qid=qid,
                        kind="mask_violated",
                        severity="error",
                        detail=(
                            f"{qid} should only offer options the respondent selected at "
                            f"{source}, but some answered it with codes they did not choose "
                            "there."
                        ),
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

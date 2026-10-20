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
    terminate_violated a respondent the screen-out matched who kept answering

`show_if` routing is deliberately NOT re-checked here — `linker.cross_validate_
routing` already owns that and reports it in its own shape.

A note on trust: the terminate check is only as good as the extracted
`terminate_if`. Before September 2026 those conditions evaluated false for
every respondent, so this check would have reported a clean bill of health on
any file, for ever. That is why `checked` is reported alongside the findings —
"nothing found" and "nothing looked at" must never read the same.
"""

from __future__ import annotations

import re
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
from src.engines.qc.variable_roles import find_companions
from src.engines.questionnaire.linker import _answered, _grid_answer, _read_rows, _row_value
from src.engines.questionnaire.schema import FLAG_UNRESOLVED_ROUTING_QID

# How many offending row numbers to keep per finding. Enough to start looking,
# not so many that a systematically broken column produces a megabyte of noise.
MAX_EXAMPLES = 5

# Codes that appear in almost every real questionnaire as system values without
# being written into the response list. Flagging these would bury the real
# findings, which is how a check stops being read at all.
DEFAULT_SYSTEM_CODES = frozenset({-1, 0, 77, 88, 97, 98, 99, 998, 999, -99, -98})

# Types whose answer IS a code list. A number box or a text box is not one of
# them even when the questionnaire prints a few special codes beside it
# ("888. Still enrolled" next to a year entry) — the column is still an open
# response, so "the questionnaire has options, the data map says open" is only
# a contradiction for the types below.
# Extraction records the fields it could not resolve. A check must not assert
# on one of them: a question whose type is unknown is given `single` as a
# placeholder, and reporting "answered twice, but it is single-answer" off that
# placeholder blames the data for something we never actually read.
UNRESOLVED_FIELDS_KEY = "unresolved_fields"


def _type_is_unresolved(question: Question) -> bool:
    unresolved = (question.validation or {}).get(UNRESOLVED_FIELDS_KEY) or []
    return "type" in unresolved


CODED_QUESTION_TYPES = frozenset(
    {
        QuestionType.SINGLE,
        QuestionType.MULTI,
        QuestionType.GRID_SINGLE,
        QuestionType.DROPDOWN,
        QuestionType.RANK,
    }
)

# A non-response category recognised by what its label SAYS, not by the number
# it happens to carry. The conventional codes above only work when the file
# uses them; a label-encoded export (a CSV of answer text) has no conventional
# codes at all, and a data map sheet is free to put "Refused" anywhere. Every
# questionnaire omits these from its printed option list, so matching them by
# number alone turned each one into a finding a reviewer had to dismiss.
#
# The WHOLE label has to be the phrase. A prefix match reads "Refused a loan in
# the past 12 months" — a real answer in any credit study — as a refusal to
# answer, and quietly drops a genuine option from the comparison.
_NON_RESPONSE_PHRASE_RE = re.compile(
    r"^(?:"
    r"refus(?:ed|al)?|declined?(?:\s+to\s+answer)?|"
    r"(?:do\s+not|don['\u2019]?t)\s+know|dk|"
    r"no\s+answer|not\s+(?:asked|sure|stated|applicable|available)|"
    r"prefer(?:red)?\s+not\s+to\s+(?:answer|say)|"
    r"skipped?|missing|n\s*/\s*a"
    r")$",
    re.I,
)
# "(Don't know)" and "Refused." are the same category as "Refused".
_NON_RESPONSE_TRIM_RE = re.compile(r"^[\s(\[]+|[\s).\]]+$")
# "Don't know / Refused" is one category written as two phrases.
_NON_RESPONSE_SPLIT_RE = re.compile(r"\s*(?:/|,|;|\bor\b)\s*", re.I)


def _is_non_response(label: str) -> bool:
    """True when a value label states a refusal / don't-know, not an answer."""
    cleaned = _NON_RESPONSE_TRIM_RE.sub("", label or "")
    if not cleaned:
        return False
    parts = [p for p in _NON_RESPONSE_SPLIT_RE.split(cleaned) if p]
    return bool(parts) and all(_NON_RESPONSE_PHRASE_RE.match(p) for p in parts)


def _non_response_codes(labelled: list[tuple[int, str]]) -> set[int]:
    """Codes whose label states they are a refusal / don't-know, not an answer."""
    return {code for code, label in labelled if _is_non_response(label)}


def _datamap_labelled_codes(
    datamap: DataMap | None, variables: list[str]
) -> list[tuple[int, str]]:
    """(code, label) for every value the data map declares on these columns."""
    if datamap is None:
        return []
    by_name = {v.name: v for v in datamap.variables}
    out: list[tuple[int, str]] = []
    for name in variables:
        variable = by_name.get(name)
        if variable is not None and variable.values:
            out.extend(variable.values.items())
    return out


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


def _downstream_variables(
    survey: SurveyDefinition, link_map: dict[str, list[str]], qid: str
) -> list[str]:
    """Every linked column belonging to a question asked AFTER `qid`.

    A screen-out is a promise about what happens next, so checking it needs to
    know what "next" is. Questions are taken in the order the instrument
    presents them (`question_order`), not the order the file happens to store
    its columns in.
    """
    order = survey.question_order()
    if qid not in order:
        return []
    return [
        name
        for later in order[order.index(qid) + 1 :]
        for name in link_map.get(later, [])
    ]


_DISPOSITION_WORDS = (
    ("terminated", re.compile(r"terminat|screen|disqualif|\bterm\b", re.I)),
    ("overquota", re.compile(r"over\s*quota|quota\s*full", re.I)),
    # Before "complete": "Incomplete" contains it.
    ("partial", re.compile(r"partial|incomplete|drop|abandon", re.I)),
    ("complete", re.compile(r"qualified|complete", re.I)),
)


def _disposition_labels(datamap: DataMap | None) -> tuple[str | None, dict[int, str]]:
    """The respondent-status column and what each of its codes means.

    Decipher exports `status` with 1 Terminated / 2 Overquota / 3 Qualified /
    4 Partial. Without it, a respondent the script screened out was judged by
    whether ANY later column was filled — and paradata is filled for everyone.
    """
    if datamap is None:
        return None, {}
    for variable in datamap.variables:
        if variable.name.lower() not in ("status", "disposition", "dispcode", "resp_status"):
            continue
        meaning: dict[int, str] = {}
        for code, label in (variable.values or {}).items():
            for outcome, pattern in _DISPOSITION_WORDS:
                # "Disqualified" is a terminate, not a qualified complete.
                if pattern.search(str(label)):
                    meaning[int(code)] = outcome
                    break
        if meaning:
            return variable.name, meaning
    return None, {}


def completes_only(df, datamap: DataMap | None):
    """(the completes, True) when the file has a status column, else (df, False).

    A fielded export holds every interview; quality is judged on the completes,
    the data that gets delivered.
    """
    import polars as pl

    status_column, meaning = _disposition_labels(datamap)
    codes = [code for code, outcome in meaning.items() if outcome == "complete"]
    if not (status_column and status_column in df.columns and codes):
        return df, False
    return df.filter(pl.col(status_column).is_in(codes)), True


def _as_code(value: object) -> int | None:
    number = _as_number(value)
    return int(number) if number is not None and float(number).is_integer() else None


_UNREAD_FLAGS = frozenset(
    {"EXTRACTION_UNAVAILABLE", "EXTRACTION_CONFIG_ERROR", "EXTRACTION_MODEL_ERROR"}
)
_LIST_RANGE = re.compile(r"(\d{1,3})\s*[-–]\s*(\d{1,3})\s*$")


def _borrowed_codes(question: Question, by_qid: dict) -> set[int]:
    """Codes the question offers by taking them from another question.

    "[INSERT CURRENTLY USED BRANDS FROM DA_USAGE 9-13]" adds codes 9 to 13 to
    the list. They are offered, just not printed, and reporting them as
    "never offered" made every piped list a finding."""
    codes: set[int] = set()
    techniques = (question.validation or {}).get("techniques") or {}
    for mask in techniques.get("masks") or []:
        span = _LIST_RANGE.search(str(mask.get("verbatim") or ""))
        if span:
            codes |= set(range(int(span.group(1)), int(span.group(2)) + 1))
        source = by_qid.get(mask.get("source_qid") or "")
        # A question printing its own list ("SHOW CORE BRANDS … ONLY THOSE
        # AWARE AT Q2a") filters it; only a list-less one takes the source's.
        if source is not None and not question.options:
            codes |= {o.code for o in source.options or []}
    return codes


def _declared_codes(question: Question) -> set[int]:
    return {o.code for o in question.options or []}


def _numeric_range(question: Question) -> tuple[float | None, float | None]:
    """The stated min/max for a numeric question, if it has one."""
    validation = question.validation or {}
    lo = validation.get("min", validation.get("minimum"))
    hi = validation.get("max", validation.get("maximum"))
    return _as_number(lo), _as_number(hi)


_PUNCH_CODE = re.compile(r"r(\d+)$")


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
    # "DA_SUCCESSr9" IS code 9, and `_row_value` already returns it. Mapping a
    # position through the DOCUMENT's list went wrong whenever the two number
    # differently (a document that skips 4 shifted every answer by one, and 35
    # respondents were reported for an answer they had ticked).
    if variables and all(_PUNCH_CODE.search(v) for v in variables):
        return [int(c) for c in value]
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
        # A loop pass (Q6_1 stored as Q6_Lr1r3) does not start with its qid;
        # a 0/1 tick box still names its code in the r<code> ending.
        punch = _PUNCH_CODE.search(name)
        tick = (punch and len(variables) > 1
                and set((variable.values or {}).keys()) <= {0, 1})
        if suffix.isdigit() or tick:
            described = True
            codes.add(int(suffix) if suffix.isdigit() else int(punch.group(1)))
        elif variable.values:
            described = True
            codes |= set(variable.values.keys())
    return codes if described else None


def _same_answer_codes(question: Question, source: Question) -> dict[int, int]:
    """This question's code -> the source's code for the same answer, by label.

    A masked list is often renumbered: Q6 shows the core brands as 1-10 while
    Q2a, the list it is masked from, numbers the same brands 1, 5, 14, ...
    Comparing raw codes blamed 933 respondents for answers they did give.
    """
    def key(label: str) -> str:
        return " ".join(re.sub(r"[^a-z0-9]+", " ", (label or "").lower()).split())

    by_label = {key(o.label): o.code for o in source.options or [] if key(o.label)}
    return {o.code: by_label[key(o.label)] for o in question.options or []
            if key(o.label) in by_label}


def _answer_rules(question: Question, variables: list[str], rows: list[dict],
                  report: DiscrepancyReport, counts: dict) -> None:
    """Rules the questionnaire states about a respondent's own answers.

    An exclusive answer ticked with others, more answers than "select up to N"
    allows, number boxes that miss the stated total. Each is exact: the rule is
    printed in the document and the data either keeps it or not.
    """
    validation = question.validation or {}
    tick_boxes = len(variables) > 1 and all(_PUNCH_CODE.search(v) for v in variables)
    if question.type is QuestionType.MULTI and tick_boxes:
        exclusive = {o.code for o in question.options or [] if o.exclusive}
        if exclusive:
            counts["exclusive"] += 1
            hits = [i for i, row in enumerate(rows, start=1)
                    if (chosen := set(_selected_codes(row, variables, question))) & exclusive
                    and len(chosen) > 1]
            if hits:
                labels = [o.label for o in question.options if o.code in exclusive]
                report.findings.append(Discrepancy(
                    qid=question.qid, kind="exclusive_violated", severity="error",
                    detail=(f"{question.qid}: \u201c{labels[0]}\u201d is exclusive, but some "
                            "respondents ticked it together with other answers."),
                    n_rows=len(hits), variables=variables, example_rows=hits[:MAX_EXAMPLES],
                ))
        cap = validation.get("max_select")
        if isinstance(cap, int) and cap > 0:
            counts["max_select"] += 1
            hits = [i for i, row in enumerate(rows, start=1)
                    if len(_selected_codes(row, variables, question)) > cap]
            if hits:
                report.findings.append(Discrepancy(
                    qid=question.qid, kind="too_many_answers", severity="error",
                    detail=f"{question.qid} allows up to {cap} answers; some gave more.",
                    n_rows=len(hits), variables=variables, example_rows=hits[:MAX_EXAMPLES],
                ))
    total = validation.get("sum")
    if isinstance(total, (int, float)) and len(variables) > 1:
        counts["sum"] += 1
        hits = []
        for i, row in enumerate(rows, start=1):
            values = [_as_number(row.get(v)) for v in variables]
            values = [v for v in values if v is not None]
            if values and abs(sum(values) - total) > 0.5:
                hits.append(i)
        if hits:
            report.findings.append(Discrepancy(
                qid=question.qid, kind="sum_not_total", severity="error",
                detail=f"{question.qid}'s boxes must add up to {_bound_text(total)}; some do not.",
                n_rows=len(hits), variables=variables, example_rows=hits[:MAX_EXAMPLES],
            ))


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
    status_column, dispositions = _disposition_labels(datamap)
    if status_column:
        columns.append(status_column)
    rows = _read_rows(Path(parquet_path), columns)
    report.n_rows = len(rows)
    if not rows:
        report.skipped.append("data file has no rows")
        return report

    counts = {
        "codes": 0,
        "single": 0,
        "range": 0,
        "terminate": 0,
        "mask": 0,
        "pipe": 0,
        "exclusive": 0,
        "max_select": 0,
        "sum": 0,
    }
    companions = find_companions(datamap) if datamap is not None else {}

    for question in survey.questions:
        if FLAG_UNRESOLVED_ROUTING_QID in (question.flags or ()):
            # GA_META_ADV_SPEC's base names DA_META_ADV, which exists nowhere:
            # no base to test, and silence would read as "base held".
            report.skipped.append(
                f"{question.qid}: its base names a question that is in neither the "
                "questionnaire nor the data, so who should answer it was not checked"
            )

    for qid, variables in sorted(link_map.items()):
        question = by_qid.get(qid)
        if question is None:
            continue
        if set(question.flags or ()) & _UNREAD_FLAGS:
            # The parser could not read this question, so its "options" are
            # not the document's. Comparing them to the data blames the data
            # for a reading that never happened.
            report.skipped.append(f"{qid}: not compared — the questionnaire text could not be read")
            continue

        declared = _declared_codes(question) | _borrowed_codes(question, by_qid)
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
        if (
            declared
            and datamap_says_open
            and question.type in CODED_QUESTION_TYPES
            and not _type_is_unresolved(question)
        ):
            # An open-ended question usually has a derived companion holding
            # the bands or flags the conditions are actually written against
            # ("SS_AD_SPEND" is a number; "HID_SS_AD_SPEND" flags spend over
            # $1,000,000). When one exists, "the column has no codes" and "the
            # questionnaire lists bands" are both true and not a contradiction
            # — so say where the codes live instead of calling it a mistake.
            coded_companions = [
                c for c in companions.get(qid, []) if c.role in ("hidden", "no_answer")
            ]
            if coded_companions:
                report.findings.append(
                    Discrepancy(
                        qid=qid,
                        kind="coded_in_companion",
                        severity="info",
                        detail=(
                            f"{qid} is collected as an open response; the codes the "
                            "questionnaire lists are held by "
                            + ", ".join(c.name for c in coded_companions)
                            + ". Conditions should be written against those."
                        ),
                        variables=[c.name for c in coded_companions],
                    )
                )
            else:
                report.findings.append(
                    Discrepancy(
                        qid=qid,
                        kind="type_mismatch",
                        severity="warning",
                        detail=(
                            f"{qid} was read as a coded question with options "
                            f"{sorted(declared)[:8]}, but the data map describes "
                            "its column as an open response, and no derived "
                            "variable holds those codes."
                        ),
                        variables=variables,
                    )
                )
        elif declared and datamap is not None and scripted is not None:
            # Punches vs questionnaire: what was scripted against what was asked.
            # "Refused" living on either side is not a disagreement about the
            # instrument — no questionnaire prints it and every data file has it.
            ignorable = (
                system_codes
                | _non_response_codes(_datamap_labelled_codes(datamap, variables))
                | _non_response_codes([(o.code, o.label) for o in question.options])
            )
            missing_in_data = sorted(declared - scripted - ignorable)
            extra_in_data = sorted(scripted - declared - ignorable)
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

        _answer_rules(question, variables, rows, report, counts)

        if question.type is QuestionType.SINGLE and len(variables) > 1:
            held = {_as_number(row.get(v)) for row in rows for v in variables} - {None}
            if not held <= {0.0, 1.0}:
                # MAXDIFF_ANCHOR_1/_2/_3 hold 1, 2, 3: one ANSWER per screen or
                # loop turn, each in its own column — not 0/1 tick boxes.
                report.skipped.append(
                    f"{qid}: single-vs-multi not checked — its columns are repeats "
                    "(one answer each), not tick boxes"
                )
                continue
            if _type_is_unresolved(question):
                report.skipped.append(
                    f"{qid}: single-vs-multi not checked — the extraction could "
                    "not read this question's type"
                )
                continue
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
    for question in survey.questions:
        if FLAG_UNRESOLVED_ROUTING_QID in (question.flags or ()):
            # GA_META_ADV_SPEC's base names DA_META_ADV, which exists nowhere:
            # no base to test, and silence would read as "base held".
            report.skipped.append(
                f"{question.qid}: its base names a question that is in neither the "
                "questionnaire nor the data, so who should answer it was not checked"
            )

    for qid, variables in sorted(link_map.items()):
        question = by_qid.get(qid)
        if question is None:
            continue
        techniques = (question.validation or {}).get("techniques") or {}
        for mask in techniques.get("masks") or []:
            mode = mask.get("mode")
            source = mask.get("source_qid")
            source_vars = link_map.get(source)
            source_question = by_qid.get(source)
            if not source_vars or source_question is None:
                report.skipped.append(
                    f"{qid}: masked from {source}, which is not linked to a variable"
                )
                continue
            if mode == "full_list":
                # No per-respondent restriction to test: everyone sees the same
                # list. An answer outside it is an undeclared code, which the
                # codes check above already reports. Said out loud so this does
                # not read as a mask that was verified.
                report.skipped.append(
                    f"{qid}: shows {source}'s full list, so there is no "
                    "per-respondent restriction to check"
                )
                continue

            exclude_qid = mask.get("exclude_qid")
            exclude_vars = link_map.get(exclude_qid) if exclude_qid else None
            exclude_question = by_qid.get(exclude_qid) if exclude_qid else None
            if mode == "list_except" and (not exclude_vars or exclude_question is None):
                report.skipped.append(
                    f"{qid}: excludes what was chosen at {exclude_qid}, which is not "
                    "linked to a variable"
                )
                continue

            if source_question is not None and source_question.type is QuestionType.GRID_SINGLE:
                # "ONLY OF PHARMACIES USED AT Q4A" where Q4a is a grid of 1/2
                # answers: which answer means "used" is not something the check
                # can read, and guessing reported 641 respondents of one study.
                report.skipped.append(
                    f"{qid}: list comes from the grid {source}; which of its answers "
                    "admits a row is not stated, so it was not checked"
                )
                continue
            counts["mask"] += 1
            other = exclude_question if mode == "list_except" else source_question
            same_answer = _same_answer_codes(question, other)
            grid_rows = (question.type is QuestionType.GRID_SINGLE
                         and all(_PUNCH_CODE.search(v) for v in variables))
            if grid_rows:
                same_answer = _same_answer_codes(
                    Question(qid=qid, text="", type=QuestionType.MULTI,
                             options=list(question.rows or [])), other)
            hits = []
            for index, row in enumerate(rows, start=1):
                if grid_rows:
                    # A masked GRID shows the chosen ROWS; the answers in them
                    # are ratings, not codes of the source list.
                    shown = set(_grid_answer(row, variables) or ()) - set(system_codes)
                else:
                    shown = set(_selected_codes(row, variables, question)) - set(system_codes)
                if not shown:
                    continue  # never answered this question
                shown = {same_answer.get(code, code) for code in shown}
                if mode == "list_except":
                    barred = set(_selected_codes(row, exclude_vars, exclude_question))
                    if shown & barred:
                        hits.append(index)
                    continue
                if not _answered(row, source_vars):
                    continue  # never answered the source; nothing to mask against
                # Answered it but chose none of the list: nothing could be shown,
                # so any answer here is outside the mask (Vivo Q18a).
                chosen = set(_selected_codes(row, source_vars, source_question))
                # `selected` may only hold what they chose there; `not_selected`
                # may hold only what they did NOT. Both are the same question
                # asked from opposite sides.
                offending = shown - chosen if mode == "selected" else shown & chosen
                if offending:
                    hits.append(index)
            if hits:
                described = {
                    "selected": (
                        f"should only offer options the respondent selected at {source}, "
                        "but some answered it with codes they did not choose there"
                    ),
                    "not_selected": (
                        f"should only offer options the respondent did NOT select at "
                        f"{source}, but some answered it with codes they did choose there"
                    ),
                    "list_except": (
                        f"should exclude whatever the respondent chose at {exclude_qid}, "
                        "but some answered it with exactly those codes"
                    ),
                }[mode]
                report.findings.append(
                    Discrepancy(
                        qid=qid,
                        kind="mask_violated",
                        severity="error",
                        detail=f"{qid} {described}.",
                        n_rows=len(hits),
                        variables=variables,
                        example_rows=hits[:MAX_EXAMPLES],
                    )
                )

    # --- Piping ------------------------------------------------------------
    # A piped question shows an earlier answer inside its own wording. When the
    # source is empty the respondent is shown a sentence with a hole in it —
    # "How satisfied are you with ?" — which no other check can see, because
    # every column involved looks perfectly valid on its own.
    for question in survey.questions:
        if FLAG_UNRESOLVED_ROUTING_QID in (question.flags or ()):
            # GA_META_ADV_SPEC's base names DA_META_ADV, which exists nowhere:
            # no base to test, and silence would read as "base held".
            report.skipped.append(
                f"{question.qid}: its base names a question that is in neither the "
                "questionnaire nor the data, so who should answer it was not checked"
            )

    for qid, variables in sorted(link_map.items()):
        question = by_qid.get(qid)
        if question is None:
            continue
        for pipe in ((question.validation or {}).get("techniques") or {}).get("pipes") or []:
            source = pipe.get("source_qid")
            if not source:
                report.skipped.append(
                    f"{qid}: pipes in '{pipe.get('name') or 'a value'}', which the "
                    "document names in scripter terms rather than as a question id"
                )
                continue
            source_vars = link_map.get(source)
            if not source_vars:
                report.skipped.append(
                    f"{qid}: pipes from {source}, which is not linked to a variable"
                )
                continue
            counts["pipe"] += 1
            hits = [
                index
                for index, row in enumerate(rows, start=1)
                if _answered(row, variables) and not _answered(row, source_vars)
            ]
            if hits:
                report.findings.append(
                    Discrepancy(
                        qid=qid,
                        kind="pipe_source_empty",
                        severity="error",
                        detail=(
                            f"{qid} pipes the answer to {source} into its own wording, but "
                            "some respondents answered it with nothing recorded at "
                            f"{source} — they were shown a gap where the piped text "
                            "should have been."
                        ),
                        n_rows=len(hits),
                        variables=variables + source_vars,
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
        # A screen-out makes two promises, and only the second one is a defect
        # when it breaks. A respondent who matches it MAY legitimately sit in
        # the file — plenty of exports keep the terminates — as long as the
        # interview actually stopped. What must never happen is a respondent who
        # matched the screen-out and then went on answering.
        #
        # Reporting mere presence as an error made every recorded terminate a
        # finding, which on a file that keeps its terminates is a flood of red
        # for a survey that behaved perfectly.
        downstream = _downstream_variables(survey, link_map, qid)
        continued: list[int] = []
        stopped: list[int] = []
        for index, row in enumerate(rows, start=1):
            answers = {
                ref: _row_value(row, link_map[ref])
                for ref in referenced
                if link_map.get(ref)
            }
            if any(value in (None, "", []) for value in answers.values()):
                continue  # not answered: "1 not selected" says nothing about a blank
            if not evaluate(question.terminate_if, answers):
                continue
            outcome = dispositions.get(_as_code(row.get(status_column))) if status_column else None
            if outcome in ("terminated", "overquota"):
                # The file itself says the interview ended here: the screen-out
                # held, whatever system columns were filled in afterwards.
                stopped.append(index)
            elif outcome == "complete":
                # Matched the screen-out and still finished the survey.
                continued.append(index)
            elif downstream and _answered(row, downstream):
                continued.append(index)
            else:
                stopped.append(index)

        if continued:
            report.findings.append(
                Discrepancy(
                    qid=qid,
                    kind="terminate_violated",
                    severity="error",
                    detail=(
                        f"{qid} screens these respondents out, but they went on to "
                        "answer later questions — the screen-out did not stop the "
                        "interview."
                    ),
                    n_rows=len(continued),
                    variables=link_map.get(qid, []),
                    example_rows=continued[:MAX_EXAMPLES],
                )
            )
        if stopped:
            # They matched the screen-out and are in the file. With a later
            # question to look at, we can say the interview stopped where it
            # should have — good news, reported so a reviewer can see the check
            # ran. With nothing after it linked to a column, we cannot say, and
            # a warning is the only honest grade for "did not stop, or did?".
            verified = bool(downstream)
            report.findings.append(
                Discrepancy(
                    qid=qid,
                    kind="terminate_recorded",
                    severity="info" if verified else "warning",
                    detail=(
                        f"{len(stopped)} respondents match {qid}'s screen-out and "
                        + (
                            "have no answers after it — the screen-out held. Expected "
                            "only if this export keeps its terminates."
                            if verified
                            else "are present in the file, but no later question is "
                            "linked to a column, so whether the interview actually "
                            "stopped could not be checked."
                        )
                    ),
                    n_rows=len(stopped),
                    variables=link_map.get(qid, []),
                    example_rows=stopped[:MAX_EXAMPLES],
                )
            )

    report.checked = {k: v for k, v in counts.items() if v}
    # Worst first: a researcher reads the top of this list and stops.
    report.findings.sort(key=lambda f: (-f.n_rows, f.qid))
    return report

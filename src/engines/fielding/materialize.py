"""Materialize captured fielding responses into a DataMap + wide DataFrame.

The T11 payoff (see 13-Questionnaire-Collector-Plan.md, key decision #1): T9'
captures answers into Postgres one row per (session, qid); this module turns the
PINNED `SurveyDefinition` snapshot + those sessions/answers into the *exact*
artifacts the rest of the stack already consumes — a respondent-level DataFrame
(one row per complete) and a `DataMap` — so crosstabs, the navigator, QC, and
coding light up on fielded data with no changes to those engines.

Pure transformation, NO HTTP and NO DB: the caller passes already-loaded
sessions and an answers map. This mirrors the adapter contract in
`src/adapters/tabular.py` (external data -> DataMap + pandas DataFrame) and is
fully unit-testable.

Per question type (`statuses` selects which dispositions become rows —
completes only by default, every disposition for a delivery export):
  single / dropdown -> one SINGLE variable named the qid; numeric codes; value
      labels from Options; variable label = question text.
  multi -> exploded 0/1 dummies `{qid}r{code}` (Decipher multi-punch naming so
      questionnaire<->data linking works for free — see linker.py); labels
      "{question text} — {option label}"; value labels {0: Not selected,
      1: Selected}.
  numeric -> NUMERIC, no value labels.
  open_text -> TEXT (how the coding engine detects open-ends: v.type == TEXT).
  grid_single -> one SINGLE variable per row `{qid}r{row.code}`, value labels
      from the columns; variable label "{question text} — {row label}".
  info -> nothing.

Missing answers (show_if-skipped, unanswered) -> null, matching the adapters'
null-missing representation (a skipped question simply has no answer row).
Paradata columns (session start/end/duration) are named with camelCase `v`
prefixes that `orchestrator._is_paradata_variable` recognizes, so they're kept
out of stub/banner candidacy.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd

from src.core.models import DataMap, Variable, VarType
from src.core.survey_definition import Question, QuestionType, SurveyDefinition

STATUS_COMPLETE = 10  # Lucid/Cint convention (fielding key decision #7)
STATUS_TERMINATE = 20
STATUS_SECURITY = 30
STATUS_OVERQUOTA = 40

#: What each disposition means, as value labels on the exported status column.
STATUS_LABELS = {
    STATUS_COMPLETE: "Complete",
    STATUS_TERMINATE: "Screened out",
    STATUS_SECURITY: "Security screen-out",
    STATUS_OVERQUOTA: "Over quota",
}
ALL_STATUSES = frozenset(STATUS_LABELS)

# camelCase v-prefixed names match orchestrator._CAMEL_SYSTEM_RE (^[dvh][A-Z]),
# so _is_paradata_variable (banner) AND _is_system_variable (stub) both skip
# them — paradata is never mistaken for a real question.
PARA_START = "vStartTime"
PARA_END = "vEndTime"
PARA_DURATION = "vDurationSec"
PARA_STATUS = "vStatus"


@dataclass
class _ColumnSpec:
    """One emitted column: its Variable plus how to read it from an answers map."""

    variable: Variable
    read: Any  # callable(answers: dict[qid -> value]) -> cell value (or None)


def _to_int(value: Any) -> int | None:
    """Coerce a stored answer to an int code, or None when it can't be one.

    SurveyJS may serialize a choice value as "1" or 1; both map to 1. Anything
    non-integral (blank, free text, a list) yields None -> null cell.
    """
    if value is None or isinstance(value, bool):
        return int(value) if isinstance(value, bool) else None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _selected_codes(value: Any) -> set[int]:
    """The set of chosen option codes from a multi answer (a list of codes)."""
    if not isinstance(value, (list, tuple, set)):
        return set()
    out: set[int] = set()
    for item in value:
        code = _to_int(item)
        if code is not None:
            out.add(code)
    return out


def _grid_cell(value: Any, row_code: int) -> int | None:
    """The column code a grid row was answered with (value is a {row: col} dict)."""
    if not isinstance(value, Mapping):
        return None
    # Answer keys may be ints or their string forms depending on the client.
    raw = value.get(row_code)
    if raw is None:
        raw = value.get(str(row_code))
    return _to_int(raw)


def _specs_for_question(question: Question) -> list[_ColumnSpec]:
    """Emit the DataMap column spec(s) for one question (empty for info)."""
    qid = question.qid
    text = question.text or qid
    qtype = question.type

    if qtype in (QuestionType.SINGLE, QuestionType.DROPDOWN):
        values = {o.code: o.label for o in question.options}
        var = Variable(
            name=qid,
            label=text,
            type=VarType.SINGLE,
            values=values,
            question_ref=qid,
            question_text=text,
            provenance="fielding",
        )
        return [_ColumnSpec(var, lambda a, q=qid: _to_int(a.get(q)))]

    if qtype == QuestionType.MULTI:
        specs: list[_ColumnSpec] = []
        for opt in question.options:
            var = Variable(
                name=f"{qid}r{opt.code}",
                label=f"{text} — {opt.label}",
                type=VarType.MULTI,
                values={0: "Not selected", 1: "Selected"},
                question_ref=qid,
                question_text=text,
                provenance="fielding",
            )

            def read(a, q=qid, code=opt.code):
                if q not in a:  # question not shown/answered -> null, not 0
                    return None
                return 1 if code in _selected_codes(a.get(q)) else 0

            specs.append(_ColumnSpec(var, read))
        return specs

    if qtype == QuestionType.NUMERIC:
        var = Variable(
            name=qid,
            label=text,
            type=VarType.NUMERIC,
            question_ref=qid,
            question_text=text,
            provenance="fielding",
        )
        return [_ColumnSpec(var, lambda a, q=qid: _to_float(a.get(q)))]

    if qtype == QuestionType.OPEN_TEXT:
        var = Variable(
            name=qid,
            label=text,
            type=VarType.TEXT,
            question_ref=qid,
            question_text=text,
            provenance="fielding",
        )

        def read_text(a, q=qid):
            raw = a.get(q)
            return str(raw) if raw is not None else None

        return [_ColumnSpec(var, read_text)]

    if qtype == QuestionType.GRID_SINGLE:
        col_labels = {c.code: c.label for c in (question.cols or [])}
        specs = []
        for row in question.rows or []:
            var = Variable(
                name=f"{qid}r{row.code}",
                label=f"{text} — {row.label}",
                type=VarType.SINGLE,
                values=dict(col_labels),
                question_ref=qid,
                question_text=text,
                row=str(row.code),
                provenance="fielding",
            )
            specs.append(
                _ColumnSpec(var, lambda a, q=qid, rc=row.code: _grid_cell(a.get(q), rc))
            )
        return specs

    # info (display-only) and any unknown/unsupported type -> no column.
    return []


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _paradata_variables() -> list[Variable]:
    """Session status/start/end/duration columns (built row-wise from sessions)."""
    return [
        # The disposition. Without it an export of every outcome is unreadable —
        # a terminate and a complete look identical once the answers stop — and
        # incidence, the number a client is actually buying, is not computable
        # from a file of completes alone.
        Variable(name=PARA_STATUS, label="Disposition", type=VarType.SINGLE,
                 values=dict(STATUS_LABELS), provenance="fielding_paradata"),
        Variable(name=PARA_START, label="Interview start time", type=VarType.DATE,
                 provenance="fielding_paradata"),
        Variable(name=PARA_END, label="Interview end time", type=VarType.DATE,
                 provenance="fielding_paradata"),
        Variable(name=PARA_DURATION, label="Interview duration (seconds)",
                 type=VarType.NUMERIC, provenance="fielding_paradata"),
    ]


def _pandas_series(spec_type: VarType, data: list[Any]) -> pd.Series:
    """Build a nullable-typed Series so missing -> parquet null (not NaN).

    NaN in a float column is NOT NULL in parquet, and the DuckDB crosstab filters
    on `IS NOT NULL` — nullable dtypes (Int64/Float64/string with pd.NA) are what
    make skipped/absent answers read back as genuine nulls downstream.
    """
    if spec_type in (VarType.SINGLE, VarType.MULTI):
        return pd.Series(data, dtype="Int64")
    if spec_type == VarType.NUMERIC:
        return pd.Series(data, dtype="Float64")
    # TEXT and DATE (ISO strings) both ride as nullable strings.
    return pd.Series([None if v is None else str(v) for v in data], dtype="string")


def materialize_fielding(
    survey: SurveyDefinition,
    sessions: Iterable[Any],
    answers: Mapping[str, Mapping[str, Any]],
    statuses: frozenset[int] = frozenset({STATUS_COMPLETE}),
) -> tuple[DataMap, pd.DataFrame]:
    """Turn respondent sessions into a (DataMap, DataFrame) pair.

    Args:
        survey: the PINNED SurveyDefinition snapshot (from FieldingLinkDB.survey_json).
        sessions: respondent session objects (need `.id`, `.status_code`,
            `.started_at`, `.completed_at`).
        answers: {session_id: {qid: value}} — value is a scalar code, a list
            (multi), a number, text, or a {row: col} grid dict.
        statuses: which dispositions become rows. Defaults to completes only,
            which is what the ANALYSIS artifacts want — a topline computed over
            screened-out respondents is simply wrong. Pass `ALL_STATUSES` for a
            delivery export: the terminates are the evidence the screen-outs
            worked and the denominator of the incidence rate, and discarding
            them means our own screen-out check can never see them.

    Returns a DataMap describing every emitted column and a wide pandas
    DataFrame with one row per included respondent (plus respondent_id and
    paradata columns).
    """
    completes = [s for s in sessions if getattr(s, "status_code", None) in statuses]
    # Deterministic order: by start time then id, so respondent_id is stable
    # across re-materializations.
    completes.sort(key=lambda s: (str(getattr(s, "started_at", "") or ""), str(s.id)))

    # Column specs: respondent_id, then questions in instrument order, then paradata.
    question_specs: list[_ColumnSpec] = []
    for qid in survey.question_order():
        if qid in survey:
            question_specs.extend(_specs_for_question(survey[qid]))

    datamap = DataMap()
    rid_var = Variable(
        name="respondent_id", label="Respondent ID", type=VarType.NUMERIC,
        provenance="fielding",
    )
    datamap.add(rid_var)
    for spec in question_specs:
        datamap.add(spec.variable)
    for var in _paradata_variables():
        datamap.add(var)

    # Build columns row-aligned with `completes`.
    columns: dict[str, pd.Series] = {}
    rids = list(range(1, len(completes) + 1))
    columns["respondent_id"] = pd.Series(rids, dtype="int64")

    for spec in question_specs:
        cells = [spec.read(answers.get(s.id, {})) for s in completes]
        columns[spec.variable.name] = _pandas_series(spec.variable.type, cells)

    starts, ends, durations, dispositions = [], [], [], []
    for s in completes:
        start = _parse_iso(getattr(s, "started_at", None))
        end = _parse_iso(getattr(s, "completed_at", None))
        starts.append(start.isoformat() if start else None)
        ends.append(end.isoformat() if end else None)
        durations.append((end - start).total_seconds() if start and end else None)
        dispositions.append(getattr(s, "status_code", None))
    columns[PARA_STATUS] = _pandas_series(VarType.SINGLE, dispositions)
    columns[PARA_START] = _pandas_series(VarType.DATE, starts)
    columns[PARA_END] = _pandas_series(VarType.DATE, ends)
    columns[PARA_DURATION] = _pandas_series(VarType.NUMERIC, durations)

    df = pd.DataFrame(columns, columns=[v.name for v in datamap.variables])
    return datamap, df

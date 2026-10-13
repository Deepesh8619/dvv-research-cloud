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

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd

from src.core.models import DataMap, Variable, VarType
from src.core.survey_definition import (
    Question,
    QuestionType,
    SurveyDefinition,
    evaluate,
)
from src.engines.fielding.paradata import DEVICE_CODES
from src.engines.fielding.paradata import summarise as summarise_paradata
from src.engines.fielding.rotation import ROTATION_VARIABLE, rotating_blocks

logger = logging.getLogger(__name__)

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
# What the interview was taken on, and whether the respondent's stated place
# agrees with their connection. Both are questions a client asks of every
# study and neither can be answered after the fact.
PARA_DEVICE = "vDevice"
PARA_PLACE = "vPlaceCheck"
PARA_COUNTRY = "vCountry"
PARA_REGION = "vRegion"
PARA_CITY = "vCity"
PARA_POSTAL = "vPostcode"
PARA_IP_HASH = "vConnection"
# Stable key: respondent_id is renumbered 1..N on every materialize.
PARA_SESSION = "vSessionId"


@dataclass
class _ColumnSpec:
    """One emitted column: its Variable plus how to read it from an answers map."""

    variable: Variable
    read: Any  # callable(answers: dict[qid -> value]) -> cell value (or None)


def _to_int(value: Any) -> int | None:
    if isinstance(value, list):
        # A single-coded column holding a list: one punch is the same answer
        # written as a list, so keep it. Several punches means the instrument
        # says single and the link collected multi — say so instead of writing a
        # silent NULL, which is how a whole question's data disappears.
        if len(value) == 1:
            return _to_int(value[0])
        if value:
            logger.warning(f"single-coded column received {len(value)} punches: {value!r}")
        return None
    return _to_int_scalar(value)


def _to_int_scalar(value: Any) -> int | None:
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

    if qtype == QuestionType.NUMERIC and question.rows:
        # A constant sum: one number column per item, named like a grid's rows.
        return [
            _ColumnSpec(
                Variable(
                    name=f"{qid}r{row.code}",
                    label=f"{text} — {row.label}",
                    type=VarType.NUMERIC,
                    question_ref=qid,
                    question_text=text,
                    row=str(row.code),
                    provenance="fielding",
                ),
                lambda a, q=qid, rc=row.code: _to_float(_grid_cell(a.get(q), rc)),
            )
            for row in question.rows
        ]

    if qtype == QuestionType.NUMERIC:
        var = Variable(
            name=qid,
            label=text,
            type=VarType.NUMERIC,
            question_ref=qid,
            question_text=text,
            provenance="fielding",
            # "99: Prefer not to answer": labelled, and declared missing so a
            # mean of ages is not dragged up by everyone who declined.
            values={o.code: o.label for o in question.options},
            missing_values=[o.code for o in question.options],
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

    if qtype == QuestionType.INFO and question.options and _is_derived(question):
        # A coded hidden variable: "CODE AGE" with its bands under it. Nobody is
        # asked it — the script computes it from an earlier answer — but it is a
        # COLUMN, and every banner cut by age band reads it. Typed info it
        # emitted nothing at all, so the variable the tables are built on was
        # simply absent from the file.
        var = Variable(
            name=qid,
            label=text,
            type=VarType.SINGLE,
            values={o.code: o.label for o in question.options},
            question_ref=qid,
            question_text=text,
            provenance="derived",
        )
        return [_ColumnSpec(var, lambda a, q=qid: _to_int(a.get(q)))]

    if qtype == QuestionType.INFO and qid.upper().startswith("HID_") and question.show_if:
        # A hidden variable IS a column — it is what banners are cut by. It has
        # no respondent-facing answer, so it is computed from the condition the
        # extractor already read ("(IF S0=1 AND S1=26-60) ASK P1-P3"), per
        # respondent, against the answers we hold. Only conditions WE parsed are
        # computed; nothing is guessed.
        var = Variable(
            name=qid,
            label=text,
            type=VarType.SINGLE,
            values={0: "Not in path", 1: "In path"},
            question_ref=qid,
            question_text=text,
            provenance="fielding_derived",
        )

        def in_path(a, expression=question.show_if):
            if not expression.referenced_qids().issubset(a.keys()):
                return None  # not yet decidable for this respondent
            return 1 if evaluate(expression, a) else 0

        return [_ColumnSpec(var, in_path)]

    # info (display-only) and any unknown/unsupported type -> no column.
    return []


def uncomputed_derived(survey) -> list[str]:
    """Derived variables that will deliver a column with nothing in it.

    Three kinds reach the data file and only two carry a value. A `HID_*` with
    a base we parsed is computed per respondent from that condition; a rotation
    arm is recorded at entry. A CODED hidden variable — "CODE AGE - BASED ON
    RESPONSES AT S1", with its bands underneath — gets a column, because the
    banner is cut by it and a missing column is worse, but the DERIVATION is
    prose the document wrote for a human scripter and nothing here computes it.

    So that column arrives empty, for every respondent — and an empty column is
    the failure this codebase keeps meeting, because it looks like data. The
    analyst cuts by age band, gets a table of blanks, and reads it as a
    fielding problem rather than as a variable nobody ever wrote the rule for.

    Reported, never guessed at. Inventing a derivation from "BASED ON
    RESPONSES AT S1" would put numbers in that column that nobody authored.
    """
    return sorted(
        q.qid
        for q in survey.questions
        if q.type is QuestionType.INFO
        and _is_derived(q)
        and q.options
        and not (q.qid.upper().startswith("HID_") and q.show_if is not None)
    )


def _is_derived(question: Question) -> bool:
    """A variable the script computes rather than asks."""
    return question.qid.upper().startswith("HID_") or "DERIVED_VARIABLE" in (
        question.flags or []
    )


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
        Variable(name=PARA_DEVICE, label="Device", type=VarType.SINGLE,
                 values={v: k.capitalize() for k, v in DEVICE_CODES.items()},
                 provenance="fielding_paradata"),
        # Flagged, never acted on: a VPN and a neighbouring postcode are both
        # ordinary, so this is a column an analyst can cut by, not a screen-out.
        Variable(name=PARA_PLACE, label="Stated place vs connection",
                 type=VarType.SINGLE,
                 values={1: "Agrees", 2: "Disagrees", 99: "Not checked"},
                 provenance="fielding_paradata"),
        Variable(name=PARA_COUNTRY, label="Country (from connection)",
                 type=VarType.TEXT, provenance="fielding_paradata"),
        Variable(name=PARA_REGION, label="Region (from connection)",
                 type=VarType.TEXT, provenance="fielding_paradata"),
        Variable(name=PARA_CITY, label="City (from connection)",
                 type=VarType.TEXT, provenance="fielding_paradata"),
        Variable(name=PARA_POSTAL, label="Postcode (from connection)",
                 type=VarType.TEXT, provenance="fielding_paradata"),
        # The connection as a salted hash, never an address. Two interviews
        # from one connection are the commonest duplicate there is, and
        # nothing downstream could see them without a column to compare.
        Variable(name=PARA_IP_HASH, label="Connection (hashed)",
                 type=VarType.TEXT, provenance="fielding_paradata"),
        Variable(name=PARA_SESSION, label="Session ID", type=VarType.TEXT,
                 provenance="fielding_paradata"),
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


def plan_datamap(survey: SurveyDefinition) -> DataMap:
    """The data layout an approved instrument will produce — before any respondent.

    Same spec builder the real export uses, so what a client is shown at approval
    is what lands in the file: one column per single/numeric/text question, one
    per multi punch and grid row, and one per hidden variable. Computed on demand
    rather than stored, so it cannot drift from the definition it describes.
    """
    datamap = DataMap()
    datamap.add(
        Variable(
            name="respondent_id", label="Respondent ID", type=VarType.NUMERIC,
            provenance="fielding",
        )
    )
    for qid in survey.question_order():
        if qid in survey:
            for spec in _specs_for_question(survey[qid]):
                datamap.add(spec.variable)
    for var in _paradata_variables():
        datamap.add(var)
    return datamap


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
    devices, places, countries, regions = [], [], [], []
    cities, postcodes, connections = [], [], []
    for s in completes:
        para = summarise_paradata(getattr(s, "entry_params", None) or {})
        devices.append(para["device"])
        places.append(para["place"])
        countries.append(para["country"])
        regions.append(para["region"])
        cities.append(para.get("city"))
        postcodes.append(para.get("postal"))
        connections.append(para.get("ip_hash"))
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
    columns[PARA_DEVICE] = _pandas_series(VarType.SINGLE, devices)
    columns[PARA_PLACE] = _pandas_series(VarType.SINGLE, places)
    columns[PARA_COUNTRY] = _pandas_series(VarType.TEXT, countries)
    columns[PARA_REGION] = _pandas_series(VarType.TEXT, regions)
    columns[PARA_CITY] = _pandas_series(VarType.TEXT, cities)
    columns[PARA_POSTAL] = _pandas_series(VarType.TEXT, postcodes)
    columns[PARA_IP_HASH] = _pandas_series(VarType.TEXT, connections)
    columns[PARA_SESSION] = _pandas_series(VarType.TEXT, [s.id for s in completes])

    # Which block each respondent met FIRST, when the script rotated them.
    #
    # This is the column that makes the rotation worth doing. Rotating without
    # recording it leaves the analyst exactly where fixed order left them: they
    # can see the brand difference and cannot tell how much of it is order.
    # Coded with value labels rather than stored as a block id, so it tabulates
    # and filters like any other single-coded variable.
    #
    # Only emitted when the study actually rotates — nothing else gains a
    # column full of blanks.
    rotating = rotating_blocks(survey)
    if rotating:
        block_labels = {b.block_id: (b.label or b.block_id) for b in survey.blocks}
        code_of = {block_id: index + 1 for index, block_id in enumerate(rotating)}
        datamap.add(Variable(
            name=ROTATION_VARIABLE,
            label="Block order: which block was shown first",
            type=VarType.SINGLE,
            values={code: block_labels.get(block_id, block_id)
                    for block_id, code in code_of.items()},
            provenance="fielding",
        ))
        columns[ROTATION_VARIABLE] = _pandas_series(
            VarType.SINGLE,
            [code_of.get(getattr(s, "block_order", None)) for s in completes],
        )

    df = pd.DataFrame(columns, columns=[v.name for v in datamap.variables])
    return datamap, df

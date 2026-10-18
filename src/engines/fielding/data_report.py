"""The field report for a study fielded somewhere else, read from its export.

A project whose fieldwork ran on Decipher, Qualtrics or a panel's own platform
arrives as a data file, and everything a field monitor shows for our own link —
starts, incidence, which screen-out removed people, which quota cell closed,
how long the interview took, where people gave up, who looks careless — is
already in that file. This reads it out in the same shape as
`field_report.build_field_report`, so one screen serves both.

What the export says outranks what we could recompute: Decipher writes WHY a
respondent was terminated (`term:<rule>`), which quota cells counted them and
which one turned them away (`OQ /table/cell`) into its `markers` column. Those
are the script's own records. Recomputing from the questionnaire is only the
fallback for files without them.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime
from statistics import median
from types import SimpleNamespace
from typing import Any

import polars as pl

from src.core.models import DataMap
from src.core.survey_definition import QuestionType, SurveyDefinition
from src.engines.fielding.decisions import (
    STATUS_COMPLETE,
    STATUS_OVERQUOTA,
    STATUS_TERMINATE,
)
from src.engines.fielding.field_report import build_field_report
from src.engines.fielding.launch_check import loi_against_promise
from src.engines.fielding.paradata import device_of
from src.engines.qc.discrepancy import _disposition_labels, _selected_codes
from src.engines.questionnaire.linker import QuestionVariableLink, _grid_answer

_STATUS_OF = {
    "complete": STATUS_COMPLETE,
    "terminated": STATUS_TERMINATE,
    "overquota": STATUS_OVERQUOTA,
    "partial": None,  # abandoned: the report reads where they stopped
}
_LOI_COLUMNS = ("qtime", "duration", "loi", "interview_length", "duration (in seconds)")
_START_COLUMNS = ("start_date", "startdate", "start", "realstart")
_END_COLUMNS = ("date", "enddate", "end_date", "completed")
_DATE_FORMATS = ("%m/%d/%Y %H:%M", "%m/%d/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
                 "%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M")
# The first variable a terminate rule names is the question that screened them
# out: "term:(SS_REVENUE.r13 or SS_REVENUE.r14)" -> SS_REVENUE.
_RULE_QUESTION = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CELL_CODE = re.compile(r"^(?P<var>.+?)_r(?P<code>\d+)$")


def _column(df: pl.DataFrame, names: tuple[str, ...]) -> str | None:
    lower = {c.lower(): c for c in df.columns}
    return next((lower[n] for n in names if n in lower), None)


def _when(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _markers(value: Any) -> list[str]:
    return [m.strip() for m in str(value or "").split(",") if m.strip()]


def _answer(question, row: dict, variables: list[str]) -> Any:
    """One respondent's answer to one question, in the shape the link stores it."""
    kind = question.type
    if kind is QuestionType.GRID_SINGLE:
        return _grid_answer(row, variables)
    if kind in (QuestionType.SINGLE, QuestionType.DROPDOWN, QuestionType.MULTI, QuestionType.RANK):
        codes = _selected_codes(row, variables, question)
        if not codes:
            return None
        return codes if kind in (QuestionType.MULTI, QuestionType.RANK) else codes[0]
    value = next((row.get(v) for v in variables if row.get(v) not in (None, "")), None)
    return value


def _cell_label(cell: str, datamap: DataMap) -> str:
    """`hSample_r14` -> the label code 14 carries on hSample, when the map has one."""
    match = _CELL_CODE.match(cell)
    if match:
        try:
            values = datamap[match["var"]].values or {}
            label = values.get(int(match["code"]))
            if label:
                return str(label)
        except (KeyError, ValueError):
            pass
    try:
        return datamap[cell].label or cell
    except KeyError:
        return cell


def data_field_report(
    df: pl.DataFrame,
    datamap: DataMap,
    survey: SurveyDefinition | None = None,
    links: list[QuestionVariableLink] | None = None,
    quality_flags: dict[str, list[dict]] | None = None,
) -> dict[str, Any]:
    """Field report + quotas + soft-launch quality for an uploaded export.

    `quality_flags` is `cleaning.collect_flags` output keyed like the data's
    respondent key; passed in so this stays pure and the caller can reuse it.
    """
    status_column, meaning = _disposition_labels(datamap)
    loi_column = _column(df, _LOI_COLUMNS)
    start_column = _column(df, _START_COLUMNS)
    end_column = _column(df, _END_COLUMNS)
    ua_column = next((c for c in df.columns if "useragent" in c.lower()), None)
    has_markers = "markers" in df.columns

    by_var = {link.qid: link.variable_names for link in (links or [])}
    questions = [q for q in (survey.questions if survey else []) if q.qid in by_var]

    sessions, answers = [], {}
    terminates: Counter[str] = Counter()
    rules: dict[str, str] = {}
    overquota: Counter[str] = Counter()
    quota_counts: dict[str, Counter[str]] = {}
    durations: list[float] = []
    completes_by_day: Counter[str] = Counter()
    first = last = None

    for i, row in enumerate(df.iter_rows(named=True)):
        raw = row.get(status_column) if status_column else None
        code = int(raw) if isinstance(raw, (int, float)) and float(raw).is_integer() else None
        outcome = meaning.get(code, "complete" if status_column is None else "other")
        status = _STATUS_OF.get(outcome)
        sid = str(row.get("uuid") or row.get("record") or i)
        sessions.append(SimpleNamespace(
            id=sid, status_code=status, started_at=None, completed_at=None,
            entry_params={"device": device_of(row.get(ua_column)) if ua_column else "unknown",
                          "place_check": "not checked"},
        ))
        answers[sid] = {
            q.qid: value for q in questions
            if (value := _answer(q, row, by_var[q.qid])) not in (None, "", [], {})
        }

        when = _when(row.get(end_column)) if end_column else None
        began = _when(row.get(start_column)) if start_column else None
        for stamp in (began, when):
            if stamp:
                first = stamp if first is None or stamp < first else first
                last = stamp if last is None or stamp > last else last

        marks = _markers(row.get("markers")) if has_markers else []
        if status == STATUS_COMPLETE:
            seconds = row.get(loi_column) if loi_column else None
            if isinstance(seconds, (int, float)) and seconds > 0:
                durations.append(float(seconds))
            elif began and when and when > began:
                durations.append((when - began).total_seconds())
            if when:
                completes_by_day[when.date().isoformat()] += 1
            # A quota cell this complete counted towards: "/Table/cell[/cell]".
            for mark in marks:
                if mark.startswith("/") and mark.count("/") >= 2:
                    _, table, cell = mark.split("/", 2)
                    quota_counts.setdefault(table, Counter())[cell] += 1
        elif status == STATUS_TERMINATE:
            rule = next((m[5:] for m in marks if m.startswith("term:")), None)
            if rule:
                name = (_RULE_QUESTION.search(rule) or [rule])[0]
                terminates[name] += 1
                rules.setdefault(name, rule)
        elif status == STATUS_OVERQUOTA:
            cell = next((m[3:].strip() for m in marks if m.startswith("OQ /")), None)
            if cell:
                overquota[cell] += 1

    report = build_field_report(survey or SurveyDefinition(study_id=""), sessions, answers, [])
    out = report.to_dict()
    # The script recorded why; trust it over a recomputation from the document.
    if terminates:
        out["terminates_by_question"] = [
            {"qid": q, "n": n, "rule": rules[q]} for q, n in terminates.most_common()
        ]
    if overquota or has_markers:
        out["overquota_by_cell"] = [{"cell": c, "n": n} for c, n in overquota.most_common()]
    out["median_loi_sec"] = round(median(durations), 1) if durations else None
    out["place_checks"] = None
    if not ua_column:
        out["devices"] = None

    overview = ((survey.metadata or {}).get("study_overview") or {}) if survey else {}
    out["length_of_interview"] = (
        loi_against_promise(survey, out["median_loi_sec"]) if survey else None
    )
    out["sample_size_target"] = overview.get("sample_size")
    out["quota_targets"] = overview.get("quotas") or []
    out["quotas"] = [
        {
            "table": table,
            "cells": [
                {"cell": cell, "n": n,
                 "label": " × ".join(_cell_label(part, datamap) for part in cell.split("/"))}
                for cell, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
            ],
        }
        for table, counts in quota_counts.items()
        if table.lower() != "overall"  # the study total, already the completes figure
    ]
    out["field_dates"] = (
        {"first": first.isoformat(), "last": last.isoformat()} if first and last else None
    )
    out["completes_by_day"] = [{"day": d, "n": n} for d, n in sorted(completes_by_day.items())]

    flags = quality_flags or {}
    by_check: Counter[str] = Counter()
    for found in flags.values():
        for check in {f["check"] for f in found}:
            by_check[check] += 1
    out["quality"] = {
        "n_flagged": len(flags),
        "by_check": [{"check": c, "n": n} for c, n in by_check.most_common()],
    }
    out["source"] = "data"
    return out

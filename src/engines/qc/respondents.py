"""Respondent-quality checks: speeders, straightliners, duplicates, junk OEs.

This is the data-cleaning layer every DP shop (and Decipher/Qualtrics natively)
runs before tables — the checks that decide whether a *respondent* is trustworthy,
as opposed to the file-level checks in orchestrator._run_qc (sample size,
missingness). All checks are pure deterministic computation over the canonical
(DataFrame, DataMap) pair: no AI, no network, bit-identical on re-run — so a
flagged respondent is always explainable to a client.

Every check returns per-respondent row indices; the orchestrator summarizes them
into the QCReport and stashes the full detail in its metadata so a UI can later
show exactly WHO was flagged and WHY (the incumbents' "data quality" tab).

Design notes:
- Grids for straightlining come from Variable.question_ref when the adapter
  provides it (SPSS path), else from a name-prefix heuristic (Q5r1/Q5r2/...,
  the dominant Decipher convention) — same fallback philosophy as the tabular
  adapter itself.
- Speeder detection needs an interview-duration column, which exports name
  inconsistently; we look for the usual suspects by name. If none is found the
  check reports "not run" rather than guessing — an honest skip beats a wrong
  flag.
- Thresholds follow common DP practice (speeder = LOI < 1/3 of median;
  straightline = zero variance across a grid of >= MIN_GRID_ITEMS items) and
  are constants here, not config, until a real client needs to tune them.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import polars as pl

from src.core.models import DataMap, VarType
from src.engines.qc.variable_roles import (
    EXPECTED_IN_QUESTIONNAIRE,
    classify_variable,
)

# A grid must have at least this many item variables before a uniform answer
# row is suspicious — answering "agree" to 3 items is opinion, to 8 is a flag.
MIN_GRID_ITEMS = 4
# Interview length below this fraction of the sample median marks a speeder.
SPEEDER_MEDIAN_FRACTION = 1 / 3
# Fewest completes needed before the median interview time is stable enough to
# derive a speeder threshold from — below this, one fast interview skews it.
MIN_SPEEDER_N = 10
# Column names (lowercased) that plausibly hold interview duration in seconds
# or minutes. Ordered: first hit wins.
_DURATION_NAMES = (
    "duration",
    "duration_in_seconds",
    "vdurationsec",  # fielding paradata (materialize.PARA_DURATION), lowercased
    "interviewtime",
    "timespent",
    "time_spent",
    "loi",
    "qtime",
    "surveytime",
    "totaltime",
)
# An open-end shorter than this many characters is never flagged as gibberish
# ("ok", "no", "n/a" are legitimate answers, not junk).
_MIN_GIBBERISH_LEN = 4
# Repeated single character ("aaaaaa") or keyboard mash rows.
_REPEAT_RE = re.compile(r"^(.)\1+$")
_MASH_SEQUENCES = ("qwert", "asdf", "zxcv", "12345", "qazws")
_VOWELS = set("aeiouAEIOU")

# Grid-item naming conventions seen across platform exports: Q5r1 (Decipher),
# Q5_1 (Qualtrics/SPSS), Q5c2. The prefix before the item marker groups a grid.
_GRID_ITEM_RE = re.compile(r"^(.*?)(?:[rc_]\d+)$")


@dataclass
class RespondentQCResult:
    """Row indices (0-based positions in the DataFrame) flagged per check."""

    speeders: list[int] = field(default_factory=list)
    straightliners: list[int] = field(default_factory=list)
    duplicates: list[int] = field(default_factory=list)
    gibberish_open_ends: dict[str, list[int]] = field(default_factory=dict)
    # Which checks actually ran (speeders needs a duration column; straightlining
    # needs grids). An honest "didn't run" is reported, not silently skipped.
    checks_run: list[str] = field(default_factory=list)
    checks_skipped: dict[str, str] = field(default_factory=dict)  # check -> why
    # Supporting details for the report/UI.
    duration_column: str | None = None
    grids_checked: int = 0

    def flagged_rows(self) -> set[int]:
        """Union of every flagged respondent across checks."""
        rows: set[int] = set(self.speeders) | set(self.straightliners) | set(self.duplicates)
        for indices in self.gibberish_open_ends.values():
            rows.update(indices)
        return rows


def _find_duration_column(df: pl.DataFrame, datamap: DataMap) -> str | None:
    """Locate an interview-duration column by name, numeric columns only."""
    by_lower = {c.lower(): c for c in df.columns}
    for candidate in _DURATION_NAMES:
        col = by_lower.get(candidate)
        if col is not None and df[col].dtype.is_numeric():
            return col
    return None


def detect_speeders(df: pl.DataFrame, datamap: DataMap) -> tuple[list[int], str | None]:
    """Respondents whose interview length is under 1/3 of the sample median.

    Returns (row indices, duration column used) — column None means the check
    could not run because no duration column was found.
    """
    col = _find_duration_column(df, datamap)
    if col is None:
        return [], None
    series = df[col]
    valid = series.drop_nulls()
    if valid.is_empty():
        return [], None
    median = valid.median()
    if not median or median <= 0:
        return [], None
    threshold = median * SPEEDER_MEDIAN_FRACTION
    mask = series.is_not_null() & (series < threshold)
    return mask.arg_true().to_list(), col


def _grid_groups(datamap: DataMap) -> list[list[str]]:
    """Group single-coded variables into grids a respondent actually filled in.

    question_ref (adapter-provided grid parent) wins; variables without one are
    grouped by the Q5r1/Q5_1 naming convention. Only groups of MIN_GRID_ITEMS+
    sharing one scale (same code set) count — mixed-scale groups are almost
    certainly not a visual grid and would false-positive.

    Columns nobody was asked are excluded, and that exclusion is the difference
    between a usable number and an ignored one. On a real 750-respondent study
    this check reported 89.3% straightlining, which nobody would act on. The
    biggest contributor was `HID_AWARE`, a block of script-computed flags that
    is identical across items for 78% of respondents — as computed values often
    are. That is arithmetic, not inattention. MaxDiff blocks are excluded for a
    related reason: choosing the same POSITION each task is ordinary MaxDiff
    behaviour, which is why scripters write a purpose-built MaxDiff
    straightliner flag instead of using this one.
    """
    groups: dict[str, list] = {}
    for v in datamap.variables:
        if v.type != VarType.SINGLE or not v.values:
            continue
        role = classify_variable(v.name, getattr(v, "label", "") or "", v.question_ref)
        if role not in EXPECTED_IN_QUESTIONNAIRE:
            continue
        key = v.question_ref
        if not key:
            m = _GRID_ITEM_RE.match(v.name)
            if not m or not m.group(1):
                continue
            key = m.group(1)
        groups.setdefault(key, []).append(v)

    grids: list[list[str]] = []
    for members in groups.values():
        if len(members) < MIN_GRID_ITEMS:
            continue
        # All items must share the same scale to be a real grid.
        scales = {tuple(sorted(v.values)) for v in members}
        if len(scales) == 1:
            grids.append([v.name for v in members])
    return grids


def detect_straightliners(df: pl.DataFrame, datamap: DataMap) -> tuple[list[int], int]:
    """Respondents who gave one identical answer across every item of a grid.

    A respondent is flagged if they straightlined at least one full grid they
    fully answered. Returns (row indices, number of grids checked).
    """
    grids = _grid_groups(datamap)
    flagged: set[int] = set()
    checked = 0
    for grid in grids:
        cols = [c for c in grid if c in df.columns]
        if len(cols) < MIN_GRID_ITEMS:
            continue
        checked += 1
        sub = df.select(cols)
        # Straightline: fully answered AND min == max across the row.
        answered = sub.select(
            pl.all_horizontal([pl.col(c).is_not_null() for c in cols]).alias("full")
        )["full"]
        lo = sub.select(pl.min_horizontal([pl.col(c) for c in cols]).alias("lo"))["lo"]
        hi = sub.select(pl.max_horizontal([pl.col(c) for c in cols]).alias("hi"))["hi"]
        mask = answered & (lo == hi)
        flagged.update(mask.arg_true().to_list())
    return sorted(flagged), checked


def detect_duplicates(df: pl.DataFrame, datamap: DataMap) -> list[int]:
    """Respondents whose full answer pattern duplicates an earlier row.

    Compares only question variables (from the DataMap), never system columns
    like uuid/date — two genuinely different respondents can't share every
    answer across a real questionnaire, but they trivially differ on record ids,
    which would mask the duplication. The FIRST occurrence is kept unflagged;
    every later copy is flagged (standard dedupe convention).
    """
    # Identifiers and paradata (record, vDurationSec, respondent_id...) differ
    # on every row and would hide every duplicate; skip them like _grid_groups.
    cols = [
        v.name
        for v in datamap.variables
        if v.name in df.columns
        and v.type in (VarType.SINGLE, VarType.MULTI, VarType.NUMERIC)
        and v.name != "respondent_id"
        and v.provenance != "fielding_paradata"
        and classify_variable(v.name, getattr(v, "label", "") or "", v.question_ref)
        in EXPECTED_IN_QUESTIONNAIRE
    ]
    if not cols:
        return []
    # Pack the answer columns into one struct series: duplicated-and-not-first
    # marks every later copy of an identical full answer pattern.
    packed = df.select(pl.struct(cols).alias("_pattern"))["_pattern"]
    mask = packed.is_duplicated() & ~packed.is_first_distinct()
    return mask.arg_true().to_list()


def _is_gibberish(text: str) -> bool:
    """Cheap junk-detection for one open-end answer. Conservative by design —
    a false 'junk' flag on a real answer costs reviewer trust."""
    stripped = text.strip()
    if len(stripped) < _MIN_GIBBERISH_LEN:
        return False  # too short to judge; not flagged
    low = stripped.lower()
    if _REPEAT_RE.match(low):
        return True
    if any(seq in low for seq in _MASH_SEQUENCES):
        return True
    letters = [ch for ch in low if ch.isalpha()]
    # A long alphabetic answer with no vowels at all is keyboard mash
    # ("dfgkjhdfkg"), not language.
    if len(letters) >= 6 and not any(ch in _VOWELS for ch in letters):
        return True
    return False


def detect_gibberish_open_ends(df: pl.DataFrame, datamap: DataMap) -> dict[str, list[int]]:
    """Per TEXT variable, row indices whose answer looks like junk."""
    out: dict[str, list[int]] = {}
    for v in datamap.variables:
        # paradata TEXT (connection hash, geo) is not an answer; a hash with no vowels is not junk
        if v.type != VarType.TEXT or v.name not in df.columns:
            continue
        if v.provenance == "fielding_paradata":
            continue
        series = df[v.name]
        flagged = [
            i
            for i, val in enumerate(series.to_list())
            if isinstance(val, str) and _is_gibberish(val)
        ]
        if flagged:
            out[v.name] = flagged
    return out


def run_respondent_qc(
    df: pl.DataFrame, datamap: DataMap, *, speeder_min_n: int = 0
) -> RespondentQCResult:
    """Run every respondent-quality check and assemble one result.

    speeder_min_n: minimum completes before the speeder check runs (the median
    is unstable below it). Defaults to 0 (no floor) so existing callers keep
    their behavior; the fielding path passes MIN_SPEEDER_N.
    """
    result = RespondentQCResult()

    duration_col = _find_duration_column(df, datamap)
    if duration_col is None:
        result.checks_skipped["speeders"] = "no interview-duration column found"
    elif df[duration_col].drop_nulls().len() < speeder_min_n:
        result.checks_skipped["speeders"] = (
            f"only {df[duration_col].drop_nulls().len()} timed interviews; "
            f"need >= {speeder_min_n} for a stable median"
        )
    else:
        result.speeders, _ = detect_speeders(df, datamap)
        result.duration_column = duration_col
        result.checks_run.append("speeders")

    straightliners, grids_checked = detect_straightliners(df, datamap)
    result.grids_checked = grids_checked
    if grids_checked == 0:
        result.checks_skipped["straightlining"] = "no grids of 4+ same-scale items found"
    else:
        result.straightliners = straightliners
        result.checks_run.append("straightlining")

    result.duplicates = detect_duplicates(df, datamap)
    result.checks_run.append("duplicates")

    result.gibberish_open_ends = detect_gibberish_open_ends(df, datamap)
    result.checks_run.append("gibberish_open_ends")

    return result


def summarize_for_report(result: RespondentQCResult, n_respondents: int) -> list[str]:
    """Human-readable warning lines for the QCReport (empty list = all clean)."""
    lines: list[str] = []
    n = max(n_respondents, 1)

    def pct(count: int) -> str:
        return f"{count} ({math.floor(count / n * 1000) / 10:.1f}%)"

    if result.speeders:
        lines.append(
            f"{pct(len(result.speeders))} respondents completed in under 1/3 of the "
            f"median time ('{result.duration_column}') — review before delivery"
        )
    if result.straightliners:
        lines.append(
            f"{pct(len(result.straightliners))} respondents straightlined at least one "
            f"grid ({result.grids_checked} grids checked)"
        )
    if result.duplicates:
        lines.append(
            f"{pct(len(result.duplicates))} respondents duplicate another respondent's "
            "full answer pattern"
        )
    if result.gibberish_open_ends:
        total = sum(len(v) for v in result.gibberish_open_ends.values())
        lines.append(
            f"{total} open-end answers across {len(result.gibberish_open_ends)} "
            "questions look like gibberish/keyboard-mash"
        )
    return lines


def respondent_qc_report(df: pl.DataFrame, datamap: DataMap) -> dict:
    """Run every respondent-quality check and return a JSON-serializable report.

    Single entry point for the Steward's check_respondent_quality tool. Flagged
    rows are reported as `respondent_id` values when that column exists (fielded
    studies always have it; SPSS imports may not), else as 0-based row indices.
    Never raises — an empty/degenerate file yields a report with everything
    clean and the honest checks_skipped notes.
    """
    result = run_respondent_qc(df, datamap, speeder_min_n=MIN_SPEEDER_N)
    n = df.height

    ids = df["respondent_id"].to_list() if "respondent_id" in df.columns else None
    key = "respondent_id" if ids is not None else "row_index"

    def label(indices: list[int]) -> list:
        return [ids[i] for i in indices] if ids is not None else list(indices)

    notes = summarize_for_report(result, n)
    for check, why in result.checks_skipped.items():
        notes.append(f"{check} not run: {why}")

    return {
        "success": True,
        "tool": "check_respondent_quality",
        "row_count": n,
        "id_key": key,
        "flagged": {
            "speeders": label(result.speeders),
            "straightliners": label(result.straightliners),
            "duplicates": label(result.duplicates),
            "gibberish_open_ends": {
                var: label(rows) for var, rows in result.gibberish_open_ends.items()
            },
        },
        "summary": {
            "speeders": len(result.speeders),
            "straightliners": len(result.straightliners),
            "duplicates": len(result.duplicates),
            "gibberish_open_ends": sum(len(v) for v in result.gibberish_open_ends.values()),
            "total_flagged": len(result.flagged_rows()),
        },
        "checks_run": result.checks_run,
        "checks_skipped": result.checks_skipped,
        "duration_column": result.duration_column,
        "grids_checked": result.grids_checked,
        "notes": notes,
    }

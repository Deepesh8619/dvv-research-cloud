"""Cleaning engine core: flags -> decisions -> a rebuilt dataset. Pure, no DB.

Respondent identity is a stable key: the first of vSessionId / uuid / record /
respondent_id that is fully non-null and unique, else positional `row:{i}`.

Removal is never destructive: the first apply_cleaning copies data.parquet to
data_raw.parquet, and every call rebuilds data.parquet FROM the raw copy, so a
later decision can restore a row an earlier one removed.
"""

from __future__ import annotations

import copy
import os
from collections import defaultdict
from pathlib import Path

import polars as pl

from src.core.models import DataMap, Variable, VarType
from src.engines.qc.fraud import fraud_signals
from src.engines.qc.respondents import MIN_SPEEDER_N, run_respondent_qc
from src.engines.qc.rules import run_rules

REASON_CODES = (
    "speeder", "straightliner", "duplicate", "gibberish", "trap_failed", "red_herring",
    "inconsistent", "fraud_signal", "out_of_range", "erasure", "client_request", "other",
)
KEY_COLUMNS = ("vSessionId", "uuid", "record", "respondent_id")
CLEAN_STATUS = "vCleanStatus"
CLEAN_REASON = "vCleanReason"


def respondent_key_column(df: pl.DataFrame) -> str | None:
    for c in KEY_COLUMNS:
        if c in df.columns and df[c].null_count() == 0 and df[c].n_unique() == df.height:
            return c
    return None


def respondent_keys(df: pl.DataFrame) -> list[str]:
    col = respondent_key_column(df)
    if col is None:
        return [f"row:{i}" for i in range(df.height)]
    return [str(v) for v in df[col].to_list()]


def collect_flags(
    df: pl.DataFrame, datamap: DataMap, rules: list[dict] | None = None,
    include_fraud: bool = True,
) -> tuple[dict[str, list[dict]], dict[str, str]]:
    """Return ({key: [{"check", "detail"}]}, skipped).

    skipped is {check: why}: respondent-QC skips carry their reason, fraud skips
    (absent paradata columns) are keyed "fraud:<signal>".
    """
    keys = respondent_keys(df)
    flags: dict[str, list[dict]] = defaultdict(list)

    def add(i: int, check: str, detail: str) -> None:
        flags[keys[i]].append({"check": check, "detail": detail})

    qc = run_respondent_qc(df, datamap, speeder_min_n=MIN_SPEEDER_N)
    for i in qc.speeders:
        add(i, "speeder", f"short interview ({qc.duration_column})")
    for i in qc.straightliners:
        add(i, "straightliner", "same answer across a grid")
    for i in qc.duplicates:
        add(i, "duplicate", "identical answers to another respondent")
    for var, idx in qc.gibberish_open_ends.items():
        for i in idx:
            add(i, "gibberish", var)
    skipped = dict(qc.checks_skipped)
    # Typing traces, AI-written text and reading time: the authenticity evidence.
    from src.engines.qc.authenticity import ai_text_flags, reading_speed_flags, typing_flags

    for found in (typing_flags(df), ai_text_flags(df, datamap), reading_speed_flags(df, datamap)):
        for i, items in found.items():
            for check, detail in items:
                add(i, check, detail)

    for f in run_rules(df, rules or [], lambda i: keys[i]):
        flags[f["key"]].append({"check": f["check"], "detail": f["detail"]})

    if include_fraud:
        fr = fraud_signals(df, datamap, key_of=lambda i, _row: keys[i])
        for key, info in fr["flags"].items():
            for s in info["signals"]:
                flags[key].append({"check": "fraud_signal", "detail": s})
        skipped.update({f"fraud:{s}": "column not in data" for s in fr["checks_skipped"]})
    return dict(flags), skipped


def current_decisions(audit_rows: list[dict]) -> dict[str, dict]:
    """Latest remove/keep per key wins. Rows: {entity_id, action, after={reason_code, note}}.

    Other actions (impute) are ignored.
    """
    out: dict[str, dict] = {}
    for r in audit_rows:
        if r.get("action") in ("remove", "keep"):
            after = r.get("after") or {}
            out[r["entity_id"]] = {
                "decision": r["action"],
                "reason_code": after.get("reason_code"),
                "note": after.get("note"),
            }
    return out


HABIT_MIN_DECISIONS, HABIT_MIN_RATE, HABIT_MIN_STUDIES = 10, 0.9, 2


def check_tally(flags: dict[str, list[dict]], decisions: dict[str, dict]) -> dict[str, list[int]]:
    """{check: [removed, kept]} over decided flagged respondents, once per respondent per check."""
    out: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for key, items in flags.items():
        d = decisions.get(key)
        if not d:
            continue
        for check in {f["check"] for f in items}:
            out[check][0 if d["decision"] == "remove" else 1] += 1
    return dict(out)


def suggestions(
    habits: dict[str, dict], flags: dict[str, list[dict]], decisions: dict[str, dict]
) -> list[dict]:
    """Checks this reviewer nearly always removes, with this study's undecided matches.

    habits: {check: {removed, kept, studies}}. A suggestion, never a decision.
    """
    from src.engines.qc.authenticity import _LABELS

    out = []
    for check, h in habits.items():
        total = h["removed"] + h["kept"]
        if total < HABIT_MIN_DECISIONS or h["studies"] < HABIT_MIN_STUDIES:
            continue
        rate = h["removed"] / total
        keys = sorted(k for k, items in flags.items()
                      if k not in decisions and any(f["check"] == check for f in items))
        if rate >= HABIT_MIN_RATE and keys:
            out.append({"check": check, "label": _LABELS.get(check, check.replace("_", " ")),
                        "removed": h["removed"], "decided": total, "removed_rate": round(rate, 3),
                        "studies": h["studies"], "keys": keys})
    return sorted(out, key=lambda s: -len(s["keys"]))


def apply_cleaning(
    run_dir: str | Path, removed_keys, imputations: list[dict] | None = None
) -> dict[str, int]:
    """Rebuild run_dir/data.parquet from data_raw.parquet minus removed_keys.

    imputations: [{key, variable, new}] applied to the raw rows that stay.
    """
    run_dir = Path(run_dir)
    data, raw = run_dir / "data.parquet", run_dir / "data_raw.parquet"
    if not raw.exists():
        tmp = run_dir / "data_raw.parquet.tmp"
        tmp.write_bytes(data.read_bytes())
        os.replace(tmp, raw)  # atomic: a half-copied raw would be mistaken for the original
    df = pl.read_parquet(raw)
    keys = respondent_keys(df)
    for imp in imputations or []:
        col = imp["variable"]
        vals = df[col].to_list()
        for i, k in enumerate(keys):
            if k == imp["key"]:
                vals[i] = imp["new"]
        df = df.with_columns(pl.Series(col, vals, dtype=df[col].dtype))
    drop = set(removed_keys)
    out = df.filter(pl.Series([k not in drop for k in keys], dtype=pl.Boolean))
    tmp = run_dir / "data.parquet.tmp"
    out.write_parquet(tmp)
    os.replace(tmp, data)
    return {"n_before": df.height, "n_after": out.height}


def annotate_delivery(
    df: pl.DataFrame, datamap: DataMap, removed
) -> tuple[pl.DataFrame, DataMap]:
    """Copies with vCleanStatus (1 Kept / 2 Removed) and vCleanReason (TEXT) added.

    removed: {key: reason_code}, or any iterable of keys (reason left blank).
    Provenance "fielding_paradata" keeps both out of duplicate/open-end checks.
    """
    reasons = dict(removed) if isinstance(removed, dict) else {k: None for k in removed}
    keys = respondent_keys(df)
    out = df.with_columns(
        pl.Series(CLEAN_STATUS, [2 if k in reasons else 1 for k in keys], dtype=pl.Int64),
        pl.Series(CLEAN_REASON, [reasons.get(k) for k in keys], dtype=pl.String),
    )
    dm = copy.deepcopy(datamap)
    dm.add(Variable(name=CLEAN_STATUS, label="Cleaning status", type=VarType.SINGLE,
                    values={1: "Kept", 2: "Removed"}, provenance="fielding_paradata"))
    dm.add(Variable(name=CLEAN_REASON, label="Removal reason", type=VarType.TEXT,
                    provenance="fielding_paradata"))
    return out, dm


def replacement_needed(removed_answers: dict[str, dict], cells) -> dict:
    """How many replacement completes the removals call for, per quota cell.

    removed_answers: {key: that respondent's answers}. A respondent in several
    cells counts once in each; `total` counts respondents (every removed complete
    needs one replacement, even one that matched no cell).
    """
    from src.engines.fielding.decisions import matching_cells

    by_cell: dict[str, int] = defaultdict(int)
    for answers in removed_answers.values():
        for cell in matching_cells(cells, answers):
            by_cell[cell.id] += 1
    return {"total": len(removed_answers), "by_cell": dict(by_cell)}

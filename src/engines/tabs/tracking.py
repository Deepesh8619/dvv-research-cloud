"""Wave-over-wave tracking: compare two waves of the same study design.

Trackers are the recurring-revenue heart of agency research — the same
questionnaire fielded repeatedly, with the client asking one question of every
new wave: WHAT MOVED, and is the movement real or noise? The incumbent
platforms answer with wave-comparison columns and sig-marks vs the prior wave;
this module is that computation for DVV.

Deterministic all the way down (same guarantee as the crosstab engine): the
wave delta test is the same pooled two-sample proportion z-test as the banner
sig-test (stats.col_prop_ztest), computed per answer code between the two
waves' Total columns. No AI anywhere in the numbers.

V1 scope (documented, honest):
- Unweighted bases (weights land when tracking meets RIM-weighted studies).
- Total-level comparison per shared single-coded variable; banner-level
  tracking (e.g. delta among 18-34s) is the natural v2 on top of crosstab().
- Two waves per call; a longer trend line is repeated pairwise calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

from src.core.models import DataMap, VarType
from src.engines.tabs.stats import col_prop_ztest, z_critical


@dataclass
class CodeDelta:
    """One answer code's movement between waves."""

    code: int
    label: str
    pct_baseline: float  # 0-100
    pct_current: float  # 0-100
    delta: float  # current - baseline, points
    significant: bool  # pooled two-sample z at the requested confidence
    direction: str  # "up" | "down" | "flat"


@dataclass
class VariableTrend:
    """One shared variable's wave-over-wave comparison."""

    name: str
    label: str
    base_baseline: int
    base_current: int
    deltas: list[CodeDelta] = field(default_factory=list)

    @property
    def n_significant(self) -> int:
        return sum(1 for d in self.deltas if d.significant)


@dataclass
class TrackingReport:
    """Everything the tracking view/deck needs for one wave pair."""

    baseline_id: str
    current_id: str
    confidence: float
    variables: list[VariableTrend] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)  # variable -> why

    def to_dict(self) -> dict:
        return {
            "baseline_id": self.baseline_id,
            "current_id": self.current_id,
            "confidence": self.confidence,
            "skipped": self.skipped,
            "variables": [
                {
                    "name": v.name,
                    "label": v.label,
                    "base_baseline": v.base_baseline,
                    "base_current": v.base_current,
                    "n_significant": v.n_significant,
                    "deltas": [
                        {
                            "code": d.code,
                            "label": d.label,
                            "pct_baseline": round(d.pct_baseline, 1),
                            "pct_current": round(d.pct_current, 1),
                            "delta": round(d.delta, 1),
                            "significant": d.significant,
                            "direction": d.direction,
                        }
                        for d in v.deltas
                    ],
                }
                for v in self.variables
            ],
        }


def _code_distribution(df: pl.DataFrame, name: str) -> tuple[dict[int, int], int]:
    """(counts per code, answered base) for one single-coded column."""
    series = df[name].drop_nulls()
    base = len(series)
    if base == 0:
        return {}, 0
    counts = series.value_counts()
    # value_counts returns columns [name, "count"]
    return {
        int(row[0]): int(row[1])
        for row in counts.iter_rows()
        if row[0] is not None
    }, base


def _shared_trackable_variables(
    dm_base: DataMap, dm_cur: DataMap
) -> tuple[list[str], dict[str, str]]:
    """Variables present in BOTH waves with compatible single-coded scales.

    A tracker's instrument drifts between waves (added codes, dropped
    questions); comparing anyway would produce nonsense deltas, so anything
    incompatible is skipped WITH a reason the report surfaces.
    """
    shared: list[str] = []
    skipped: dict[str, str] = {}
    cur_names = {v.name for v in dm_cur.variables}
    for v in dm_base.variables:
        if v.type != VarType.SINGLE or not v.values:
            continue
        if v.name not in cur_names:
            skipped[v.name] = "not present in current wave"
            continue
        cur = dm_cur[v.name]
        if cur.type != VarType.SINGLE:
            skipped[v.name] = f"type changed ({v.type} -> {cur.type})"
            continue
        if set(v.values) != set(cur.values or {}):
            skipped[v.name] = "answer codes differ between waves"
            continue
        shared.append(v.name)
    return shared, skipped


def compare_waves(
    baseline_parquet: str | Path,
    baseline_datamap: DataMap,
    current_parquet: str | Path,
    current_datamap: DataMap,
    baseline_id: str = "baseline",
    current_id: str = "current",
    confidence: float = 0.95,
    min_base: int = 30,
) -> TrackingReport:
    """Compare two waves' Total distributions for every shared variable.

    Sig-marks each code's delta with the pooled two-sample proportion z-test
    (two-tailed: |z| above the critical value). Unweighted bases; variables
    with a base under `min_base` in either wave are skipped (a sig-test on
    n=12 is theater, not statistics).
    """
    df_base = pl.read_parquet(str(baseline_parquet))
    df_cur = pl.read_parquet(str(current_parquet))

    report = TrackingReport(
        baseline_id=baseline_id, current_id=current_id, confidence=confidence
    )
    shared, report.skipped = _shared_trackable_variables(baseline_datamap, current_datamap)
    crit = z_critical(confidence)

    for name in shared:
        if name not in df_base.columns or name not in df_cur.columns:
            report.skipped[name] = "column missing from data file"
            continue
        counts_b, base_b = _code_distribution(df_base, name)
        counts_c, base_c = _code_distribution(df_cur, name)
        if base_b < min_base or base_c < min_base:
            report.skipped[name] = f"base too small (baseline {base_b}, current {base_c})"
            continue

        var = baseline_datamap[name]
        trend = VariableTrend(
            name=name,
            label=var.label or name,
            base_baseline=base_b,
            base_current=base_c,
        )
        for code in sorted(var.values):
            p_b = counts_b.get(code, 0) / base_b
            p_c = counts_c.get(code, 0) / base_c
            # Unweighted: effective base == n.
            z = col_prop_ztest(p_c, p_b, float(base_c), float(base_b))
            # bool() strips numpy's bool_ — it isn't JSON-serializable.
            significant = bool(abs(z) > crit)
            delta = (p_c - p_b) * 100
            trend.deltas.append(
                CodeDelta(
                    code=code,
                    label=var.values.get(code, str(code)),
                    pct_baseline=p_b * 100,
                    pct_current=p_c * 100,
                    delta=delta,
                    significant=significant,
                    direction="up" if delta > 0.05 else ("down" if delta < -0.05 else "flat"),
                )
            )
        report.variables.append(trend)

    # Most-moved first: the client's question is "what changed?", so lead with it.
    report.variables.sort(
        key=lambda v: (v.n_significant, max((abs(d.delta) for d in v.deltas), default=0.0)),
        reverse=True,
    )
    return report

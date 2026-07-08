"""Wave-over-wave tracking engine: deltas, sig-marks, honest skips.

Synthetic two-wave data with a movement large enough to be unambiguous under
the z-test, plus fixtures for every skip reason (code drift, missing variable,
small base).
"""

from __future__ import annotations

import polars as pl

from src.core.models import DataMap, Variable, VarType
from src.engines.tabs.tracking import compare_waves

_AWARE = {1: "Aware", 2: "Not aware"}


def _dm(codes: dict[int, str] = _AWARE, extra: Variable | None = None) -> DataMap:
    dm = DataMap()
    dm.add(Variable(name="Q1", label="Brand awareness", type=VarType.SINGLE, values=dict(codes)))
    if extra is not None:
        dm.add(extra)
    return dm


def _wave(path, aware_count: int, total: int = 200):
    df = pl.DataFrame({"Q1": [1] * aware_count + [2] * (total - aware_count)})
    df.write_parquet(str(path))
    return path


def test_big_shift_is_significant_and_directional(tmp_path):
    # 40% -> 60% awareness on n=200/200: unambiguously significant at 95%.
    base = _wave(tmp_path / "w1.parquet", aware_count=80)
    cur = _wave(tmp_path / "w2.parquet", aware_count=120)

    report = compare_waves(base, _dm(), cur, _dm(), "w1", "w2")

    assert len(report.variables) == 1
    trend = report.variables[0]
    assert trend.base_baseline == trend.base_current == 200
    aware = next(d for d in trend.deltas if d.code == 1)
    assert aware.pct_baseline == 40.0
    assert aware.pct_current == 60.0
    assert abs(aware.delta - 20.0) < 1e-9
    assert aware.significant and aware.direction == "up"
    not_aware = next(d for d in trend.deltas if d.code == 2)
    assert not_aware.significant and not_aware.direction == "down"


def test_tiny_shift_is_not_significant(tmp_path):
    # 40% -> 41% on n=200: noise, must not be marked.
    base = _wave(tmp_path / "w1.parquet", aware_count=80)
    cur = _wave(tmp_path / "w2.parquet", aware_count=82)

    report = compare_waves(base, _dm(), cur, _dm(), "w1", "w2")
    assert all(not d.significant for d in report.variables[0].deltas)


def test_code_drift_is_skipped_with_reason(tmp_path):
    base = _wave(tmp_path / "w1.parquet", aware_count=80)
    cur = _wave(tmp_path / "w2.parquet", aware_count=90)
    dm_drifted = _dm(codes={1: "Aware", 2: "Not aware", 3: "Unsure"})

    report = compare_waves(base, _dm(), cur, dm_drifted, "w1", "w2")
    assert report.variables == []
    assert "answer codes differ" in report.skipped["Q1"]


def test_small_base_is_skipped(tmp_path):
    base = _wave(tmp_path / "w1.parquet", aware_count=4, total=10)
    cur = _wave(tmp_path / "w2.parquet", aware_count=120, total=200)

    report = compare_waves(base, _dm(), cur, _dm(), "w1", "w2")
    assert report.variables == []
    assert "base too small" in report.skipped["Q1"]


def test_missing_variable_in_current_wave_is_skipped(tmp_path):
    base = _wave(tmp_path / "w1.parquet", aware_count=80)
    cur = _wave(tmp_path / "w2.parquet", aware_count=90)
    dm_cur = DataMap()
    dm_cur.add(Variable(name="Q9", label="new q", type=VarType.SINGLE, values={1: "y"}))

    report = compare_waves(base, _dm(), cur, dm_cur, "w1", "w2")
    assert report.variables == []
    assert report.skipped["Q1"] == "not present in current wave"


def test_report_serializes_to_plain_dict(tmp_path):
    import json

    base = _wave(tmp_path / "w1.parquet", aware_count=80)
    cur = _wave(tmp_path / "w2.parquet", aware_count=120)
    payload = compare_waves(base, _dm(), cur, _dm(), "w1", "w2").to_dict()
    payload = json.loads(json.dumps(payload))  # must survive JSON round-trip
    assert payload["variables"][0]["deltas"][0]["delta"] == 20.0
    assert payload["baseline_id"] == "w1"

"""Respondent-quality QC: speeders, straightliners, duplicates, gibberish OEs.

All synthetic data — each check gets a fixture where the flagged rows are known
by construction, so assertions are exact row indices, not fuzzy counts.
"""

from __future__ import annotations

import polars as pl

from src.core.models import DataMap, Variable, VarType
from src.engines.qc.respondents import (
    detect_duplicates,
    detect_gibberish_open_ends,
    detect_speeders,
    detect_straightliners,
    run_respondent_qc,
    summarize_for_report,
)

_SCALE = {1: "1", 2: "2", 3: "3", 4: "4", 5: "5"}


def _grid_datamap() -> DataMap:
    """A 4-item same-scale grid (Q5r1..Q5r4) plus one non-grid single."""
    dm = DataMap()
    for i in range(1, 5):
        dm.add(
            Variable(name=f"Q5r{i}", label=f"Item {i}", type=VarType.SINGLE, values=dict(_SCALE))
        )
    dm.add(Variable(name="S1", label="Gender", type=VarType.SINGLE, values={1: "M", 2: "F"}))
    return dm


def test_straightliner_flagged_only_when_full_grid_identical():
    dm = _grid_datamap()
    df = pl.DataFrame(
        {
            # row 0 straightlines (all 3s); row 1 varies; row 2 identical but
            # has a null (not fully answered -> not flagged).
            "Q5r1": [3, 1, 4, 5],
            "Q5r2": [3, 2, 4, 4],
            "Q5r3": [3, 3, None, 5],
            "Q5r4": [3, 4, 4, 3],
            "S1": [1, 2, 1, 2],
        }
    )
    flagged, grids_checked = detect_straightliners(df, dm)
    assert grids_checked == 1
    assert flagged == [0]


def test_straightlining_skipped_without_grids():
    dm = DataMap()
    dm.add(Variable(name="S1", type=VarType.SINGLE, values={1: "M", 2: "F"}))
    df = pl.DataFrame({"S1": [1, 2]})
    flagged, grids_checked = detect_straightliners(df, dm)
    assert grids_checked == 0 and flagged == []


def test_speeders_use_median_third():
    dm = DataMap()
    dm.add(Variable(name="duration", type=VarType.NUMERIC))
    # median 600 -> threshold 200; rows 0 (100) and 3 (50) are speeders.
    df = pl.DataFrame({"duration": [100, 600, 900, 50, 600]})
    flagged, col = detect_speeders(df, dm)
    assert col == "duration"
    assert flagged == [0, 3]


def test_speeders_skipped_without_duration_column():
    dm = DataMap()
    dm.add(Variable(name="Q1", type=VarType.SINGLE, values={1: "a"}))
    df = pl.DataFrame({"Q1": [1, 1]})
    flagged, col = detect_speeders(df, dm)
    assert col is None and flagged == []


def test_duplicates_ignore_system_columns_and_keep_first():
    dm = DataMap()
    dm.add(Variable(name="Q1", type=VarType.SINGLE, values={1: "a", 2: "b"}))
    dm.add(Variable(name="Q2", type=VarType.NUMERIC))
    # uuid differs on every row but is NOT in the datamap -> ignored.
    df = pl.DataFrame(
        {
            "uuid": ["a", "b", "c", "d"],
            "Q1": [1, 2, 1, 1],
            "Q2": [10, 20, 10, 30],
        }
    )
    # rows 0 and 2 share the full answer pattern; only the LATER row is flagged.
    assert detect_duplicates(df, dm) == [2]


def test_gibberish_open_ends():
    dm = DataMap()
    dm.add(Variable(name="OE1", type=VarType.TEXT))
    df = pl.DataFrame(
        {
            "OE1": [
                "The delivery was late but support helped",  # real
                "asdfasdf",  # keyboard mash
                "aaaaaaa",  # repeated char
                "ok",  # short -> never flagged
                "dfgkjhdfkg",  # no vowels
                None,
            ]
        }
    )
    out = detect_gibberish_open_ends(df, dm)
    assert out == {"OE1": [1, 2, 4]}


def test_run_respondent_qc_summary_and_skips():
    dm = _grid_datamap()
    dm.add(Variable(name="OE1", type=VarType.TEXT))
    df = pl.DataFrame(
        {
            "Q5r1": [3, 1, 2, 2],
            "Q5r2": [3, 2, 2, 3],
            "Q5r3": [3, 3, 2, 4],
            "Q5r4": [3, 4, 2, 5],
            "S1": [1, 2, 1, 2],
            "OE1": ["fine", "qwerty mash", "good product", "meh"],
        }
    )
    result = run_respondent_qc(df, dm)
    # No duration column -> speeders skipped, honestly reported.
    assert "speeders" in result.checks_skipped
    # Rows 0 AND 2 straightline (0: all 3s, 2: all 2s).
    assert result.straightliners == [0, 2]
    assert result.gibberish_open_ends == {"OE1": [1]}
    assert result.flagged_rows() == {0, 1, 2}

    lines = summarize_for_report(result, len(df))
    assert any("straightlined" in line for line in lines)
    assert any("gibberish" in line for line in lines)
    assert not any("median time" in line for line in lines)  # speeders skipped

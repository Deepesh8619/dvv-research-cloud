"""Respondent-quality QC: speeders, straightliners, duplicates, gibberish OEs.

All synthetic data — each check gets a fixture where the flagged rows are known
by construction, so assertions are exact row indices, not fuzzy counts.
"""

from __future__ import annotations

import polars as pl

from src.core.models import DataMap, Variable, VarType
from src.engines.qc.respondents import (
    MIN_SPEEDER_N,
    detect_duplicates,
    detect_gibberish_open_ends,
    detect_speeders,
    detect_straightliners,
    respondent_qc_report,
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


def test_duplicates_ignore_record_and_paradata_columns():
    dm = DataMap()
    dm.add(Variable(name="Q1", type=VarType.SINGLE, values={1: "a", 2: "b"}))
    dm.add(Variable(name="Q2", type=VarType.NUMERIC))
    dm.add(Variable(name="record", type=VarType.NUMERIC))
    dm.add(Variable(name="vDurationSec", type=VarType.NUMERIC, provenance="fielding_paradata"))
    df = pl.DataFrame(
        {"record": [1, 2], "vDurationSec": [100.0, 250.0], "Q1": [1, 1], "Q2": [10, 10]}
    )
    assert detect_duplicates(df, dm) == [1]


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


# --- respondent_qc_report (the Steward tool entry point) ---


def _fielding_datamap() -> DataMap:
    """A materialized-fielding-shaped map: respondent_id, a 4-item grid, paradata."""
    dm = DataMap()
    dm.add(Variable(name="respondent_id", label="Respondent ID", type=VarType.NUMERIC))
    for i in range(1, 5):
        dm.add(
            Variable(name=f"Q5r{i}", label=f"Item {i}", type=VarType.SINGLE, values=dict(_SCALE))
        )
    dm.add(Variable(name="vDurationSec", label="Interview duration", type=VarType.NUMERIC))
    return dm


def test_report_flags_speeder_and_straightliner_by_respondent_id():
    dm = _fielding_datamap()
    n = 12
    df = pl.DataFrame(
        {
            "respondent_id": list(range(101, 101 + n)),
            # row0 (id 101) races through; median stays 600 -> threshold 200.
            "vDurationSec": [50] + [600] * 10 + [900],
            # row1 (id 102) straightlines all 3s; no other row is uniform.
            "Q5r1": [1, 3, 1, 2, 4, 5, 1, 2, 3, 4, 5, 1],
            "Q5r2": [2, 3, 4, 1, 3, 2, 5, 1, 2, 3, 4, 5],
            "Q5r3": [3, 3, 1, 5, 2, 4, 1, 3, 5, 2, 1, 4],
            "Q5r4": [4, 3, 2, 1, 5, 3, 2, 4, 1, 5, 3, 2],
        }
    )
    report = respondent_qc_report(df, dm)
    assert report["success"] and report["id_key"] == "respondent_id"
    assert report["flagged"]["speeders"] == [101]
    assert report["flagged"]["straightliners"] == [102]
    # A clean respondent appears in no flag list.
    assert 103 not in report["flagged"]["speeders"]
    assert 103 not in report["flagged"]["straightliners"]
    assert report["summary"]["speeders"] == 1
    assert report["summary"]["straightliners"] == 1


def test_report_non_fielded_study_degrades_gracefully():
    """No vDurationSec (a non-fielded/imported study) -> speeders skipped, no crash."""
    dm = DataMap()
    dm.add(Variable(name="S1", label="Gender", type=VarType.SINGLE, values={1: "M", 2: "F"}))
    df = pl.DataFrame({"S1": [1, 2, 1, 2]})
    report = respondent_qc_report(df, dm)
    assert report["success"]
    assert report["id_key"] == "row_index"  # no respondent_id column
    assert "speeders" in report["checks_skipped"]
    assert any("duration" in note for note in report["notes"])


def test_report_small_n_skips_speeder_check():
    dm = _fielding_datamap()
    df = pl.DataFrame(
        {
            "respondent_id": [1, 2, 3, 4, 5],
            "vDurationSec": [10, 600, 600, 600, 600],  # id 1 would be a speeder if run
            "Q5r1": [1, 2, 3, 4, 5],
            "Q5r2": [2, 3, 4, 5, 1],
            "Q5r3": [3, 4, 5, 1, 2],
            "Q5r4": [4, 5, 1, 2, 3],
        }
    )
    assert df.height < MIN_SPEEDER_N
    report = respondent_qc_report(df, dm)
    assert report["flagged"]["speeders"] == []
    assert "speeders" in report["checks_skipped"]
    assert "stable median" in report["checks_skipped"]["speeders"]


# --- Only grids a respondent actually filled in -----------------------------
# On a real 750-respondent study this check reported 89.3% straightlining — a
# number nobody would act on. The biggest contributor was HID_AWARE, a block of
# script-computed flags identical across items for 78% of respondents, as
# computed values often are. Excluding what nobody was asked took it to 16.4%.


def _grid(prefix, n=5, values=None, label="", parent=None):
    from src.core.models import Variable, VarType

    return [
        Variable(
            name=f"{prefix}r{i}",
            label=label or f"item {i}",
            type=VarType.SINGLE,
            values=values or {1: "Low", 2: "Mid", 3: "High"},
            question_ref=parent,
        )
        for i in range(1, n + 1)
    ]


def _map(variables):
    from src.core.models import DataMap

    dm = DataMap()
    for v in variables:
        dm.add(v)
    return dm


def test_a_real_rating_grid_is_still_checked():
    from src.engines.qc.respondents import _grid_groups

    grids = _grid_groups(_map(_grid("Q7")))
    assert len(grids) == 1
    assert grids[0][0] == "Q7r1"


def test_a_block_of_computed_flags_is_not_a_grid():
    """HID_AWARE is set by the script. Identical values across it are
    arithmetic, not a respondent rushing."""
    from src.engines.qc.respondents import _grid_groups

    assert _grid_groups(_map(_grid("HID_AWARE"))) == []


def test_a_hidden_block_named_only_in_its_label_is_also_excluded():
    from src.engines.qc.respondents import _grid_groups

    hidden = _grid("Hid_DA_ATTITUDES_1", label="Hidden variable to punch attitudes")
    assert _grid_groups(_map(hidden)) == []


def test_a_maxdiff_block_is_not_treated_as_a_rating_grid():
    """Choosing the same POSITION each task is ordinary MaxDiff behaviour —
    which is why scripters write their own MaxDiff straightliner flag rather
    than relying on this check."""
    from src.engines.qc.respondents import _grid_groups

    maxdiff = [
        v for v in _grid("MAXDIFF_ANCHOR", n=12)
    ]
    for v in maxdiff:
        v.name = v.name.replace("r", "_")  # MAXDIFF_ANCHOR_1 .. _12
    assert _grid_groups(_map(maxdiff)) == []


def test_excluding_computed_columns_changes_the_verdict_not_just_the_count():
    """Both blocks are perfectly flat. Only the asked one may be reported."""
    import polars as pl

    from src.engines.qc.respondents import detect_straightliners

    dm = _map(_grid("Q7") + _grid("HID_AWARE"))
    # Respondents 1 and 2 give the same answer to every item; respondent 3
    # varies. The computed block is flat for everyone, as computed blocks are.
    columns = {f"Q7r{i}": [2, 3, i] for i in range(1, 6)}
    columns.update({f"HID_AWAREr{i}": [1, 1, 1] for i in range(1, 6)})
    df = pl.DataFrame(columns)

    rows, n_grids = detect_straightliners(df, dm)
    assert n_grids == 1, "the computed block must not be counted as a grid"
    assert sorted(rows) == [0, 1], "respondent 3 varied and must not be flagged"


def test_duplicates_compare_fielded_question_columns():
    # Fielded question variables carry provenance "fielding"; excluding them
    # left nothing to compare, so a real duplicate was never found (rehearsal).
    dm = DataMap([
        Variable("Q1", type=VarType.SINGLE, values={1: "a", 2: "b"}, provenance="fielding"),
        Variable("Q2", type=VarType.SINGLE, values={1: "a", 2: "b"}, provenance="fielding"),
        Variable("vDurationSec", type=VarType.NUMERIC, provenance="fielding_paradata"),
    ])
    df = pl.DataFrame({"Q1": [1, 1, 2], "Q2": [2, 2, 1], "vDurationSec": [300, 410, 500]})
    assert detect_duplicates(df, dm) == [1]


def test_gibberish_skips_paradata_text():
    dm = DataMap([Variable("vConnection", type=VarType.TEXT, provenance="fielding_paradata")])
    df = pl.DataFrame({"vConnection": ["b7d0c1f3d9" * 3 + "bd"]})  # hex hash, no vowels
    assert detect_gibberish_open_ends(df, dm) == {}

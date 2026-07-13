"""Tests for the demo respondent-dataset generator (scripts/generate_demo_datasets.py).

Covers the frame contract (shape, exploded multi/grid columns, NPS routing,
determinism) plus one integration test proving the flagship xlsx sails through
the tabular intake adapter into a labeled (DataFrame, DataMap).
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from scripts.generate_demo_datasets import (
    QUESTIONNAIRES,
    generate_segment,
    write_segment,
)
from src.adapters.tabular import read_tabular
from src.core.models import VarType


@pytest.fixture(scope="module")
def fmcg_segment() -> dict:
    segments = json.loads(QUESTIONNAIRES.read_text())
    return next(s for s in segments if s["segment_slug"] == "fmcg-oral-care")


def test_frame_contract(fmcg_segment):
    df, specs, stats = generate_segment(fmcg_segment, seed=1000)

    assert len(df) == 500
    assert df["respondent_id"].tolist() == list(range(1, 501))

    # Exploded multi (Q2), grid (Q6), and detractor follow-up multi (Q8).
    for name in ["Q2r1", "Q2r7", "Q6r1", "Q6r5", "Q8r1", "Q8r6", "vDurationSec"]:
        assert name in df.columns, name

    # Routing: follow-up answered ONLY where NPS < 7, null everywhere else.
    nps = df["Q7"]
    followup_cols = [c for c in df.columns if c.startswith("Q8r")]
    for col in followup_cols:
        assert df.loc[nps >= 7, col].isna().all(), f"{col} should be null for non-detractors"
        assert df.loc[nps < 7, col].notna().all(), f"{col} should be answered for detractors"

    # Completes only: screener S1 (terminate under-18=code 1) never carries code 1.
    assert 1 not in set(df["S1"].unique())


def test_deterministic(fmcg_segment):
    df1, _, _ = generate_segment(fmcg_segment, seed=1000)
    df2, _, _ = generate_segment(fmcg_segment, seed=1000)
    pd.testing.assert_frame_equal(df1, df2)


def test_loads_through_tabular_adapter(fmcg_segment, tmp_path):
    df_gen, specs, _ = generate_segment(fmcg_segment, seed=1000)
    # Point the writer at tmp_path so the test stays hermetic.
    import scripts.generate_demo_datasets as gen

    gen.OUT_DIR = tmp_path
    write_segment("fmcg-oral-care", df_gen, specs)

    df, datamap = read_tabular(tmp_path / "fmcg-oral-care.xlsx")

    assert len(df) == 500
    assert len(datamap) == df.shape[1]

    def var(name):
        return next(v for v in datamap.variables if v.name == name)

    # Multi dummy: labeled Selected/Not selected (from the Datamap sheet, not guessed).
    q2 = var("Q2r1")
    assert q2.type == VarType.SINGLE
    assert q2.values == {0: "Not selected", 1: "Selected"}

    # Grid item: real 5-point satisfaction labels.
    q6 = var("Q6r1")
    assert q6.type == VarType.SINGLE
    assert q6.values[1] == "Very dissatisfied" and q6.values[5] == "Very satisfied"

    # NPS numeric and the duration column stay numeric.
    assert var("Q7").type == VarType.NUMERIC
    assert var("vDurationSec").type == VarType.NUMERIC

"""Regression: mixed-type open-end columns must survive pl.from_pandas().

An Excel export preserves each cell's native type, so an open-end column can
hold genuine mixed Python types — some cells str ("none"), some int (5). Left as
a raw pandas object column, PyArrow (called by pl.from_pandas() in the
orchestrator's load step, before QC) infers the column as string from the first
value and then raises "Expected bytes, got a 'int' object" on the first int
cell — the production failure that surfaced as a QC-stage error on study 2509100.
read_tabular now normalizes open-end cells to str, so the column loads cleanly.
"""

from __future__ import annotations

import openpyxl
import polars as pl

from src.adapters.tabular import read_tabular
from src.core.models import VarType


def _write_xlsx(path, header: list[str], rows: list[list]) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(header)
    for r in rows:
        ws.append(r)
    wb.save(path)


def test_mixed_type_open_end_loads_and_pl_from_pandas_succeeds(tmp_path):
    # OE1: >25 distinct values so it's classified TEXT, with cells that are a
    # mix of native strings and native ints — exactly what openpyxl yields.
    open_ends = [f"comment {i}" for i in range(30)] + [5, 3, 7, 12, "none", "n/a"]
    rows = [[(i % 4) + 1, open_ends[i]] for i in range(len(open_ends))]
    path = tmp_path / "mixed.xlsx"
    _write_xlsx(path, ["Q1", "OE1"], rows)

    df_pd, datamap = read_tabular(path)

    assert datamap["OE1"].type == VarType.TEXT
    # The exact call that used to raise ArrowTypeError.
    plf = pl.from_pandas(df_pd)
    assert plf["OE1"].dtype == pl.String
    # Values preserved as strings; the int cells are now "5", "3", ... not lost.
    assert "5" in plf["OE1"].to_list()
    assert "none" in plf["OE1"].to_list()

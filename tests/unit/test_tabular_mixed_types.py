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


def test_cp1252_csv_is_read_not_crashed(tmp_path) -> None:
    """A curly apostrophe from Excel/SPSS must not kill the upload.

    Byte 0x92 is a right single quote in cp1252 and invalid UTF-8; every US
    federal and Excel-authored export carries them. Before the fallback this
    raised UnicodeDecodeError straight out of pandas and up to the browser.
    """
    csv = tmp_path / "cp1252.csv"
    # Written as raw bytes: 0x92 is the cp1252 right single quote and is not a
    # legal UTF-8 lead byte, which is exactly the file the crash came from.
    csv.write_bytes(b"Q1,Q2\r\nIt\x92s cheaper to rent,1\r\nDon\x92t know,2\r\n")
    df, datamap = read_tabular(csv)
    assert len(df) == 2
    assert "Q1" in {v.name for v in datamap.variables}

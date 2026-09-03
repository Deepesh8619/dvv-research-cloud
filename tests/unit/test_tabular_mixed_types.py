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


# --- one column, one type: the load step cannot be killed by one cell --------


def _workbook_with_datamap(path, data: dict, datamap_rows: list[str]) -> None:
    """An .xlsx with a companion Datamap sheet, the shape a client sends."""
    import pandas as pd

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pd.DataFrame(data).to_excel(writer, sheet_name="Data", index=False)
        pd.DataFrame({"A": datamap_rows}).to_excel(
            writer, sheet_name="Datamap", index=False, header=False
        )


def test_a_codebook_coded_column_holding_mixed_cells_still_loads(tmp_path) -> None:
    """The upload died at the load step, before QC, over one cell.

    A column the codebook declares as coded skips the inference branch
    entirely, so nothing ever made its cells agree with each other — and
    PyArrow raised "Expected bytes, got a 'int' object" for the whole file.
    """
    import polars as pl

    path = tmp_path / "client.xlsx"
    _workbook_with_datamap(
        path,
        {"Q1": [1, "2", 3, None], "Q2": [1, 1, 2, 2]},
        ["[Q1]: Are you employed?", "Values: 1-3", "1 Yes", "2 No", "3 Maybe"],
    )

    df, datamap = read_tabular(path)

    # The real assertion: the frame survives the conversion that was crashing.
    assert pl.from_pandas(df).height == 4
    # Pin the branch: a codebook entry wins and skips inference entirely, which
    # is precisely why nothing was normalising this column.
    assert datamap["Q1"].provenance == "datamap_sheet"
    assert datamap["Q1"].type == VarType.SINGLE  # still coded — the cells read as numbers
    assert df["Q1"].tolist()[:3] == [1.0, 2.0, 3.0]


def test_a_coded_column_that_is_really_words_is_recorded_as_text(tmp_path) -> None:
    """A codebook can be wrong. Leaving it claiming codes it has not is worse.

    Every later comparison is made against the data map, so a data map that
    says "coded" about a column of sentences makes each of those comparisons
    meaningless.
    """
    import polars as pl

    path = tmp_path / "client.xlsx"
    _workbook_with_datamap(
        path,
        {"Q1": ["it was fine", "too slow", 4, "nothing to add"], "Q2": [1, 1, 2, 2]},
        ["[Q1]: Are you employed?", "Values: 1-3", "1 Yes", "2 No", "3 Maybe"],
    )

    df, datamap = read_tabular(path)

    assert pl.from_pandas(df).height == 4
    assert datamap["Q1"].provenance == "datamap_sheet"
    assert datamap["Q1"].type == VarType.TEXT
    assert datamap["Q1"].values == {}
    assert df["Q1"].tolist()[2] == "4"  # the stray number kept, as written


def test_no_column_leaves_the_adapter_holding_two_types(tmp_path) -> None:
    """The invariant, stated once: parquet allows a column exactly one type."""
    import polars as pl

    csv = tmp_path / "mixed.csv"
    csv.write_bytes(
        b"OE,CODED,NUM\r\n"
        b"it was fine,1,10\r\n"
        b"5,2,20\r\n"
        b"nothing to add,x,30\r\n"
    )

    df, _ = read_tabular(csv)

    assert all(df[c].map(type).nunique(dropna=True) == 1 for c in df.columns)
    assert pl.from_pandas(df).height == 3

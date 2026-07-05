"""Companion Datamap-sheet parsing and merge into the tabular adapter."""

import openpyxl
import pytest

from src.adapters.datamap_sheet import find_datamap_sheet, parse_datamap_sheet
from src.adapters.tabular import read_tabular
from src.core.models import VarType


def _write_datamap_block(ws, row: int, name: str, label: str, kind: str, values=None) -> int:
    """Write one variable block starting at `row`; returns the next free row."""
    ws.cell(row=row, column=1, value=f"[{name}]: {label}")
    row += 1
    if kind == "numeric":
        ws.cell(row=row, column=1, value="Open numeric response")
        row += 1
    elif kind == "text":
        ws.cell(row=row, column=1, value="Open text response")
        row += 1
    else:
        lo, hi = min(values), max(values)
        ws.cell(row=row, column=1, value=f"Values: {lo}-{hi}")
        row += 1
        for code, text in values.items():
            ws.cell(row=row, column=2, value=code)
            ws.cell(row=row, column=3, value=text)
            row += 1
    row += 1  # blank separator
    return row


@pytest.fixture
def two_sheet_workbook(tmp_path):
    """A Decipher-style workbook: data on 'A1', codebook on 'Datamap'."""
    wb = openpyxl.Workbook()
    data_ws = wb.active
    data_ws.title = "A1"
    data_ws.append(["respondent_id", "status", "gender", "feedback"])
    data_ws.append([1, 3, 1, "loved it"])
    data_ws.append([2, 4, 2, "too slow"])
    data_ws.append([3, 3, 1, "great value"])

    dm_ws = wb.create_sheet("Datamap")
    row = 1
    row = _write_datamap_block(dm_ws, row, "respondent_id", "Respondent ID", "numeric")
    row = _write_datamap_block(
        dm_ws, row, "status", "Participant status", "single",
        values={1: "Terminated", 2: "Overquota", 3: "Qualified", 4: "Partial"},
    )
    row = _write_datamap_block(
        dm_ws, row, "gender", "Gender", "single", values={1: "Male", 2: "Female"}
    )
    _write_datamap_block(dm_ws, row, "feedback", "Open feedback", "text")

    path = tmp_path / "study.xlsx"
    wb.save(path)
    return path


def test_find_datamap_sheet_matches_unambiguous_pair():
    assert find_datamap_sheet(["A1", "Datamap"]) == "Datamap"
    assert find_datamap_sheet(["Sheet1", "Codebook"]) == "Codebook"


def test_find_datamap_sheet_ignores_ambiguous_workbooks():
    assert find_datamap_sheet(["Sheet1"]) is None  # single sheet, nothing to merge
    assert find_datamap_sheet(["A1", "A2", "Datamap"]) is None  # 3 sheets, ambiguous
    assert find_datamap_sheet(["A1", "B1"]) is None  # no datamap-named sheet


def test_parse_datamap_sheet_extracts_real_labels(two_sheet_workbook):
    dm = parse_datamap_sheet(two_sheet_workbook, "Datamap")
    assert dm["status"].type == VarType.SINGLE
    assert dm["status"].values == {1: "Terminated", 2: "Overquota", 3: "Qualified", 4: "Partial"}
    assert dm["gender"].values == {1: "Male", 2: "Female"}
    assert dm["respondent_id"].type == VarType.NUMERIC
    assert dm["feedback"].type == VarType.TEXT


def test_read_tabular_merges_real_labels_instead_of_guessing(two_sheet_workbook):
    df, datamap = read_tabular(two_sheet_workbook)

    status = datamap["status"]
    assert status.label == "Participant status"
    # Without the datamap sheet, tabular.py would have guessed values={3: "3", 4: "4"}
    assert status.values[3] == "Qualified"
    assert status.values[4] == "Partial"

    gender = datamap["gender"]
    assert gender.values == {1: "Male", 2: "Female"}

    assert len(df) == 3
    assert "status" in df.columns

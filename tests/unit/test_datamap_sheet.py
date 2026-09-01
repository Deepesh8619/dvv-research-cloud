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


# --- Multi-punch blocks -----------------------------------------------------
# On a real 440-column client export these were 189 columns — 48% of the file
# and nearly every multi-select question in the study — silently absent from
# the parsed datamap. The cause was a one-character difference in the
# convention: a punch column is written "[NAMEr1] | label" in the SECOND
# column, under an UNBRACKETED parent header, not "[NAME]: label" in the first.


def _multi_sheet(tmp_path):
    """A datamap laid out exactly as the real client export lays one out."""
    import pandas as pd

    rows = [
        ["[RESP_AGE]: What is your age?", None, None],
        ["Open numeric response", None, None],
        ["", None, None],
        ["DA_GOALS: Which objectives do you seek?", None, None],
        ["Values: 0-1", None, None],
        ["", 0, "Unchecked"],
        ["", 1, "Checked"],
        ["", "[DA_GOALSr1]", "Generate new customer leads"],
        ["", "[DA_GOALSr2]", "Increase visits to website"],
        ["", "[DA_GOALSr98]", "Other, specify:"],
        ["", None, None],
        ["[DA_TOP_GOAL]: What is the top objective?", None, None],
        ["Values: 1-2", None, None],
        ["", 1, "Leads"],
        ["", 2, "Visits"],
    ]
    path = tmp_path / "dm.xlsx"
    pd.DataFrame(rows).to_excel(path, sheet_name="Datamap", header=False, index=False)
    return path


def test_multi_punch_columns_become_real_variables(tmp_path):
    dm = parse_datamap_sheet(_multi_sheet(tmp_path), "Datamap")
    names = {v.name for v in dm.variables}
    assert {"DA_GOALSr1", "DA_GOALSr2", "DA_GOALSr98"} <= names


def test_each_punch_keeps_its_own_row_label(tmp_path):
    """The row label is the only thing that identifies which option a punch is."""
    dm = parse_datamap_sheet(_multi_sheet(tmp_path), "Datamap")
    by = {v.name: v for v in dm.variables}
    assert by["DA_GOALSr1"].label == "Generate new customer leads"
    assert by["DA_GOALSr98"].label == "Other, specify:"


def test_a_punch_points_back_at_its_parent_question(tmp_path):
    """Without question_ref the columns cannot be grouped back into a question."""
    dm = parse_datamap_sheet(_multi_sheet(tmp_path), "Datamap")
    by = {v.name: v for v in dm.variables}
    assert by["DA_GOALSr1"].question_ref == "DA_GOALS"


def test_the_parent_itself_is_not_emitted_as_a_column(tmp_path):
    """DA_GOALS is a question, not a column in the data — emitting it would
    invent a variable that no data file contains."""
    dm = parse_datamap_sheet(_multi_sheet(tmp_path), "Datamap")
    assert "DA_GOALS" not in {v.name for v in dm.variables}


def test_punches_inherit_the_blocks_shared_value_labels(tmp_path):
    dm = parse_datamap_sheet(_multi_sheet(tmp_path), "Datamap")
    by = {v.name: v for v in dm.variables}
    assert by["DA_GOALSr1"].values == {0: "Unchecked", 1: "Checked"}


def test_a_multi_block_does_not_swallow_the_variable_after_it(tmp_path):
    """The block must stop at the next header, or one bad block eats the rest
    of the sheet."""
    dm = parse_datamap_sheet(_multi_sheet(tmp_path), "Datamap")
    by = {v.name: v for v in dm.variables}
    assert "DA_TOP_GOAL" in by
    assert by["DA_TOP_GOAL"].values == {1: "Leads", 2: "Visits"}
    assert by["DA_TOP_GOAL"].question_ref is None
    assert "RESP_AGE" in by  # the plain block before it survived too

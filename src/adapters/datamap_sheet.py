"""Parse a companion 'Datamap' sheet exported alongside survey data.

Decipher/Confirmit-style workbooks often ship two sheets: the respondent
data, and a codebook ("Datamap") describing every variable. `tabular.py`
alone can only *infer* types/labels from the raw codes (§3.2's documented
gap: real labels show as "1", "2", "98" instead of "Terminated", "Qualified").
When a companion datamap sheet exists, this module parses its real labels so
imports get the same label quality as an SPSS file — no inference needed.

Expected block format, one variable per block, blank-row separated:

    [varname]: Variable label
    Open numeric response          <- OR: Open text response
                                    <- OR: "Values: <range>" followed by
                                       (code, label) rows until the next
                                       blank row or variable header
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from src.core.models import DataMap, Variable, VarType

_VAR_HEADER_RE = re.compile(r"^\[(\w+)\]:\s*(.*)$")

# A multi-punch question is written as an UNBRACKETED parent header whose
# individual columns are listed underneath, in the second column, bracketed:
#
#     DA_GOALS: Which business objectives do you seek?   <- parent, NOT a column
#     Values: 0-1
#              0            Unchecked
#              1            Checked
#              [DA_GOALSr1] Generate new customer leads  <- IS a column
#              [DA_GOALSr2] Increase visits to website   <- IS a column
#
# Only the bracketed-with-colon form was recognised, so every punch column was
# skipped: on a real 440-column export that hid 189 columns — 48% of the file,
# and nearly every multi-select question in the study. A one-character
# difference in the convention (`]:` vs `] |`) was the whole cause.
_PARENT_HEADER_RE = re.compile(r"^(\w+):\s*(.*)$")
_CHILD_COLUMN_RE = re.compile(r"^\[(\w+)\]$")

# Sheet names recognized as a companion datamap/codebook, not respondent data.
DATAMAP_SHEET_NAMES = {"datamap", "data map", "codebook", "labels"}


def find_datamap_sheet(sheet_names: list[str]) -> str | None:
    """Return the sheet name that looks like a companion datamap, if unambiguous.

    Only fires for the clear-cut case (exactly 2 sheets, exactly one matches
    a known datamap name) so an unrelated multi-sheet workbook falls back to
    the existing single-sheet inference path rather than guessing wrong.
    """
    matches = [s for s in sheet_names if s.strip().lower() in DATAMAP_SHEET_NAMES]
    if len(matches) == 1 and len(sheet_names) == 2:
        return matches[0]
    return None


def _consume_multi_block(
    datamap: DataMap,
    raw,
    col0,
    n: int,
    i: int,
    parent_name: str,
    parent_label: str,
    cell,
) -> int:
    """Read one multi-punch block, emitting a Variable per real data column.

    The parent itself is NOT a column in the data and is deliberately not
    emitted; `question_ref` on each child carries the parent name so callers
    can group them back into one question.
    """
    i += 1  # step past the parent header
    shared_values: dict[int, str] = {}
    children: list[tuple[str, str]] = []

    while i < n:
        if _VAR_HEADER_RE.match(col0.iloc[i]):
            break
        first = col0.iloc[i]
        if first and not first.startswith(("Values:", "Open ")):
            break  # the next parent header
        second, third = cell(i, 1), cell(i, 2)
        child = _CHILD_COLUMN_RE.match(second)
        if child:
            children.append((child.group(1), third))
        elif second:
            try:
                shared_values[int(float(second))] = third
            except (TypeError, ValueError):
                pass  # a stray row; the block's shape is not worth failing over
        i += 1

    for name, label in children:
        try:
            datamap.add(
                Variable(
                    name=name,
                    label=label or parent_label,
                    type=VarType.SINGLE,
                    values=dict(shared_values),
                    question_ref=parent_name,
                    # The punch label says WHICH option; the parent header says
                    # what was asked. Both are needed to rebuild the question,
                    # and only the punch label survives in `label`.
                    question_text=parent_label,
                    provenance="datamap_sheet",
                )
            )
        except ValueError:
            pass  # duplicate name — keep the first definition
    return i


def parse_datamap_sheet(path: str | Path, sheet_name: str) -> DataMap:
    """Parse a Decipher/Confirmit-style datamap sheet into canonical Variables."""
    raw = pd.read_excel(path, sheet_name=sheet_name, header=None)
    # pandas 2.x's astype(str) leaves real NaN as NaN (not the string "nan"),
    # so blank-row / header-regex checks need an explicit isna guard instead
    # of comparing against a stringified sentinel.
    col0 = raw[0].apply(lambda v: "" if pd.isna(v) else str(v))
    n = len(raw)

    def _cell(row: int, col: int) -> str:
        if raw.shape[1] <= col:
            return ""
        value = raw.iat[row, col]
        return "" if pd.isna(value) else str(value).strip()

    datamap = DataMap()
    i = 0
    while i < n:
        header = _VAR_HEADER_RE.match(col0.iloc[i])
        if not header:
            parent = _PARENT_HEADER_RE.match(col0.iloc[i])
            # Guard against matching "Values: 0-1" and other prose: a real
            # parent header is followed by a type marker, exactly like a
            # bracketed one.
            following = col0.iloc[i + 1] if i + 1 < n else ""
            if parent and (
                following.startswith("Values:") or following.startswith("Open ")
            ):
                i = _consume_multi_block(
                    datamap, raw, col0, n, i, parent.group(1), parent.group(2).strip(), _cell
                )
                continue
            i += 1
            continue
        name, label = header.group(1), header.group(2).strip()
        i += 1

        var_type = VarType.TEXT
        values: dict[int, str] = {}
        if i < n:
            marker = col0.iloc[i]
            if marker == "Open numeric response":
                var_type = VarType.NUMERIC
                i += 1
            elif marker == "Open text response":
                var_type = VarType.TEXT
                i += 1
            elif marker.startswith("Values:"):
                var_type = VarType.SINGLE
                i += 1
                while i < n and not _VAR_HEADER_RE.match(col0.iloc[i]):
                    code = raw.iat[i, 1] if raw.shape[1] > 1 else None
                    if pd.notna(code):
                        text_label = raw.iat[i, 2] if raw.shape[1] > 2 else ""
                        values[int(code)] = str(text_label) if pd.notna(text_label) else ""
                        i += 1
                    elif col0.iloc[i] == "":
                        i += 1
                        break
                    else:
                        i += 1

        try:
            datamap.add(
                Variable(
                    name=name,
                    label=label,
                    type=var_type,
                    values=values,
                    provenance="datamap_sheet",
                )
            )
        except ValueError:
            pass  # duplicate variable name in the sheet — keep the first definition

    return datamap

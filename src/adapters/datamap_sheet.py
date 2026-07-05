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


def parse_datamap_sheet(path: str | Path, sheet_name: str) -> DataMap:
    """Parse a Decipher/Confirmit-style datamap sheet into canonical Variables."""
    raw = pd.read_excel(path, sheet_name=sheet_name, header=None)
    # pandas 2.x's astype(str) leaves real NaN as NaN (not the string "nan"),
    # so blank-row / header-regex checks need an explicit isna guard instead
    # of comparing against a stringified sentinel.
    col0 = raw[0].apply(lambda v: "" if pd.isna(v) else str(v))
    n = len(raw)

    datamap = DataMap()
    i = 0
    while i < n:
        header = _VAR_HEADER_RE.match(col0.iloc[i])
        if not header:
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

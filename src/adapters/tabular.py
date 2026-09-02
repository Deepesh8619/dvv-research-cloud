"""CSV/Excel adapter: tabular survey exports → canonical DataMap + DataFrame.

Real-world inputs here are Decipher/Qualtrics-style datafeed exports: one row
per respondent, hundreds of columns mixing system fields (uuid, markers),
categorical codes, numerics, and open-end text. Unlike SPSS there is no
embedded metadata, so variable types and value labels are usually *inferred*
from the data itself (architecture §adapters: every format maps into the
canonical model; engines never see the source format) — UNLESS the workbook
ships a companion "Datamap" sheet (see datamap_sheet.py), in which case real
labels are parsed from it instead of guessed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from src.adapters.datamap_sheet import find_datamap_sheet, parse_datamap_sheet
from src.core.models import DataMap, Variable, VarType

# A numeric column whose distinct values are all integers and few in number is
# treated as single-coded categorical; beyond this it's a real numeric.
MAX_CATEGORY_CODES = 15
# A string column with few distinct values is categorical (encoded to codes);
# beyond this it's open-end text.
MAX_STRING_CATEGORIES = 25


def _safe_name(name: str, taken: set[str]) -> str:
    """Sanitize a column name into a SQL-safe identifier, keeping it unique."""
    safe = re.sub(r"\W", "_", str(name).strip())
    if not safe or safe[0].isdigit():
        safe = f"v_{safe}"
    base, n = safe, 2
    while safe in taken:
        safe = f"{base}_{n}"
        n += 1
    return safe


def _is_integral(series: pd.Series) -> bool:
    values = series.dropna()
    if values.empty:
        return False
    return bool(((values % 1) == 0).all())


# Survey exports are not reliably UTF-8. SPSS, Excel-on-Windows and most
# government data portals emit cp1252, so a single curly apostrophe in an
# option label (byte 0x92) raised UnicodeDecodeError and the whole upload died
# — with the raw traceback, which is not an error a client should ever see.
# cp1252 decodes every byte 0x00-0xFF, so the fallback cannot fail in turn.
_CSV_ENCODINGS = ("utf-8", "cp1252")


def _read_csv_any_encoding(path: Path) -> pd.DataFrame:
    """Read a CSV, trying UTF-8 first and falling back to cp1252."""
    last: UnicodeDecodeError | None = None
    for encoding in _CSV_ENCODINGS:
        try:
            return pd.read_csv(path, low_memory=False, encoding=encoding)
        except UnicodeDecodeError as exc:  # pragma: no cover - cp1252 never raises
            last = exc
    raise last  # type: ignore[misc]


def read_tabular(path: str | Path) -> tuple[pd.DataFrame, DataMap]:
    """Read a CSV/XLSX export into a DataFrame plus a DataMap.

    String categoricals are re-encoded to integer codes (with the original
    strings kept as value labels) so the DuckDB crosstab engine can treat
    every categorical identically to an SPSS import. If the workbook has a
    companion "Datamap"/"Codebook" sheet, real labels are parsed from it
    (datamap_sheet.py) instead of guessed from the raw codes.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    parsed_vars: dict[str, Variable] = {}
    if suffix == ".csv":
        df = _read_csv_any_encoding(path)
        provenance = "csv_import"
    elif suffix in (".xlsx", ".xls"):
        sheet_names = pd.ExcelFile(path).sheet_names
        datamap_sheet = find_datamap_sheet(sheet_names)
        if datamap_sheet:
            data_sheet = next(s for s in sheet_names if s != datamap_sheet)
            df = pd.read_excel(path, sheet_name=data_sheet)
            parsed_vars = {v.name: v for v in parse_datamap_sheet(path, datamap_sheet).variables}
        else:
            df = pd.read_excel(path)
        provenance = "excel_import"
    else:
        raise ValueError(f"not a tabular file: {path.name}")

    # Drop columns with no data at all — they carry nothing for any engine.
    df = df.dropna(axis=1, how="all")

    taken: set[str] = set()
    renames: dict[str, str] = {}
    datamap = DataMap()

    for col in df.columns:
        name = _safe_name(col, taken)
        taken.add(name)
        renames[col] = name
        label = str(col)
        series = df[col]

        parsed = parsed_vars.get(str(col))
        if parsed is not None:
            # A real codebook entry beats inference — same labels an SPSS
            # import would have carried natively.
            datamap.add(
                Variable(
                    name=name,
                    label=parsed.label or label,
                    type=parsed.type,
                    values=dict(parsed.values),
                    provenance="datamap_sheet",
                )
            )
            continue

        if pd.api.types.is_datetime64_any_dtype(series):
            datamap.add(Variable(name=name, label=label, type=VarType.DATE, provenance=provenance))
            continue

        if pd.api.types.is_numeric_dtype(series):
            uniques = series.dropna().unique()
            if 1 <= len(uniques) <= MAX_CATEGORY_CODES and _is_integral(series):
                values = {int(v): str(int(v)) for v in sorted(uniques)}
                datamap.add(
                    Variable(
                        name=name,
                        label=label,
                        type=VarType.SINGLE,
                        values=values,
                        provenance=provenance,
                    )
                )
            else:
                datamap.add(
                    Variable(name=name, label=label, type=VarType.NUMERIC, provenance=provenance)
                )
            continue

        # Object/string columns: few distinct values → categorical (encode to
        # integer codes); otherwise open-end text.
        as_str = series.dropna().astype(str)
        uniques = sorted(as_str.unique())
        if 1 <= len(uniques) <= MAX_STRING_CATEGORIES:
            code_of = {v: i + 1 for i, v in enumerate(uniques)}
            df[col] = series.map(
                lambda v, _codes=code_of: _codes.get(str(v)) if pd.notna(v) else None
            )
            datamap.add(
                Variable(
                    name=name,
                    label=label,
                    type=VarType.SINGLE,
                    values={c: v for v, c in code_of.items()},
                    provenance=provenance,
                )
            )
        else:
            datamap.add(Variable(name=name, label=label, type=VarType.TEXT, provenance=provenance))

    df = df.rename(columns=renames)

    # Normalize every TEXT (open-end) column to a uniform string dtype. A raw
    # object column can hold mixed Python types — respondents type "none" in one
    # cell and 5 in another, and Excel preserves each cell's native type — which
    # makes PyArrow (via pl.from_pandas() in the orchestrator's load step, before
    # QC) infer the column as string from the first value and then raise
    # "Expected bytes, got a 'int' object" on the first int cell. This is the
    # production failure that surfaced as a QC-stage error. Covers both the
    # inferred TEXT path above and datamap-sheet-parsed TEXT columns; nulls stay
    # null so missingness/coding downstream still see them as empty.
    text_cols = [v.name for v in datamap.variables if v.type == VarType.TEXT]
    for name in text_cols:
        df[name] = df[name].map(lambda v: str(v) if pd.notna(v) else None)

    return df, datamap

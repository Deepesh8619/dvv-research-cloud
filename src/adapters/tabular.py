"""CSV/Excel adapter: tabular survey exports → canonical DataMap + DataFrame.

Real-world inputs here are Decipher/Qualtrics-style datafeed exports: one row
per respondent, hundreds of columns mixing system fields (uuid, markers),
categorical codes, numerics, and open-end text. Unlike SPSS there is no
embedded metadata, so variable types and value labels are inferred from the
data itself (architecture §adapters: every format maps into the canonical
model; engines never see the source format).
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

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


def read_tabular(path: str | Path) -> tuple[pd.DataFrame, DataMap]:
    """Read a CSV/XLSX export into a DataFrame plus an inferred DataMap.

    String categoricals are re-encoded to integer codes (with the original
    strings kept as value labels) so the DuckDB crosstab engine can treat
    every categorical identically to an SPSS import.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        df = pd.read_csv(path, low_memory=False)
        provenance = "csv_import"
    elif suffix in (".xlsx", ".xls"):
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
    return df, datamap

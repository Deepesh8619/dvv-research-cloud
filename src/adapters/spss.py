"""SPSS adapter: .sav → Arrow/Parquet + canonical DataMap.

Convert once at intake, never re-parse (architecture §7.3). The original .sav is
preserved for audit; all downstream compute reads the Parquet.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pyreadstat

from src.core.models import DataMap, Variable, VarType


def _infer_type(name: str, meta: pyreadstat.metadata_container) -> VarType:
    readstat_type = meta.readstat_variable_types.get(name, "double")
    has_value_labels = name in meta.variable_value_labels
    measure = (meta.variable_measure or {}).get(name, "unknown")

    if readstat_type == "string":
        return VarType.TEXT
    if has_value_labels or measure in ("nominal", "ordinal"):
        return VarType.SINGLE
    return VarType.NUMERIC


def read_sav(path: str | Path) -> tuple[pd.DataFrame, DataMap]:
    """Read a .sav into a DataFrame plus the canonical DataMap."""
    df, meta = pyreadstat.read_sav(str(path))

    datamap = DataMap()
    for name in meta.column_names:
        value_labels = meta.variable_value_labels.get(name, {})
        datamap.add(
            Variable(
                name=name,
                label=meta.column_names_to_labels.get(name) or "",
                type=_infer_type(name, meta),
                values={int(code): str(lab) for code, lab in value_labels.items()},
                provenance="spss_import",
            )
        )
    return df, datamap


def sav_to_parquet(sav_path: str | Path, parquet_path: str | Path) -> DataMap:
    """Convert a .sav to Parquet and return the extracted DataMap."""
    df, datamap = read_sav(sav_path)
    table = pa.Table.from_pandas(df, preserve_index=False)
    pq.write_table(table, str(parquet_path), compression="zstd")
    return datamap


def write_sav(
    df: pd.DataFrame, datamap: DataMap, path: str | Path
) -> None:
    """Render a canonical study back to .sav (the industry interchange format)."""
    pyreadstat.write_sav(
        df,
        str(path),
        column_labels={v.name: v.label for v in datamap.variables if v.label},
        variable_value_labels={
            v.name: {code: lab for code, lab in v.values.items()}
            for v in datamap.variables
            if v.values
        },
    )

"""Data quality report: deterministic per-column diagnostics on DuckDB.

No LLM involved — this computes numbers the navigator LLM cites, never
invents. Flags the classic survey-data smells: heavily-missing columns,
constant columns (no variance, useless for any crosstab), and respondent-id
columns that look duplicated (a common upstream export bug).
"""

from __future__ import annotations

from pathlib import Path

import duckdb

MISSING_PCT_FLAG = 50.0
TOP_N_CATEGORICAL = 5


def data_quality(parquet_path: Path, variables: list[str] | None = None) -> dict:
    """Compute a per-column quality report over the respondent data.

    Args:
        parquet_path: path to data.parquet for the study.
        variables: optional subset of columns to check; None = all columns.

    Returns a report with one entry per column (missing %, distinct count,
    numeric min/max or top categorical values) plus a flags summary:
    high-missing columns, constant columns, and columns that look like
    respondent IDs but contain duplicates.
    """
    if not parquet_path.exists():
        return {"success": False, "error": "Respondent data not available for this study"}

    con = duckdb.connect()
    try:
        con.execute("SET memory_limit = '256MB'")
        con.execute("SET threads = 2")
        con.execute(f"CREATE VIEW data AS SELECT * FROM read_parquet('{parquet_path}')")

        schema = con.execute("DESCRIBE data").fetchall()
        available = {row[0]: row[1] for row in schema}

        if variables:
            bad = [v for v in variables if v not in available]
            if bad:
                return {"success": False, "error": f"Unknown column(s): {bad}"}
            columns = variables
        else:
            columns = list(available.keys())

        total_rows = con.execute("SELECT COUNT(*) FROM data").fetchone()[0]

        report: dict[str, dict] = {}
        high_missing: list[str] = []
        constant_cols: list[str] = []
        duplicate_id_cols: list[str] = []

        for col in columns:
            duckdb_type = available[col]
            missing = con.execute(
                f'SELECT COUNT(*) FROM data WHERE "{col}" IS NULL'
            ).fetchone()[0]
            missing_pct = 100.0 * missing / total_rows if total_rows else 0.0
            distinct = con.execute(f'SELECT COUNT(DISTINCT "{col}") FROM data').fetchone()[0]

            entry: dict = {
                "type": duckdb_type,
                "missing_pct": round(missing_pct, 2),
                "distinct_count": distinct,
            }

            is_numeric = any(
                t in duckdb_type.upper() for t in ("INT", "DOUBLE", "FLOAT", "DECIMAL", "BIGINT")
            )
            if is_numeric:
                row = con.execute(
                    f'SELECT MIN("{col}"), MAX("{col}") FROM data WHERE "{col}" IS NOT NULL'
                ).fetchone()
                entry["min"], entry["max"] = row[0], row[1]
            else:
                top = con.execute(
                    f'SELECT "{col}", COUNT(*) AS n FROM data '
                    f'WHERE "{col}" IS NOT NULL GROUP BY "{col}" '
                    f"ORDER BY n DESC LIMIT {TOP_N_CATEGORICAL}"
                ).fetchall()
                entry["top_values"] = [{"value": v, "count": n} for v, n in top]

            if missing_pct > MISSING_PCT_FLAG:
                high_missing.append(col)
            if distinct <= 1 and total_rows > 0:
                constant_cols.append(col)

            # Respondent-id heuristic: name suggests an identifier, but distinct
            # count is less than total rows — implies duplicated IDs upstream.
            if "id" in col.lower() and distinct < total_rows and missing == 0:
                duplicate_id_cols.append(col)

            report[col] = entry

    except Exception as exc:
        return {"success": False, "error": str(exc)}
    finally:
        con.close()

    return {
        "success": True,
        "row_count": total_rows,
        "columns": report,
        "flags": {
            "high_missing_columns": high_missing,
            "constant_columns": constant_cols,
            "likely_duplicate_id_columns": duplicate_id_cols,
        },
    }

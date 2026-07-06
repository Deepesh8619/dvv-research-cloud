"""Pivot tables over respondent data: GROUP BY + aggregation on DuckDB.

Distinct from the crosstab engine (which computes column % + significance
letters against a banner). A pivot is a plain rows x columns aggregation —
count, mean, sum, min, max — with no statistical testing. Useful for "how
many X by Y" style questions that don't need sig letters.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

AGGFUNCS = ("count", "mean", "sum", "min", "max")


def generate_pivot(
    parquet_path: Path,
    rows: str,
    columns: str,
    values: str | None = None,
    aggfunc: str = "count",
) -> dict:
    """Pivot `rows` x `columns` on the respondent data, aggregating `values`.

    aggfunc="count" ignores `values` (counts respondents per cell); all other
    aggfuncs require `values` to be set.

    Returns {"success": True, "columns": [...], "rows": [...]} where "rows" is
    a list of dicts (one per distinct row value, one key per distinct column
    value) — the shape the frontend's table renderer expects.
    """
    if aggfunc not in AGGFUNCS:
        return {
            "success": False,
            "error": f"Unknown aggfunc '{aggfunc}'; must be one of {AGGFUNCS}",
        }
    if aggfunc != "count" and not values:
        return {"success": False, "error": f"aggfunc '{aggfunc}' requires a 'values' column"}
    if not parquet_path.exists():
        return {"success": False, "error": "Respondent data not available for this study"}

    agg_expr = "COUNT(*)" if aggfunc == "count" else f"{aggfunc.upper()}({values})"

    con = duckdb.connect()
    try:
        con.execute("SET memory_limit = '256MB'")
        con.execute("SET threads = 2")
        con.execute(f"CREATE VIEW data AS SELECT * FROM read_parquet('{parquet_path}')")

        # Validate requested columns exist before building SQL with them
        available = {row[0] for row in con.execute("DESCRIBE data").fetchall()}
        for col in (rows, columns, values):
            if col and col not in available:
                return {"success": False, "error": f"Unknown column: {col}"}

        query = f"""
            SELECT {rows} AS row_key, {columns} AS col_key, {agg_expr} AS agg_value
            FROM data
            WHERE {rows} IS NOT NULL AND {columns} IS NOT NULL
            GROUP BY {rows}, {columns}
            ORDER BY {rows}, {columns}
        """
        frame = con.execute(query).fetchdf()
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    finally:
        con.close()

    col_values = sorted(frame["col_key"].unique().tolist(), key=str)
    row_values = sorted(frame["row_key"].unique().tolist(), key=str)

    lookup = {(r.row_key, r.col_key): r.agg_value for r in frame.itertuples()}
    out_rows = []
    for rv in row_values:
        entry = {"row": rv}
        for cv in col_values:
            entry[str(cv)] = lookup.get((rv, cv))
        out_rows.append(entry)

    return {
        "success": True,
        "rows_variable": rows,
        "columns_variable": columns,
        "values_variable": values,
        "aggfunc": aggfunc,
        "columns": [str(c) for c in col_values],
        "rows": out_rows,
    }

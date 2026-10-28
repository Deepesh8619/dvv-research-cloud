"""Weighted crosstabs with significance letters, computed on DuckDB.

DuckDB reads only the columns a table needs off the Parquet file (column
pruning) — this is the structural speed advantage over row-oriented tools
(architecture §7.2).

Beyond category rows, a table can carry net rows (summed categories, e.g.
Top-2-Box) and a weighted mean row, both sig-tested with the same
effective-base machinery.
"""

from __future__ import annotations

import string
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import numpy as np

from src.core.models import DataMap
from src.engines.tabs.stats import col_prop_ztest, z_critical

TOTAL = "__total__"


@dataclass
class Net:
    """A summed group of category codes, e.g. Top 2 Box = [1, 2]."""

    label: str
    codes: list[int]


@dataclass
class BannerColumn:
    variable: str  # banner variable name, or TOTAL
    code: int  # category code (0 for TOTAL)
    label: str
    letter: str | None  # sig-test letter; Total is untested
    base: float = 0.0  # weighted base
    unweighted_base: int = 0
    effective_base: float = 0.0


@dataclass
class CrosstabResult:
    stub: str
    stub_label: str
    columns: list[BannerColumn]
    row_codes: list[int]
    row_labels: list[str]
    counts: np.ndarray  # weighted counts, rows x columns
    col_pct: np.ndarray  # column percentages (0-100), rows x columns
    sig: list[list[str]] = field(default_factory=list)  # letters beaten, rows x columns
    net_labels: list[str] = field(default_factory=list)
    net_pct: np.ndarray | None = None  # nets x columns
    net_sig: list[list[str]] = field(default_factory=list)
    means: np.ndarray | None = None  # per column
    mean_sig: list[str] = field(default_factory=list)
    filter_desc: str = ""


def _filter_clause(filter_: dict[str, list[int]] | None, datamap: DataMap) -> tuple[str, str]:
    """Validated SQL fragment + human-readable description for a categorical filter."""
    if not filter_:
        return "", ""
    fragments, descriptions = [], []
    for var, codes in filter_.items():
        if var not in datamap:
            raise ValueError(f"filter variable not in datamap: {var}")
        if not codes or not all(isinstance(c, int) for c in codes):
            raise ValueError(f"filter codes for {var} must be a non-empty list of ints")
        fragments.append(f"AND {var} IN ({', '.join(str(c) for c in codes)})")
        labels = [datamap[var].values.get(c, str(c)) for c in codes]
        descriptions.append(f"{datamap[var].label or var}: {' / '.join(labels)}")
    return " ".join(fragments), "; ".join(descriptions)


def _aggregate(
    con: duckdb.DuckDBPyConnection,
    source: str,
    stub: str,
    banner_var: str | None,
    weight: str | None,
    where_extra: str = "",
) -> dict:
    """One DuckDB pass: weighted count, n, and sum w^2 per (stub, banner) cell."""
    w = weight if weight else "1.0"
    group = f"{stub}, {banner_var}" if banner_var else stub
    select_banner = f"{banner_var} AS banner," if banner_var else "0 AS banner,"
    query = f"""
        SELECT {stub} AS stub, {select_banner}
               SUM({w}) AS wsum, COUNT(*) AS n, SUM(({w})*({w})) AS wsq
        FROM {source}
        WHERE {stub} IS NOT NULL
              {f"AND {banner_var} IS NOT NULL" if banner_var else ""}
              {where_extra}
        GROUP BY {group}
    """
    cells = {}
    for stub_code, banner_code, wsum, n, wsq in con.sql(query).fetchall():
        cells[(int(stub_code), int(banner_code))] = (float(wsum), int(n), float(wsq))
    return cells


def _letters_for_row(
    proportions: np.ndarray,
    columns: list[BannerColumn],
    crit: float,
    min_base: int,
) -> list[str]:
    """Sig letters for one row of proportions across the banner."""
    out = []
    for j, col_j in enumerate(columns):
        if col_j.letter is None or col_j.effective_base < min_base:
            out.append("")
            continue
        beaten = []
        for k, col_k in enumerate(columns):
            if (
                k == j
                or col_k.letter is None
                or col_k.variable != col_j.variable
                or col_k.effective_base < min_base
            ):
                continue
            z = col_prop_ztest(
                proportions[j], proportions[k], col_j.effective_base, col_k.effective_base
            )
            if z > crit:
                beaten.append(col_k.letter)
        out.append("".join(beaten))
    return out


def crosstab(
    parquet_path: str | Path,
    stub: str,
    banner: list[str],
    datamap: DataMap,
    weight: str | None = None,
    confidence: float = 0.95,
    min_base_for_test: int = 30,
    nets: list[Net] | None = None,
    show_mean: bool = False,
    filter_: dict[str, list[int]] | None = None,
) -> CrosstabResult:
    """Compute one stub-by-banner table with column %, nets, mean, and sig letters.

    Letters follow tab-shop convention: a column shows the letter of every column
    (within the same banner variable) it is significantly higher than. The mean
    row uses a large-sample z on within-column variance over effective bases.
    """
    # De-dup, preserving order: callers (chiefly the navigator LLM's show_table
    # tool call) aren't guaranteed to pass distinct names. datamap.add() blocks
    # a variable being registered twice, but nothing stops a `banner` list from
    # naming the same variable twice — without this, the column-building loop
    # below would emit that variable's categories twice, verbatim (same
    # aggregation looked up twice from per_banner_cells), producing a table
    # with repeated columns showing identical numbers under each repeat.
    banner = list(dict.fromkeys(banner))
    con = duckdb.connect()
    source = f"read_parquet('{Path(parquet_path)}')"
    stub_var = datamap[stub]
    row_codes = stub_var.valid_codes()
    where_extra, filter_desc = _filter_clause(filter_, datamap)

    columns: list[BannerColumn] = [
        BannerColumn(variable=TOTAL, code=0, label="Total", letter=None)
    ]
    # A-Z then a-z (tab-shop convention past 26). Columns past 52 get no letter,
    # so no sig test, rather than a crash: Country on one live file had 47 codes
    # and next() raised a StopIteration whose message is empty.
    letters = iter(string.ascii_uppercase + string.ascii_lowercase)
    for banner_name in banner:
        for code in datamap[banner_name].valid_codes():
            columns.append(
                BannerColumn(
                    variable=banner_name,
                    code=code,
                    label=datamap[banner_name].values.get(code, str(code)),
                    letter=next(letters, None),
                )
            )

    n_rows, n_cols = len(row_codes), len(columns)
    counts = np.zeros((n_rows, n_cols))
    col_pct = np.zeros((n_rows, n_cols))

    total_cells = _aggregate(con, source, stub, None, weight, where_extra)
    per_banner_cells = {
        banner_name: _aggregate(con, source, stub, banner_name, weight, where_extra)
        for banner_name in banner
    }

    for j, col in enumerate(columns):
        if col.variable == TOTAL:
            relevant = {code: v for (code, _), v in total_cells.items()}
        else:
            relevant = {
                stub_code: v
                for (stub_code, banner_code), v in per_banner_cells[col.variable].items()
                if banner_code == col.code
            }
        wsum_all = sum(v[0] for v in relevant.values())
        n_all = sum(v[1] for v in relevant.values())
        wsq_all = sum(v[2] for v in relevant.values())
        col.base = wsum_all
        col.unweighted_base = n_all
        col.effective_base = wsum_all**2 / wsq_all if wsq_all > 0 else 0.0
        for i, code in enumerate(row_codes):
            wsum = relevant.get(code, (0.0, 0, 0.0))[0]
            counts[i, j] = wsum
            col_pct[i, j] = 100.0 * wsum / wsum_all if wsum_all > 0 else 0.0

    con.close()
    crit = z_critical(confidence)
    sig = [
        _letters_for_row(col_pct[i] / 100.0, columns, crit, min_base_for_test)
        for i in range(n_rows)
    ]

    net_labels: list[str] = []
    net_pct = None
    net_sig: list[list[str]] = []
    if nets:
        net_pct = np.zeros((len(nets), n_cols))
        for gi, net_def in enumerate(nets):
            unknown = [c for c in net_def.codes if c not in row_codes]
            if unknown:
                raise ValueError(f"net '{net_def.label}' uses codes not in {stub}: {unknown}")
            member_idx = [row_codes.index(c) for c in net_def.codes]
            net_pct[gi] = col_pct[member_idx].sum(axis=0)
            net_labels.append(net_def.label)
        net_sig = [
            _letters_for_row(net_pct[gi] / 100.0, columns, crit, min_base_for_test)
            for gi in range(len(nets))
        ]

    means = None
    mean_sig: list[str] = []
    if show_mean:
        codes_arr = np.array(row_codes, dtype=np.float64)
        means = np.zeros(n_cols)
        variances = np.zeros(n_cols)
        for j in range(n_cols):
            base = counts[:, j].sum()
            if base <= 0:
                continue
            means[j] = float((codes_arr * counts[:, j]).sum() / base)
            ex2 = float((codes_arr**2 * counts[:, j]).sum() / base)
            variances[j] = max(ex2 - means[j] ** 2, 0.0)
        mean_sig = []
        for j, col_j in enumerate(columns):
            if col_j.letter is None or col_j.effective_base < min_base_for_test:
                mean_sig.append("")
                continue
            beaten = []
            for k, col_k in enumerate(columns):
                if (
                    k == j
                    or col_k.letter is None
                    or col_k.variable != col_j.variable
                    or col_k.effective_base < min_base_for_test
                ):
                    continue
                se = np.sqrt(
                    variances[j] / col_j.effective_base + variances[k] / col_k.effective_base
                )
                if se > 0 and (means[j] - means[k]) / se > crit:
                    beaten.append(col_k.letter)
            mean_sig.append("".join(beaten))

    return CrosstabResult(
        stub=stub,
        stub_label=stub_var.label or stub,
        columns=columns,
        row_codes=row_codes,
        row_labels=[stub_var.values.get(c, str(c)) for c in row_codes],
        counts=counts,
        col_pct=col_pct,
        sig=sig,
        net_labels=net_labels,
        net_pct=net_pct,
        net_sig=net_sig,
        means=means,
        mean_sig=mean_sig,
        filter_desc=filter_desc,
    )

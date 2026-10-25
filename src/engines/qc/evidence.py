"""What a flagged respondent actually answered, beside each reason they were flagged.

A reviewer shown "gibberish: Q5" cannot judge it; shown Q5's question and the
answer "asdkjh qwe", they can in a second. For each flag this finds the
columns it is about — the column named in the detail, or for a straight-liner
the grid answered with one value throughout — and returns their question text
and the respondent's answer in words.
"""

from __future__ import annotations

import re
from typing import Any

import polars as pl

from src.core.models import DataMap
from src.engines.qc.paradata import row_paradata
from src.engines.qc.respondents import MIN_GRID_ITEMS, _find_duration_column, _grid_groups

# Decipher writes a piped label as script: ${"Not at all Compelling1" if (...) else ...}
_SCRIPTED = re.compile(r'^\$\s*\{\s*"([^"]+?)\d*"')


def _shown(datamap: DataMap, column: str, value: Any) -> str:
    if value is None:
        return ""
    v = datamap[column] if column in datamap else None
    if v and v.values:
        try:
            label = v.values.get(int(value), str(value))
        except (TypeError, ValueError):
            return str(value)
        m = _SCRIPTED.match(label)
        return m.group(1) if m else label
    return str(value)


def _question(datamap: DataMap, column: str) -> str:
    v = datamap[column] if column in datamap else None
    return ((v.question_text or v.label) if v else "") or column


def _columns_in(detail: str, columns: set[str]) -> list[str]:
    """Columns a flag's detail names: "Q5", "Q15OE: pasted …", '"hQ7: …"'."""
    head = detail.split(":", 1)[0].strip().strip('"')
    if head in columns:
        return [head]
    return [t for t in re.findall(r"[A-Za-z_][\w]*", detail) if t in columns][:3]


def evidence(df: pl.DataFrame, datamap: DataMap, row: int, flags: list[dict]) -> dict:
    cols = set(df.columns)
    record = df.row(row, named=True)
    items = []
    for f in flags:
        answers = []
        if f["check"] == "straightliner":
            for grid in _grid_groups(datamap):
                g = [c for c in grid if c in cols]
                vals = [record.get(c) for c in g]
                if len(g) >= MIN_GRID_ITEMS and None not in vals and len(set(vals)) == 1:
                    answers.append({"column": g[0], "question": _question(datamap, g[0]),
                                    "answer": f"all {len(g)} rows answered "
                                              f"“{_shown(datamap, g[0], vals[0])}”"})
        elif f["check"] != "speeder":  # the time is in the header, against the median
            for c in _columns_in(f["detail"], cols):
                answers.append({"column": c, "question": _question(datamap, c)[:200],
                                "answer": _shown(datamap, c, record.get(c))[:1000]})
        items.append({**f, "answers": answers})
    duration = _find_duration_column(df, datamap)
    seconds = record.get(duration) if duration else None
    median = df[duration].median() if duration else None
    out = {"items": items,
           "seconds": float(seconds) if isinstance(seconds, int | float) else None,
           "median_seconds": float(median) if isinstance(median, int | float) else None}
    if (para := row_paradata(df, row)) is not None:
        out["paradata"] = para
    return out

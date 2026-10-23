"""Advanced methods on the delivered data: MaxDiff, TURF, Van Westendorp, key drivers.

The analyses an agency otherwise buys a second tool for, computed from the
same data file as the tables, deterministically (numpy, no model):

- **MaxDiff scores** — counting analysis: per item, how often it was picked
  best and worst, and (best - worst) / times shown. Reads Decipher's layout
  (`<q>_<n>best`, `<q>_<n>worst`, the items shown in `MD_Items_<n>`) and DVV's
  own (`<q>_s<n>_best`). Without the items shown, the score is per respondent
  and says so. Hierarchical-Bayes utilities are not attempted.
- **TURF** — which set of k options reaches the most respondents (anyone
  choosing at least one), exhaustively while that is cheap, greedily beyond.
- **Van Westendorp** — the four price questions' curves and their crossings:
  the acceptable range (PMC to PME), optimal and indifference price points.
  Respondents whose four prices are out of order are left out, and counted.
- **Key drivers** — Johnson's relative weights: each attribute's share of the
  outcome's explained variance, which (unlike regression coefficients) stays
  honest when attributes correlate with each other, as ratings always do.
"""

from __future__ import annotations

import itertools
import math
import re
from typing import Any

import numpy as np
import polars as pl

from src.core.models import DataMap

_MD = re.compile(r"^(?P<q>.+?)_s?(?P<n>\d+)_?(?P<k>best|worst)$", re.I)
_SHOWN = re.compile(r"Item:\s*(\d+)", re.I)


def _codes(series: pl.Series) -> list[int | None]:
    return [None if v is None or (isinstance(v, float) and math.isnan(v)) else int(v)
            for v in series.to_list()]


def maxdiff_exercises(df: pl.DataFrame) -> dict[str, int]:
    """Each MaxDiff in the file and its number of screens."""
    screens: dict[str, set[int]] = {}
    for col in df.columns:
        if m := _MD.match(col):
            screens.setdefault(m["q"], set()).add(int(m["n"]))
    return {q: len(n) for q, n in screens.items() if len(n) >= 2}


def maxdiff_scores(df: pl.DataFrame, datamap: DataMap, exercise: str) -> dict[str, Any]:
    cols: dict[int, dict[str, str]] = {}
    for col in df.columns:
        if (m := _MD.match(col)) and m["q"] == exercise:
            cols.setdefault(int(m["n"]), {})[m["k"].lower()] = col
    if len(cols) < 2:
        raise ValueError(f"no MaxDiff screens named {exercise}")
    labels: dict[int, str] = {}
    for c in cols.values():
        for name in c.values():
            if name in datamap:
                labels.update(datamap[name].values)
    best: dict[int, int] = {}
    worst: dict[int, int] = {}
    shown: dict[int, int] = {}
    shown_known = all(f"MD_Items_{n}" in df.columns for n in cols)
    answered = set()
    for n, c in cols.items():
        b = _codes(df[c["best"]]) if "best" in c else [None] * df.height
        w = _codes(df[c["worst"]]) if "worst" in c else [None] * df.height
        lists = df[f"MD_Items_{n}"].to_list() if shown_known else [None] * df.height
        for i in range(df.height):
            if b[i] is None and w[i] is None:
                continue
            answered.add(i)
            if b[i] is not None:
                best[b[i]] = best.get(b[i], 0) + 1
            if w[i] is not None:
                worst[w[i]] = worst.get(w[i], 0) + 1
            for item in _SHOWN.findall(lists[i] or ""):
                shown[int(item)] = shown.get(int(item), 0) + 1
    n_resp = len(answered)
    items = sorted(set(labels) | set(best) | set(worst) | set(shown))
    rows = []
    for item in items:
        b, w = best.get(item, 0), worst.get(item, 0)
        base = shown.get(item, 0) if shown_known else n_resp
        rows.append({"item": item, "label": labels.get(item, str(item)), "best": b, "worst": w,
                     "shown": shown.get(item) if shown_known else None,
                     "score": round((b - w) / base, 3) if base else None})
    rows.sort(key=lambda r: -(r["score"] if r["score"] is not None else -9))
    return {"exercise": exercise, "n_respondents": n_resp, "n_screens": len(cols),
            "basis": ("per time shown" if shown_known
                      else "per respondent (items shown not recorded)"),
            "items": rows}


def option_columns(df: pl.DataFrame, question: str) -> list[str]:
    """A multi-select's 0/1 option columns, `Q5r1`, `Q5r2`, ..."""
    pat = re.compile(rf"^{re.escape(question)}r\d+$")
    return [c for c in df.columns if pat.match(c)]


def _chosen(df: pl.DataFrame, columns: list[str]) -> tuple[np.ndarray, np.ndarray]:
    block = df.select(columns).to_numpy().astype(float)
    asked = ~np.all(np.isnan(block), axis=1)
    return (np.nan_to_num(block[asked]) > 0), asked


def turf(df: pl.DataFrame, datamap: DataMap, columns: list[str], max_size: int = 5,
         exhaustive_limit: int = 50_000) -> dict[str, Any]:
    """The reach of the best set of each size from 1 to `max_size`."""
    if len(columns) < 2:
        raise ValueError("TURF needs at least two options")
    picks, _asked = _chosen(df, columns)
    n = picks.shape[0]
    if n == 0:
        raise ValueError("nobody answered these options")

    def label(c: str) -> str:
        v = datamap[c] if c in datamap else None
        own = (v.label or c).replace(v.question_text or "", "").strip(" -—:") if v else c
        return own or c

    def reach(idx: tuple[int, ...]) -> tuple[float, float]:
        hits = picks[:, idx].sum(axis=1)
        reached = hits > 0
        return float(reached.mean() * 100), float(hits[reached].mean()) if reached.any() else 0.0

    out, chosen = [], ()
    for k in range(1, min(max_size, len(columns)) + 1):
        if math.comb(len(columns), k) <= exhaustive_limit:
            combos = itertools.combinations(range(len(columns)), k)
            best = max(combos, key=lambda s: reach(s)[0])
            how = "every combination"
        else:  # ponytail: greedy beyond the limit, may miss the true best set by a point or two
            rest = [i for i in range(len(columns)) if i not in chosen]
            best = max((tuple(sorted((*chosen, i))) for i in rest), key=lambda s: reach(s)[0])
            how = "greedy"
        chosen = best
        r, freq = reach(best)
        out.append({"size": k, "options": [label(columns[i]) for i in best],
                    "columns": [columns[i] for i in best], "reach_pct": round(r, 1),
                    "frequency": round(freq, 2), "searched": how})
    singles = sorted(({"option": label(c), "reach_pct": round(float(picks[:, i].mean()) * 100, 1)}
                      for i, c in enumerate(columns)), key=lambda x: -x["reach_pct"])
    return {"n_respondents": n, "sets": out, "single_reach": singles}


def _crossing(prices: np.ndarray, a: np.ndarray, b: np.ndarray) -> float | None:
    d = a - b
    for i in range(1, len(prices)):
        if d[i - 1] == 0:
            return float(prices[i - 1])
        if d[i - 1] * d[i] < 0:
            t = d[i - 1] / (d[i - 1] - d[i])
            return round(float(prices[i - 1] + t * (prices[i] - prices[i - 1])), 2)
    return None


def van_westendorp(df: pl.DataFrame, too_cheap: str, cheap: str, expensive: str,
                   too_expensive: str) -> dict[str, Any]:
    """The price sensitivity meter: four curves and where they cross."""
    block = df.select([too_cheap, cheap, expensive, too_expensive]).to_numpy().astype(float)
    complete = block[~np.isnan(block).any(axis=1)]
    ordered = complete[np.all(np.diff(complete, axis=1) >= 0, axis=1)]
    if len(ordered) < 30:
        raise ValueError(f"only {len(ordered)} respondents gave four prices in order; 30 needed")
    tc, ch, ex, te = ordered.T
    prices = np.unique(ordered)
    pct = lambda cond: np.array([cond(p).mean() * 100 for p in prices])  # noqa: E731
    curves = {"too_cheap": pct(lambda p: tc >= p), "cheap": pct(lambda p: ch >= p),
              "expensive": pct(lambda p: ex <= p), "too_expensive": pct(lambda p: te <= p)}
    points = {
        # The acceptable range: below PMC too many find it suspiciously cheap,
        # above PME too many find it too expensive.
        "point_of_marginal_cheapness": _crossing(prices, curves["too_cheap"],
                                                 100 - curves["cheap"]),
        "point_of_marginal_expensiveness": _crossing(prices, curves["too_expensive"],
                                                     100 - curves["expensive"]),
        "optimal_price_point": _crossing(prices, curves["too_cheap"], curves["too_expensive"]),
        "indifference_price_point": _crossing(prices, curves["cheap"], curves["expensive"]),
    }
    step = max(1, len(prices) // 40)
    return {"n_respondents": len(ordered), "n_out_of_order": len(complete) - len(ordered),
            "points": points,
            "curve": [{"price": float(p), **{k: round(float(v[i]), 1) for k, v in curves.items()}}
                      for i, p in enumerate(prices) if i % step == 0]}


def drivers(df: pl.DataFrame, datamap: DataMap, outcome: str, predictors: list[str]) -> dict:
    """Johnson's relative weights of each predictor for the outcome."""
    cols = [outcome, *predictors]
    block = df.select(cols).to_numpy().astype(float)
    block = block[~np.isnan(block).any(axis=1)]
    n, k = block.shape[0], len(predictors)
    if n < max(30, 5 * k):
        raise ValueError(f"{n} complete answers for {k} attributes; {max(30, 5 * k)} needed")
    corr = np.corrcoef(block, rowvar=False)
    if np.isnan(corr).any():
        raise ValueError("an attribute has the same answer from everyone")
    rxx, rxy = corr[1:, 1:], corr[1:, 0]
    vals, vecs = np.linalg.eigh(rxx)
    lam = vecs @ np.diag(np.sqrt(np.clip(vals, 0, None))) @ vecs.T
    beta = np.linalg.pinv(lam) @ rxy
    raw = (lam ** 2) @ (beta ** 2)
    r2 = float(raw.sum())

    def label(c: str) -> str:
        v = datamap[c] if c in datamap else None
        return (v.label or c) if v else c

    rows = sorted(({"attribute": label(c), "variable": c, "correlation": round(float(rxy[i]), 3),
                    "weight": round(float(raw[i]), 4),
                    "share_pct": round(float(raw[i]) / r2 * 100, 1) if r2 else None}
                   for i, c in enumerate(predictors)), key=lambda r: -r["weight"])
    return {"outcome": label(outcome), "n_respondents": n, "r_squared": round(r2, 3),
            "attributes": rows}

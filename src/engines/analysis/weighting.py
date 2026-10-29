"""Rim weighting (raking): weights that make the sample match known targets.

The sample came back 60% female; the population is 51%. Raking adjusts each
respondent's weight, one target variable at a time, until every target's
weighted shares match — gender, then age, then region, then round again —
stopping when nothing moves more than `tolerance`. Weights are trimmed to
[cap_low, cap_high] (x the mean) between rounds so a handful of respondents
cannot carry the result.

What a researcher must see before trusting it, and what `RakeResult` reports:
the weighting efficiency (n_eff / n, Kish), the effective sample size, the
smallest and largest weight, whether it converged, and each target's achieved
share next to the one asked for. A respondent missing a target variable keeps
their weight on that dimension rather than being guessed into a category.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class RakeResult:
    weights: np.ndarray
    converged: bool
    iterations: int
    efficiency: float  # n_eff / n, 0-1
    effective_n: float
    min_weight: float
    max_weight: float
    trimmed: int  # respondents at a cap in the final weights
    achieved: dict[str, dict[int, dict]] = field(default_factory=dict)

    def report(self) -> dict:
        return {"converged": self.converged, "iterations": self.iterations,
                "efficiency_pct": round(100 * self.efficiency, 1),
                "effective_n": round(self.effective_n, 1), "n": int(len(self.weights)),
                "min_weight": round(self.min_weight, 3), "max_weight": round(self.max_weight, 3),
                "trimmed": self.trimmed, "targets": self.achieved}


def normalise(targets: dict[str, dict[int, float]]) -> dict[str, dict[int, float]]:
    """Shares or percentages, each variable scaled to sum to 1."""
    out = {}
    for var, cells in targets.items():
        total = sum(float(v) for v in cells.values())
        if total <= 0:
            raise ValueError(f"targets for {var} add up to nothing")
        out[var] = {int(k): float(v) / total for k, v in cells.items()}
    return out


def rake(columns: dict[str, np.ndarray], targets: dict[str, dict[int, float]],
         cap_low: float = 0.3, cap_high: float = 3.0, tolerance: float = 1e-5,
         max_iterations: int = 100, base: np.ndarray | None = None) -> RakeResult:
    """`columns[var]` holds each respondent's code (NaN when missing).
    `targets[var][code]` is the share wanted (any scale; normalised here).
    Every code present in the data must have a target: a category left out
    would silently be weighted to zero."""
    targets = normalise(targets)
    n = len(next(iter(columns.values())))
    w = np.ones(n) if base is None else base.astype(float).copy()
    for var, cells in targets.items():
        col = columns[var]
        present = {int(c) for c in np.unique(col[~np.isnan(col)])}
        if missing := sorted(present - set(cells)):
            raise ValueError(f"{var}: codes {missing} are in the data but have no target")
        if empty := sorted(c for c, share in cells.items() if share > 0 and c not in present):
            raise ValueError(f"{var}: no respondent has code(s) {empty}, so no weight can "
                             "reach that target; merge it with another category")
    converged, it = False, 0
    for it in range(1, max_iterations + 1):  # noqa: B007 - `it` is reported after the loop
        biggest = 0.0
        for var, cells in targets.items():
            col = columns[var]
            answered = ~np.isnan(col)
            total = w[answered].sum()
            for code, share in cells.items():
                mask = col == code
                current = w[mask].sum()
                if current > 0:
                    factor = share * total / current
                    biggest = max(biggest, abs(factor - 1))
                    w[mask] *= factor
        mean = w.mean()
        w = np.clip(w, cap_low * mean, cap_high * mean)
        if biggest < tolerance:
            converged = True
            break
    w *= n / w.sum()  # mean weight 1: weighted bases read as respondents
    eff_n = w.sum() ** 2 / (w ** 2).sum()
    mean = w.mean()
    trimmed = int(np.sum(np.isclose(w, cap_low * mean) | np.isclose(w, cap_high * mean)))
    achieved: dict[str, dict[int, dict]] = {}
    for var, cells in targets.items():
        col = columns[var]
        answered = ~np.isnan(col)
        tot_w, tot_n = w[answered].sum(), answered.sum()
        achieved[var] = {code: {"target_pct": round(100 * share, 1),
                                "weighted_pct": round(100 * w[col == code].sum() / tot_w, 1),
                                "unweighted_pct": round(100 * (col == code).sum() / tot_n, 1)}
                         for code, share in cells.items()}
    return RakeResult(w, converged, it, eff_n / n, eff_n, float(w.min()), float(w.max()),
                      trimmed, achieved)


if __name__ == "__main__":
    rng = np.random.default_rng(1)
    g = rng.choice([1, 2], 1000, p=[0.4, 0.6]).astype(float)
    a = rng.choice([1, 2, 3], 1000, p=[0.5, 0.3, 0.2]).astype(float)
    r = rake({"g": g, "a": a}, {"g": {1: 49, 2: 51}, "a": {1: 30, 2: 40, 3: 30}})
    assert r.converged and abs(r.achieved["g"][1]["weighted_pct"] - 49) < 0.1
    assert abs(r.achieved["a"][3]["weighted_pct"] - 30) < 0.1 and 0.5 < r.efficiency < 1
    assert abs(r.weights.mean() - 1) < 1e-9
    try:
        rake({"g": g}, {"g": {1: 50}})
        raise AssertionError("a code with no target must be refused")
    except ValueError as e:
        assert "no target" in str(e)
    print("ok", r.report()["efficiency_pct"], r.iterations)

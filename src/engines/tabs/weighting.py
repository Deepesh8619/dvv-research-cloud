"""RIM weighting (iterative proportional fitting / raking).

Math follows the standard MR approach (as in Quantipy's RIM implementation and
SPSS raking): scale weights per target category, cycle over target variables
until marginals converge. Weights are normalised so they sum to the sample size.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl


@dataclass
class WeightResult:
    weights: np.ndarray
    iterations: int
    converged: bool
    efficiency: float  # (sum w)^2 / (n * sum w^2), 1.0 = unweighted

    @property
    def effective_base(self) -> float:
        return float(self.weights.sum() ** 2 / (self.weights**2).sum())


def rim_weight(
    df: pl.DataFrame,
    targets: dict[str, dict[int, float]],
    max_iterations: int = 200,
    tolerance: float = 1e-7,
    weight_cap: float | None = None,
) -> WeightResult:
    """Compute RIM weights so each target variable's weighted marginals match targets.

    targets: {variable_name: {code: target_proportion}}; proportions per variable
    must sum to 1 over the codes present in the data.
    """
    n = df.height
    w = np.ones(n, dtype=np.float64)
    masks: dict[str, list[tuple[np.ndarray, float]]] = {}
    for var, dist in targets.items():
        col = df[var].to_numpy()
        pairs = []
        for code, proportion in dist.items():
            mask = col == code
            if not mask.any():
                raise ValueError(f"target code {code} not present in {var}")
            pairs.append((mask, proportion))
        masks[var] = pairs

    converged = False
    iterations_run = 0
    for iterations_run in range(1, max_iterations + 1):  # noqa: B007
        max_deviation = 0.0
        for pairs in masks.values():
            total = w.sum()
            for mask, target_prop in pairs:
                current = w[mask].sum() / total
                if current <= 0:
                    continue
                factor = target_prop / current
                w[mask] *= factor
                total = w.sum()
                max_deviation = max(max_deviation, abs(factor - 1.0))
        if weight_cap is not None:
            mean = w.mean()
            np.clip(w, mean / weight_cap, mean * weight_cap, out=w)
        if max_deviation < tolerance:
            converged = True
            break

    w *= n / w.sum()
    efficiency = float(w.sum() ** 2 / (n * (w**2).sum()))
    return WeightResult(
        weights=w, iterations=iterations_run, converged=converged, efficiency=efficiency
    )

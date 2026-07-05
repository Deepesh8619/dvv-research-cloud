"""RIM weighting: weighted marginals must hit targets; efficiency must be sane."""

import numpy as np
import polars as pl
import pytest

from src.engines.tabs.weighting import rim_weight

RNG = np.random.default_rng(42)


def _sample(n: int = 10_000) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "gender": RNG.choice([1, 2], size=n, p=[0.42, 0.58]),
            "age": RNG.choice([1, 2, 3], size=n, p=[0.5, 0.3, 0.2]),
        }
    )


TARGETS = {
    "gender": {1: 0.49, 2: 0.51},
    "age": {1: 0.30, 2: 0.40, 3: 0.30},
}


def test_marginals_match_targets():
    df = _sample()
    result = rim_weight(df, TARGETS)
    assert result.converged
    w = result.weights
    for var, dist in TARGETS.items():
        col = df[var].to_numpy()
        for code, target in dist.items():
            achieved = w[col == code].sum() / w.sum()
            assert achieved == pytest.approx(target, abs=1e-4)


def test_weights_sum_to_n_and_efficiency_in_range():
    df = _sample()
    result = rim_weight(df, TARGETS)
    assert result.weights.sum() == pytest.approx(df.height)
    assert 0.0 < result.efficiency <= 1.0
    # this design departs notably from sample -> efficiency clearly below 1
    assert result.efficiency < 0.99


def test_uniform_targets_on_uniform_sample_yield_unit_weights():
    n = 4_000
    df = pl.DataFrame({"gender": np.repeat([1, 2], n // 2)})
    result = rim_weight(df, {"gender": {1: 0.5, 2: 0.5}})
    assert np.allclose(result.weights, 1.0, atol=1e-6)
    assert result.efficiency == pytest.approx(1.0, abs=1e-9)


def test_missing_target_code_raises():
    df = pl.DataFrame({"gender": [1, 1, 1]})
    with pytest.raises(ValueError, match="not present"):
        rim_weight(df, {"gender": {1: 0.5, 2: 0.5}})

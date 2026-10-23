"""Advanced methods against data whose answer is known."""

import numpy as np
import polars as pl
import pytest

from src.core.models import DataMap, Variable, VarType
from src.engines.analysis.methods import (
    drivers,
    maxdiff_exercises,
    maxdiff_scores,
    turf,
    van_westendorp,
)


def test_maxdiff_counts_per_time_shown_and_reads_dvv_names_too():
    df = pl.DataFrame({
        "MX_1best": [1, 1, 2], "MX_1worst": [3, 2, 3],
        "MX_2best": [1, 3, 1], "MX_2worst": [2, 1, 3],
        "MD_Items_1": ["Item: 1 Item: 2 Item: 3"] * 3,
        "MD_Items_2": ["Item: 1 Item: 2 Item: 3"] * 3,
    })
    dm = DataMap([Variable(name="MX_1best", type=VarType.SINGLE,
                           values={1: "Price", 2: "Taste", 3: "Pack"})])
    assert maxdiff_exercises(df) == {"MX": 2}
    r = maxdiff_scores(df, dm, "MX")
    assert r["basis"] == "per time shown" and r["n_respondents"] == 3
    top = r["items"][0]
    assert (top["label"], top["best"], top["worst"], top["shown"], top["score"]) == (
        "Price", 4, 1, 6, 0.5)
    own = pl.DataFrame({"Q9_s1_best": [1], "Q9_s1_worst": [2],
                        "Q9_s2_best": [1], "Q9_s2_worst": [3]})
    assert maxdiff_scores(own, DataMap(), "Q9")["basis"].startswith("per respondent")


def test_turf_finds_the_pair_that_reaches_everyone():
    # A and B overlap; C reaches exactly the people A misses.
    df = pl.DataFrame({"Q5r1": [1, 1, 1, 0, 0], "Q5r2": [1, 1, 0, 0, 0], "Q5r3": [0, 0, 0, 1, 1]})
    r = turf(df, DataMap(), ["Q5r1", "Q5r2", "Q5r3"], max_size=2)
    assert r["sets"][0]["reach_pct"] == 60.0
    assert r["sets"][1]["columns"] == ["Q5r1", "Q5r3"] and r["sets"][1]["reach_pct"] == 100.0


def test_van_westendorp_crossings_and_out_of_order_answers():
    rng = np.random.default_rng(1)
    n = 400
    base = rng.normal(10, 1.5, n)
    df = pl.DataFrame({"tc": base - 4, "ch": base - 2, "ex": base + 2, "te": base + 4})
    df = pl.concat([df, pl.DataFrame({"tc": [9.0], "ch": [5.0], "ex": [6.0], "te": [7.0]})])
    r = van_westendorp(df, "tc", "ch", "ex", "te")
    p = r["points"]
    assert r["n_out_of_order"] == 1
    assert 9 < p["optimal_price_point"] < 11 and 9 < p["indifference_price_point"] < 11
    assert p["point_of_marginal_cheapness"] < p["point_of_marginal_expensiveness"]


def test_relative_weights_split_the_explained_variance():
    rng = np.random.default_rng(7)
    x1, x2 = rng.normal(size=2000), rng.normal(size=2000)
    y = 2 * x1 + x2 + rng.normal(size=2000)
    r = drivers(pl.DataFrame({"y": y, "x1": x1, "x2": x2}), DataMap(), "y", ["x1", "x2"])
    shares = {a["variable"]: a["share_pct"] for a in r["attributes"]}
    assert 0.8 < r["r_squared"] < 0.88
    assert shares["x1"] == pytest.approx(80, abs=3) and shares["x2"] == pytest.approx(20, abs=3)
    with pytest.raises(ValueError, match="needed"):
        drivers(pl.DataFrame({"y": y[:10], "x1": x1[:10], "x2": x2[:10]}), DataMap(), "y",
                ["x1", "x2"])

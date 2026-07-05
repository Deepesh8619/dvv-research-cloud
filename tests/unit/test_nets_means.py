"""Nets and means math on hand-checkable data."""

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.core.models import DataMap, Variable, VarType
from src.engines.tabs.crosstab import Net, crosstab

SCALE = {1: "Strongly agree", 2: "Agree", 3: "Neither", 4: "Disagree", 5: "Strongly disagree"}


@pytest.fixture
def study(tmp_path):
    # 10 respondents, q1 codes chosen for easy arithmetic
    q1 = [1, 1, 2, 2, 3, 3, 4, 4, 5, 5]
    gender = [1, 2] * 5
    path = tmp_path / "scale.parquet"
    pq.write_table(pa.table({"gender": gender, "q1": q1}), str(path))
    datamap = DataMap(
        [
            Variable("gender", "Gender", VarType.SINGLE, {1: "Male", 2: "Female"}),
            Variable("q1", "Agreement", VarType.SINGLE, SCALE),
        ]
    )
    return path, datamap


def test_net_is_sum_of_member_categories(study):
    path, datamap = study
    result = crosstab(
        path, stub="q1", banner=["gender"], datamap=datamap,
        nets=[Net("T2B", [1, 2]), Net("B2B", [4, 5])],
    )
    # Total column: each code is 20%, so T2B = B2B = 40%
    assert result.net_labels == ["T2B", "B2B"]
    assert result.net_pct[0, 0] == pytest.approx(40.0)
    assert result.net_pct[1, 0] == pytest.approx(40.0)


def test_mean_matches_hand_computation(study):
    path, datamap = study
    result = crosstab(path, stub="q1", banner=["gender"], datamap=datamap, show_mean=True)
    # codes 1..5 twice each -> mean 3.0 exactly
    assert result.means[0] == pytest.approx(3.0)


def test_weighted_mean(tmp_path):
    # 4 respondents: codes 1,1,5,5 with weights 3,3,1,1 -> mean = (3+3+5+5)/8 = 2.0
    path = tmp_path / "w.parquet"
    pq.write_table(
        pa.table({"q1": [1, 1, 5, 5], "w": [3.0, 3.0, 1.0, 1.0]}), str(path)
    )
    datamap = DataMap(
        [
            Variable("q1", "Scale", VarType.SINGLE, SCALE),
            Variable("w", "Weight", VarType.NUMERIC),
        ]
    )
    result = crosstab(path, stub="q1", banner=[], datamap=datamap, weight="w", show_mean=True)
    assert result.means[0] == pytest.approx(2.0)


def test_net_with_unknown_code_raises(study):
    path, datamap = study
    with pytest.raises(ValueError, match="codes not in"):
        crosstab(path, stub="q1", banner=["gender"], datamap=datamap, nets=[Net("bad", [9])])


def test_filter_restricts_base(study):
    path, datamap = study
    result = crosstab(path, stub="q1", banner=["gender"], datamap=datamap,
                      filter_={"gender": [1]})
    assert result.columns[0].unweighted_base == 5  # males only
    assert "Male" in result.filter_desc
    # female column exists but is empty under the filter
    assert result.columns[2].unweighted_base == 0


def test_filter_validation(study):
    path, datamap = study
    with pytest.raises(ValueError, match="not in datamap"):
        crosstab(path, stub="q1", banner=["gender"], datamap=datamap,
                 filter_={"nope": [1]})


def test_net_sig_letters(tmp_path):
    # males heavily top-box vs females -> T2B male column must beat B
    rng = np.random.default_rng(5)
    gender = np.repeat([1, 2], 1000)
    q1 = np.where(
        gender == 1,
        rng.choice([1, 2, 3, 4, 5], size=2000, p=[0.4, 0.3, 0.1, 0.1, 0.1]),
        rng.choice([1, 2, 3, 4, 5], size=2000, p=[0.1, 0.1, 0.1, 0.3, 0.4]),
    )
    path = tmp_path / "sig.parquet"
    pq.write_table(pa.table({"gender": gender, "q1": q1}), str(path))
    datamap = DataMap(
        [
            Variable("gender", "Gender", VarType.SINGLE, {1: "Male", 2: "Female"}),
            Variable("q1", "Agreement", VarType.SINGLE, SCALE),
        ]
    )
    result = crosstab(
        path, stub="q1", banner=["gender"], datamap=datamap,
        nets=[Net("T2B", [1, 2])], show_mean=True,
    )
    assert "B" in result.net_sig[0][1]  # male T2B beats female
    assert result.net_sig[0][2] == ""
    # female mean (higher code = more disagreement) significantly above male
    assert "A" in result.mean_sig[2]

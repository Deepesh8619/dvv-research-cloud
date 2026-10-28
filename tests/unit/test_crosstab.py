"""Crosstab engine on a tiny dataset with hand-checkable answers."""

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.core.models import DataMap, Variable, VarType
from src.engines.tabs.crosstab import crosstab


@pytest.fixture
def study(tmp_path):
    # 8 respondents: gender (1=male, 2=female), q1 awareness (1=yes, 2=no)
    gender = [1, 1, 1, 1, 2, 2, 2, 2]
    q1 = [1, 1, 2, 2, 1, 1, 1, 2]
    weight = [1.0, 1.0, 1.0, 1.0, 2.0, 2.0, 2.0, 2.0]
    path = tmp_path / "mini.parquet"
    pq.write_table(
        pa.table({"gender": gender, "q1": q1, "weight": weight}), str(path)
    )
    datamap = DataMap(
        [
            Variable("gender", "Gender", VarType.SINGLE, {1: "Male", 2: "Female"}),
            Variable("q1", "Aware of brand", VarType.SINGLE, {1: "Yes", 2: "No"}),
            Variable("weight", "Weight", VarType.NUMERIC),
        ]
    )
    return path, datamap


def test_unweighted_percentages(study):
    path, datamap = study
    result = crosstab(path, stub="q1", banner=["gender"], datamap=datamap)
    labels = [c.label for c in result.columns]
    assert labels == ["Total", "Male", "Female"]
    # Total: 5/8 yes; Male: 2/4; Female: 3/4
    assert result.col_pct[0].tolist() == pytest.approx([62.5, 50.0, 75.0])
    assert result.col_pct[1].tolist() == pytest.approx([37.5, 50.0, 25.0])
    assert [c.unweighted_base for c in result.columns] == [8, 4, 4]


def test_weighted_percentages(study):
    path, datamap = study
    result = crosstab(path, stub="q1", banner=["gender"], datamap=datamap, weight="weight")
    # weighted: male base 4 (2 yes), female base 8 (6 yes), total 12 (8 yes)
    assert result.columns[0].base == pytest.approx(12.0)
    assert result.col_pct[0].tolist() == pytest.approx([100 * 8 / 12, 50.0, 75.0])
    # effective base for female: (8^2)/(4*4) = 4.0 (Kish correction applied)
    assert result.columns[2].effective_base == pytest.approx(4.0)


def test_columns_sum_to_100(study):
    path, datamap = study
    result = crosstab(path, stub="q1", banner=["gender"], datamap=datamap, weight="weight")
    sums = result.col_pct.sum(axis=0)
    assert np.allclose(sums, 100.0)


def test_duplicate_banner_name_does_not_duplicate_columns(study):
    """Regression: the navigator LLM's show_table tool call isn't guaranteed to
    pass distinct banner names. A caller passing the same variable twice used
    to render it twice (identical numbers repeated verbatim, e.g. "New/Used"
    columns appearing twice with the exact same figures) instead of being
    silently de-duplicated.
    """
    path, datamap = study
    result = crosstab(path, stub="q1", banner=["gender", "gender"], datamap=datamap)
    labels = [c.label for c in result.columns]
    assert labels == ["Total", "Male", "Female"]  # not Male, Female, Male, Female
    assert result.col_pct.shape == (2, 3)


def test_sig_letters_on_large_synthetic(tmp_path):
    # 2000 respondents; males 60% yes, females 40% yes -> male column must beat B
    rng = np.random.default_rng(7)
    gender = np.repeat([1, 2], 1000)
    p = np.where(gender == 1, 0.6, 0.4)
    q1 = np.where(rng.random(2000) < p, 1, 2)
    path = tmp_path / "big.parquet"
    pq.write_table(pa.table({"gender": gender, "q1": q1}), str(path))
    datamap = DataMap(
        [
            Variable("gender", "Gender", VarType.SINGLE, {1: "Male", 2: "Female"}),
            Variable("q1", "Aware", VarType.SINGLE, {1: "Yes", 2: "No"}),
        ]
    )
    result = crosstab(path, stub="q1", banner=["gender"], datamap=datamap)
    male, female = result.columns[1], result.columns[2]
    assert male.letter == "A" and female.letter == "B"
    yes_row = result.row_codes.index(1)
    assert "B" in result.sig[yes_row][1]  # male significantly higher on yes
    assert result.sig[yes_row][2] == ""  # female beats nothing on yes
    no_row = result.row_codes.index(2)
    assert "A" in result.sig[no_row][2]  # female significantly higher on no


def test_small_base_columns_are_not_tested(tmp_path):
    gender = [1] * 100 + [2] * 5  # female base 5 < min_base_for_test
    rng = np.random.default_rng(3)
    q1 = rng.choice([1, 2], size=105).tolist()
    path = tmp_path / "smallbase.parquet"
    pq.write_table(pa.table({"gender": gender, "q1": q1}), str(path))
    datamap = DataMap(
        [
            Variable("gender", "Gender", VarType.SINGLE, {1: "Male", 2: "Female"}),
            Variable("q1", "Aware", VarType.SINGLE, {1: "Yes", 2: "No"}),
        ]
    )
    result = crosstab(path, stub="q1", banner=["gender"], datamap=datamap)
    for row in result.sig:
        assert row[1] == "" and row[2] == ""


def test_a_banner_past_26_columns_does_not_crash(tmp_path):
    """Live: Country with 47 codes ran out of A-Z and crashed with an empty error."""
    path = tmp_path / "wide.parquet"
    pq.write_table(pa.table({"country": list(range(1, 61)), "q1": [1, 2] * 30}), str(path))
    datamap = DataMap([
        Variable("country", "Country", VarType.SINGLE, {c: f"C{c}" for c in range(1, 61)}),
        Variable("q1", "Aware", VarType.SINGLE, {1: "Yes", 2: "No"}),
    ])
    result = crosstab(path, stub="q1", banner=["country"], datamap=datamap)
    letters = [c.letter for c in result.columns[1:]]
    assert letters[25:27] == ["Z", "a"] and letters[51] == "z"
    assert letters[52:] == [None] * 8  # no letter, no test — never a crash

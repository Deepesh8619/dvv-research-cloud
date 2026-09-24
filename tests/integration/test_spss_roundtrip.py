"""SPSS adapter: .sav -> canonical model -> Parquet, and back out to .sav."""

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pyreadstat
import pytest

from src.adapters.spss import read_sav, sav_to_parquet, write_sav
from src.core.models import DataMap, Variable, VarType


@pytest.fixture
def sav_file(tmp_path):
    rng = np.random.default_rng(11)
    df = pd.DataFrame(
        {
            "resp_id": np.arange(1, 201, dtype=float),
            "gender": rng.choice([1.0, 2.0], size=200),
            "q1": rng.choice([1.0, 2.0, 3.0], size=200),
            "comment": ["some text"] * 200,
        }
    )
    datamap = DataMap(
        [
            Variable("resp_id", "Respondent ID", VarType.NUMERIC),
            Variable("gender", "Gender", VarType.SINGLE, {1: "Male", 2: "Female"}),
            Variable(
                "q1", "Purchase intent", VarType.SINGLE,
                {1: "Definitely", 2: "Maybe", 3: "Never"},
            ),
            Variable("comment", "Open end", VarType.TEXT),
        ]
    )
    path = tmp_path / "study.sav"
    write_sav(df, datamap, path)
    return path, df, datamap


def test_datamap_extracted_from_sav(sav_file):
    path, _, original = sav_file
    _, datamap = read_sav(path)
    assert datamap.names() == original.names()
    assert datamap["gender"].type == VarType.SINGLE
    assert datamap["gender"].values == {1: "Male", 2: "Female"}
    assert datamap["gender"].label == "Gender"
    assert datamap["comment"].type == VarType.TEXT
    assert datamap["gender"].provenance == "spss_import"


def test_sav_to_parquet_preserves_data(sav_file, tmp_path):
    path, df, _ = sav_file
    parquet = tmp_path / "study.parquet"
    datamap = sav_to_parquet(path, parquet)
    table = pq.read_table(str(parquet))
    assert table.num_rows == len(df)
    assert table.column_names == list(df.columns)
    np.testing.assert_allclose(
        table.column("gender").to_numpy(), df["gender"].to_numpy()
    )
    assert len(datamap) == 4


def test_datamap_json_roundtrip(sav_file):
    path, _, _ = sav_file
    _, datamap = read_sav(path)
    restored = DataMap.from_json(datamap.to_json())
    assert restored.names() == datamap.names()
    assert restored["q1"].values == datamap["q1"].values
    assert restored["q1"].type == datamap["q1"].type


# --- the codebook has to travel WITH the data ------------------------------


def _satisfaction_map() -> DataMap:
    datamap = DataMap()
    datamap.add(Variable(
        name="Q1", label="How satisfied are you?", type=VarType.SINGLE,
        values={1: "Very", 2: "Somewhat", 99: "Prefer not to answer"},
        missing_values=[99],
    ))
    datamap.add(Variable(name="AGE", label="Age in years", type=VarType.NUMERIC))
    return datamap


def test_a_declared_missing_code_is_not_counted_as_a_value(tmp_path):
    """The defect that changes numbers in a client deliverable.

    "99: Prefer not to answer" is an answer in the file and NOT one in a mean.
    Undeclared, SPSS treats it as an ordinary value — this exact frame averaged
    25.75 on a 1-2 satisfaction scale. The file opened, the labels were right,
    and every mean computed from it was wrong, which is worse than a file that
    fails to open.
    """
    df = pd.DataFrame({"Q1": [1.0, 2.0, 99.0, 1.0], "AGE": [34.0, 52.0, 41.0, 29.0]})
    path = tmp_path / "study.sav"

    write_sav(df, _satisfaction_map(), path)

    # Declared in the file itself, not merely dropped on write.
    _, meta = pyreadstat.read_sav(str(path), user_missing=True)
    assert meta.missing_ranges["Q1"] == [{"lo": 99.0, "hi": 99.0}]

    # The analyst's view: 99 is absent, so the mean is of 1, 2, 1.
    analysis, _ = pyreadstat.read_sav(str(path), user_missing=False)
    assert analysis["Q1"].isna().sum() == 1
    assert analysis["Q1"].mean() == pytest.approx(4 / 3)


def test_the_value_is_still_there_when_asked_for(tmp_path):
    """Declaring it missing must not DELETE it — "refused" is a real finding."""
    df = pd.DataFrame({"Q1": [1.0, 2.0, 99.0, 1.0], "AGE": [34.0, 52.0, 41.0, 29.0]})
    path = tmp_path / "study.sav"
    write_sav(df, _satisfaction_map(), path)

    raw, _ = pyreadstat.read_sav(str(path), user_missing=True)
    assert list(raw["Q1"]) == [1.0, 2.0, 99.0, 1.0]


def test_measure_levels_reach_the_file(tmp_path):
    """Without these every column arrives "unknown" and SPSS guesses."""
    df = pd.DataFrame({"Q1": [1.0, 2.0], "AGE": [34.0, 52.0]})
    path = tmp_path / "study.sav"
    write_sav(df, _satisfaction_map(), path)

    _, meta = pyreadstat.read_sav(str(path))
    assert meta.variable_measure["Q1"] == "nominal"
    assert meta.variable_measure["AGE"] == "scale"


def test_a_study_with_no_missing_codes_still_writes(tmp_path):
    """missing_ranges=None rather than {} — pyreadstat rejects the empty dict."""
    datamap = DataMap()
    datamap.add(Variable(name="AGE", label="Age in years", type=VarType.NUMERIC))
    path = tmp_path / "plain.sav"

    write_sav(pd.DataFrame({"AGE": [34.0, 52.0]}), datamap, path)

    back, meta = pyreadstat.read_sav(str(path))
    assert list(back["AGE"]) == [34.0, 52.0]
    assert meta.column_names_to_labels["AGE"] == "Age in years"

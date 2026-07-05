"""SPSS adapter: .sav -> canonical model -> Parquet, and back out to .sav."""

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
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

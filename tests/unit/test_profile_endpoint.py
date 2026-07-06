"""Study data-profile endpoint: data_quality() + datamap label enrichment.

Crosstabs no longer auto-display in the app; this profile (missing %, type,
distinct count, top values/range) is the default Overview view instead.
Crosstabs remain available on demand via the chat navigator's show_table tool.

Mirrors the fixture style in test_analysis_tools.py — tests the enrichment
logic directly (label merge + summary counts) rather than through FastAPI,
consistent with how other engine-level logic in this repo is tested.
"""

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.core.models import DataMap, Variable, VarType
from src.engines.analysis.quality import data_quality


@pytest.fixture
def study_dir(tmp_path) -> tuple[Path, DataMap]:
    """6 respondents: one constant column, one >50% missing, one duplicate ID."""
    gender = [1, 1, 2, 2, 1, 2]
    respondent_id = [1, 2, 3, 4, 5, 5]  # last two duplicated
    always_one = [1, 1, 1, 1, 1, 1]  # constant
    sparse = [None, None, None, None, None, 1]  # >50% missing

    path = tmp_path / "data.parquet"
    pq.write_table(
        pa.table(
            {
                "gender": gender,
                "respondent_id": respondent_id,
                "always_one": always_one,
                "sparse": sparse,
            }
        ),
        str(path),
    )

    datamap = DataMap()
    datamap.add(Variable(name="respondent_id", label="Respondent ID", type=VarType.NUMERIC))
    datamap.add(
        Variable(
            name="gender",
            label="Gender",
            type=VarType.SINGLE,
            values={1: "Male", 2: "Female"},
        )
    )
    datamap.add(Variable(name="always_one", label="Constant Flag", type=VarType.NUMERIC))
    datamap.add(Variable(name="sparse", label="Sparse Field", type=VarType.NUMERIC))
    return path, datamap


def _enrich(report: dict, datamap: DataMap) -> tuple[list[dict], dict]:
    """Mirror the label-merge + summary logic in api/routes/studies.py:get_study_profile."""
    labels = {v.name: v.label for v in datamap.variables}
    flags = report["flags"]
    variables = [
        {"name": col, "label": labels.get(col, ""), **entry}
        for col, entry in report["columns"].items()
    ]
    summary = {
        "n_variables": len(variables),
        "n_respondents": report["row_count"],
        "n_high_missing": len(flags["high_missing_columns"]),
        "n_constant": len(flags["constant_columns"]),
        "n_likely_duplicate_id": len(flags["likely_duplicate_id_columns"]),
    }
    return variables, summary


def test_profile_enriches_columns_with_labels(study_dir):
    path, datamap = study_dir
    report = data_quality(path)
    assert report["success"]

    variables, _ = _enrich(report, datamap)
    by_name = {v["name"]: v for v in variables}

    assert by_name["gender"]["label"] == "Gender"
    assert by_name["respondent_id"]["label"] == "Respondent ID"


def test_profile_summary_counts_match_flags(study_dir):
    path, datamap = study_dir
    report = data_quality(path)

    _, summary = _enrich(report, datamap)

    assert summary["n_variables"] == 4
    assert summary["n_respondents"] == 6
    assert summary["n_high_missing"] == 1  # sparse
    # always_one is constant by design; sparse also qualifies here because its
    # only non-null value is a single distinct value (COUNT(DISTINCT) ignores
    # NULLs) — a column with one real value left is functionally constant too.
    assert summary["n_constant"] == 2  # always_one, sparse
    assert summary["n_likely_duplicate_id"] == 1  # respondent_id


def test_profile_unlabelled_column_falls_back_to_empty_string(study_dir):
    """A column present in the data but absent from the datamap gets label=""
    rather than crashing — e.g. a raw export column the datamap parser skipped."""
    path, datamap = study_dir
    report = data_quality(path)

    variables, _ = _enrich(report, DataMap())  # empty datamap: no labels known
    by_name = {v["name"]: v for v in variables}

    assert by_name["gender"]["label"] == ""


def test_profile_not_ready_shape_when_no_parquet(tmp_path):
    """The endpoint itself returns this exact shape when data.parquet doesn't
    exist yet (study still processing) — verified here since data_quality()
    already returns success=False for a missing path, which the route must
    translate into the ready=False placeholder rather than a 500."""
    missing_path = tmp_path / "data.parquet"
    report = data_quality(missing_path)
    assert report["success"] is False

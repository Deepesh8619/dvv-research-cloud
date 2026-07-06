"""Navigator capability expansion: charts, pivots, code schema, data quality.

Mirrors the fixture style in test_crosstab.py / test_navigator.py — a tiny
synthetic parquet + datamap, checked with hand-verifiable expectations.
"""

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from api.intent_router import classify_intent
from api.routes.chat import _make_executor
from src.core.models import DataMap, Variable, VarType
from src.engines.analysis.charts import generate_chart
from src.engines.analysis.codegen import generate_code
from src.engines.analysis.pivot import generate_pivot
from src.engines.analysis.quality import data_quality


@pytest.fixture
def study_dir(tmp_path) -> tuple[Path, DataMap]:
    """8 respondents: gender, satisfaction (5-pt), region, plus quality-check smells."""
    gender = [1, 1, 1, 1, 2, 2, 2, 2]
    satisfaction = [1, 2, 3, 4, 5, 1, 2, 3]
    region = [1, 1, 2, 2, 1, 1, 2, 2]
    respondent_id = [1, 2, 3, 4, 5, 6, 7, 7]  # last two duplicated -> quality flag
    always_five = [5, 5, 5, 5, 5, 5, 5, 5]  # constant column
    mostly_missing = [None, None, None, None, None, None, None, 1]  # >50% missing

    path = tmp_path / "data.parquet"
    pq.write_table(
        pa.table(
            {
                "gender": gender,
                "satisfaction": satisfaction,
                "region": region,
                "respondent_id": respondent_id,
                "always_five": always_five,
                "mostly_missing": mostly_missing,
            }
        ),
        str(path),
    )

    datamap = DataMap()
    datamap.add(Variable(name="respondent_id", label="ID", type=VarType.NUMERIC))
    datamap.add(
        Variable(
            name="gender", label="Gender", type=VarType.SINGLE, values={1: "Male", 2: "Female"}
        )
    )
    datamap.add(
        Variable(
            name="satisfaction",
            label="Satisfaction",
            type=VarType.SINGLE,
            values={1: "Very poor", 2: "Poor", 3: "Neutral", 4: "Good", 5: "Very good"},
        )
    )
    datamap.add(
        Variable(
            name="region", label="Region", type=VarType.SINGLE, values={1: "North", 2: "South"}
        )
    )
    datamap.add(Variable(name="always_five", label="Constant", type=VarType.NUMERIC))
    datamap.add(Variable(name="mostly_missing", label="Sparse", type=VarType.NUMERIC))
    return tmp_path, datamap


# --- Charts ---


def test_generate_chart_renders_png(study_dir):
    path, datamap = study_dir
    result = generate_chart(path, datamap, stub="satisfaction", banner=["gender"], chart_type="bar")
    assert result["success"]
    assert Path(result["chart_path"]).exists()
    assert result["chart_type"] == "bar"
    assert result["table"]["title"] == "Satisfaction"


def test_generate_chart_pie_and_heatmap(study_dir):
    path, datamap = study_dir
    for chart_type in ("pie", "heatmap", "line"):
        result = generate_chart(
            path, datamap, stub="satisfaction", banner=["gender"], chart_type=chart_type
        )
        assert result["success"], f"{chart_type} failed: {result.get('error')}"
        assert Path(result["chart_path"]).exists()


def test_generate_chart_rejects_bad_chart_type(study_dir):
    path, datamap = study_dir
    result = generate_chart(path, datamap, stub="satisfaction", chart_type="scatter3d")
    assert not result["success"]
    assert "Unknown chart_type" in result["error"]


def test_generate_chart_rejects_unknown_variable(study_dir):
    path, datamap = study_dir
    result = generate_chart(path, datamap, stub="not_a_var")
    assert not result["success"]
    assert "Unknown variable" in result["error"]


# --- Pivot ---


def test_generate_pivot_count(study_dir):
    path, _ = study_dir
    result = generate_pivot(path / "data.parquet", rows="region", columns="gender", aggfunc="count")
    assert result["success"]
    # region=1: gender1 x2 (rows 0,1), gender2 x2 (rows 4,5) => region1: {1:2, 2:2}
    row1 = next(r for r in result["rows"] if r["row"] == 1)
    assert row1["1"] == 2
    assert row1["2"] == 2


def test_generate_pivot_mean(study_dir):
    path, _ = study_dir
    result = generate_pivot(
        path / "data.parquet",
        rows="region",
        columns="gender",
        values="satisfaction",
        aggfunc="mean",
    )
    assert result["success"]
    # region=1, gender=1: rows 0,1 -> satisfaction 1,2 -> mean 1.5
    row1 = next(r for r in result["rows"] if r["row"] == 1)
    assert row1["1"] == pytest.approx(1.5)


def test_generate_pivot_rejects_bad_aggfunc(study_dir):
    path, _ = study_dir
    result = generate_pivot(
        path / "data.parquet", rows="region", columns="gender", aggfunc="median"
    )
    assert not result["success"]
    assert "Unknown aggfunc" in result["error"]


def test_generate_pivot_requires_values_for_non_count(study_dir):
    path, _ = study_dir
    result = generate_pivot(path / "data.parquet", rows="region", columns="gender", aggfunc="sum")
    assert not result["success"]
    assert "requires a 'values'" in result["error"]


# --- Codegen ---


def test_generate_code_returns_schema(study_dir):
    _, datamap = study_dir
    result = generate_code(datamap, language="python", question="correlate age and satisfaction")
    assert result["success"]
    assert result["language"] == "python"
    names = {c["name"] for c in result["schema"]}
    assert "satisfaction" in names
    assert result["executed_result"] is None


def test_generate_code_sql_executes_via_sandbox(study_dir):
    path, datamap = study_dir

    def run_sql(sql: str) -> dict:
        from api.routes.chat import _run_sql

        return _run_sql(path / "data.parquet", sql)

    result = generate_code(
        datamap,
        language="sql",
        question="count by gender",
        sql="SELECT gender, COUNT(*) AS n FROM data GROUP BY gender ORDER BY gender",
        run_sql=run_sql,
    )
    assert result["success"]
    assert result["executed_result"]["success"]
    assert result["executed_result"]["rows"][0]["n"] == 4


def test_generate_code_rejects_disallowed_sql(study_dir):
    path, datamap = study_dir

    def run_sql(sql: str) -> dict:
        from api.routes.chat import _run_sql

        return _run_sql(path / "data.parquet", sql)

    result = generate_code(
        datamap, language="sql", question="drop it", sql="DROP TABLE data", run_sql=run_sql
    )
    assert result["success"]  # the tool call itself succeeds
    assert not result["executed_result"]["success"]  # but the sandbox rejects the SQL


def test_generate_code_rejects_unknown_language(study_dir):
    _, datamap = study_dir
    result = generate_code(datamap, language="cobol", question="???")
    assert not result["success"]


# --- Data quality ---


def test_data_quality_flags_high_missing_and_constant(study_dir):
    path, _ = study_dir
    result = data_quality(path / "data.parquet")
    assert result["success"]
    assert "mostly_missing" in result["flags"]["high_missing_columns"]
    assert "always_five" in result["flags"]["constant_columns"]


def test_data_quality_flags_duplicate_ids(study_dir):
    path, _ = study_dir
    result = data_quality(path / "data.parquet")
    assert "respondent_id" in result["flags"]["likely_duplicate_id_columns"]


def test_data_quality_rejects_unknown_column(study_dir):
    path, _ = study_dir
    result = data_quality(path / "data.parquet", variables=["not_a_col"])
    assert not result["success"]


# --- Intent router ---


@pytest.mark.parametrize(
    "question,expected_intent",
    [
        ("Make a bar chart of satisfaction by gender", "visualization"),
        ("Show a pie chart of region", "visualization"),
        ("Plot the heatmap of satisfaction", "visualization"),
        ("Pivot region by gender", "pivot"),
        ("Aggregate by average satisfaction per region", "pivot"),
        ("Write python to correlate age and satisfaction", "code_generation"),
        ("Give me the SQL code for this", "code_generation"),
        ("Write a formula for this in excel", "code_generation"),
        ("Check data quality", "data_quality"),
        ("Are there missing values or duplicates?", "data_quality"),
    ],
)
def test_new_intents_classify_correctly(question, expected_intent):
    intent, _ = classify_intent(question)
    assert intent == expected_intent, f"'{question}' routed to {intent}, expected {expected_intent}"


def test_existing_intents_still_work():
    """New intents must not steal keyword matches from the original five."""
    assert classify_intent("Cross satisfaction by gender")[0] == "crosstab_analysis"
    assert classify_intent("Is there a significant difference?")[0] == "significance_testing"
    assert classify_intent("What's the average age?")[0] == "exploratory_query"
    assert classify_intent("Show me Table T1")[0] == "deck_reference"
    assert classify_intent("Where is the activity log?")[0] == "navigation"


# --- Executor wiring ---


def test_executor_routes_generate_chart(study_dir):
    path, datamap = study_dir
    execute = _make_executor(path, datamap, deck={})
    result = execute(
        "generate_chart", {"stub": "satisfaction", "banner": ["gender"], "chart_type": "bar"}
    )
    assert result["success"]
    assert Path(result["chart_path"]).exists()


def test_executor_routes_generate_pivot(study_dir):
    path, datamap = study_dir
    execute = _make_executor(path, datamap, deck={})
    result = execute("generate_pivot", {"rows": "region", "columns": "gender", "aggfunc": "count"})
    assert result["success"]


def test_executor_routes_generate_code(study_dir):
    path, datamap = study_dir
    execute = _make_executor(path, datamap, deck={})
    result = execute("generate_code", {"language": "python", "question": "anything"})
    assert result["success"]


def test_executor_routes_generate_code_with_sql(study_dir):
    path, datamap = study_dir
    execute = _make_executor(path, datamap, deck={})
    result = execute(
        "generate_code",
        {"language": "sql", "question": "count", "sql": "SELECT COUNT(*) AS n FROM data"},
    )
    assert result["success"]
    assert result["executed_result"]["success"]


def test_executor_routes_data_quality(study_dir):
    path, datamap = study_dir
    execute = _make_executor(path, datamap, deck={})
    result = execute("data_quality", {})
    assert result["success"]
    assert "flags" in result

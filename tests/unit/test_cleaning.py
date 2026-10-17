import polars as pl

from src.core.models import DataMap, Variable, VarType
from src.engines.qc.cleaning import (
    annotate_delivery,
    apply_cleaning,
    collect_flags,
    current_decisions,
    respondent_key_column,
    respondent_keys,
)


def test_key_column_skips_non_unique():
    df = pl.DataFrame({"record": [1, 1, 2], "respondent_id": [1, 2, 3]})
    assert respondent_key_column(df) == "respondent_id"
    assert respondent_key_column(pl.DataFrame({"record": [1, 1], "x": [1, 2]})) is None
    assert respondent_keys(pl.DataFrame({"x": [1, 2]})) == ["row:0", "row:1"]
    assert respondent_keys(pl.DataFrame({"vSessionId": ["a", None]})) == ["row:0", "row:1"]


def test_current_decisions_latest_wins():
    rows = [
        {"entity_id": "a", "action": "remove", "after": {"reason_code": "speeder", "note": "x"}},
        {"entity_id": "a", "action": "keep", "after": {"reason_code": "other", "note": "ok"}},
        {"entity_id": "b", "action": "remove", "after": {"reason_code": "duplicate"}},
        {"entity_id": "b", "action": "impute", "after": {}},
    ]
    d = current_decisions(rows)
    assert d["a"] == {"decision": "keep", "reason_code": "other", "note": "ok"}
    assert d["b"]["decision"] == "remove" and d["b"]["note"] is None


def _frame():
    n = 12
    dur = [600] * n
    dur[3] = 30  # speeder
    df = pl.DataFrame({
        "vSessionId": [f"s{i}" for i in range(n)],
        "vDurationSec": [float(d) for d in dur],
        "Q1": [i % 3 + 1 for i in range(n)],
        "Q2": [150 if i == 5 else 30 for i in range(n)],
    })
    dm = DataMap()
    dm.add(Variable(name="vSessionId", type=VarType.TEXT, provenance="fielding_paradata"))
    dm.add(Variable(name="vDurationSec", type=VarType.NUMERIC, provenance="fielding_paradata"))
    dm.add(Variable(name="Q1", type=VarType.SINGLE, values={1: "a", 2: "b", 3: "c"}))
    dm.add(Variable(name="Q2", type=VarType.NUMERIC))
    return df, dm


def test_collect_flags_speeder_and_rule():
    df, dm = _frame()
    rules = [{"kind": "range", "variable": "Q2", "min": 0, "max": 100}]
    flags, skipped = collect_flags(df, dm, rules)
    assert {"check": "speeder", "detail": "short interview (vDurationSec)"} in flags["s3"]
    assert [f["check"] for f in flags["s5"]] == ["out_of_range"]
    assert "straightlining" in skipped and "fraud:shared_connection" in skipped


def test_collect_flags_duplicate():
    df = pl.DataFrame({f"Q{i}": [1, 2, 3, 1] for i in range(1, 19)})
    dm = DataMap()
    for c in df.columns:
        dm.add(Variable(name=c, type=VarType.NUMERIC))
    flags, _ = collect_flags(df, dm, include_fraud=False)
    dup = [k for k, v in flags.items() if any(f["check"] == "duplicate" for f in v)]
    assert dup and set(dup) <= {"row:0", "row:3"}


def test_apply_cleaning_restores_from_raw(tmp_path):
    df, _ = _frame()
    df.write_parquet(tmp_path / "data.parquet")
    assert apply_cleaning(tmp_path, ["s1"]) == {"n_before": 12, "n_after": 11}
    raw_bytes = (tmp_path / "data_raw.parquet").read_bytes()
    assert apply_cleaning(tmp_path, ["s2"]) == {"n_before": 12, "n_after": 11}
    ids = pl.read_parquet(tmp_path / "data.parquet")["vSessionId"].to_list()
    assert "s1" in ids and "s2" not in ids
    assert (tmp_path / "data_raw.parquet").read_bytes() == raw_bytes
    apply_cleaning(tmp_path, [], [{"key": "s0", "variable": "Q2", "new": 7}])
    out = pl.read_parquet(tmp_path / "data.parquet")
    assert out.height == 12 and out["Q2"][0] == 7


def test_annotate_delivery_labels():
    df, dm = _frame()
    out, dm2 = annotate_delivery(df, dm, {"s3": "speeder"})
    assert out["vCleanStatus"][3] == 2 and out["vCleanStatus"][0] == 1
    assert out["vCleanReason"][3] == "speeder" and out["vCleanReason"][0] is None
    v = dm2["vCleanStatus"]
    assert v.values == {1: "Kept", 2: "Removed"} and v.provenance == "fielding_paradata"
    assert "vCleanStatus" not in dm and "vCleanStatus" not in df.columns


def test_replacement_needed_counts_per_quota_cell():
    from src.core.survey_definition import Expression
    from src.engines.fielding.decisions import QuotaCell
    from src.engines.qc.cleaning import replacement_needed

    cells = [
        QuotaCell(id="yes", condition=Expression(op="eq", qid="Q2", value=1), limit=5, count=3),
        QuotaCell(id="no", condition=Expression(op="eq", qid="Q2", value=2), limit=5, count=1),
    ]
    got = replacement_needed({"a": {"Q2": 1}, "b": {"Q2": 1}, "c": {"Q2": 2}}, cells)
    assert got == {"total": 3, "by_cell": {"yes": 2, "no": 1}}

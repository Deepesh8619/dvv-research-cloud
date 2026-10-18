"""Pipeline QC on a fielded export judges the completes, not the screen-outs.

A real Decipher export reported 62 "speeders" — the screen-outs, whose
interviews are short because the screener ended them — and 618 "empty"
columns that were only routing.
"""

import polars as pl

from src.core.models import DataMap, Variable, VarType
from src.orchestration.orchestrator import StudyOrchestrator


def _export() -> tuple[pl.DataFrame, DataMap]:
    n_complete, n_term = 30, 30
    df = pl.DataFrame({
        "status": [3] * n_complete + [1] * n_term,
        "qtime": [900.0 + i for i in range(n_complete)] + [40.0] * n_term,
        "Q5": [1] * n_complete + [None] * n_term,
    })
    dm = DataMap([
        Variable("status", label="Status", type=VarType.SINGLE,
                 values={1: "Terminated", 3: "Qualified"}),
        Variable("qtime", label="Interview time", type=VarType.NUMERIC),
        Variable("Q5", label="Q5", type=VarType.SINGLE, values={1: "Yes"}),
    ])
    return df, dm


def test_screen_outs_are_not_speeders_and_routing_is_not_missing(tmp_path) -> None:
    df, dm = _export()
    qc = StudyOrchestrator(tmp_path)._run_qc(df, dm)
    text = " ".join(qc.warnings)
    assert "Checked on the 30 completes; 30 screen-outs" in text
    assert "completed in under 1/3" not in text
    assert "empty" not in text
    assert qc.metadata["respondent_qc"]["speeders"] == []


def test_a_file_without_status_is_checked_as_before(tmp_path) -> None:
    df, dm = _export()
    qc = StudyOrchestrator(tmp_path)._run_qc(df.drop("status"), DataMap(dm.variables[1:]))
    assert any("empty" in w for w in qc.warnings)

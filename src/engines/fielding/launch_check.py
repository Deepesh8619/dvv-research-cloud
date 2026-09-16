"""The soft-launch check as one pure computation, so it can run off the web process.

Everything the soft-launch pause inspects — the field report, and the QC
cross-check run on the launch batch with terminates INCLUDED (they are the only
evidence the screen-outs fired) — from plain data in, plain dicts out.
"""

import tempfile
from pathlib import Path
from typing import Any

import polars as pl

from src.core.survey_definition import QuestionVariableLink, SurveyDefinition
from src.engines.fielding.decisions import QuotaCell
from src.engines.fielding.field_report import build_field_report
from src.engines.fielding.materialize import ALL_STATUSES, materialize_fielding
from src.engines.qc.discrepancy import check_against_questionnaire


def launch_check(
    survey: SurveyDefinition,
    sessions: list[Any],
    answers: dict[str, dict],
    cells: list[QuotaCell],
) -> dict[str, Any]:
    """Field report + QC cross-check on every captured respondent."""
    report = build_field_report(survey, sessions, answers, cells)
    datamap, df = materialize_fielding(survey, sessions, answers, statuses=ALL_STATUSES)
    # The question->variable mapping is KNOWN here (materialize stamped
    # question_ref on every column), so no fuzzy linker: an exact join can't mis-link.
    by_qid: dict[str, list[str]] = {}
    for var in datamap.variables:
        if var.question_ref:
            by_qid.setdefault(var.question_ref, []).append(var.name)
    links = [QuestionVariableLink(qid=qid, variable_names=names) for qid, names in by_qid.items()]
    with tempfile.TemporaryDirectory() as tmp:
        parquet_path = Path(tmp) / "launch.parquet"
        pl.from_pandas(df, nan_to_null=True).write_parquet(str(parquet_path))
        crosscheck = check_against_questionnaire(survey, links, parquet_path, datamap=datamap)
    return {
        "n_checked_rows": int(len(df)),
        "crosscheck": crosscheck.to_dict(),
        "field_report": report.to_dict(),
    }

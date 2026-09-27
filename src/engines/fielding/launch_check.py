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
        "length_of_interview": loi_against_promise(survey, report.median_loi_sec),
    }


#: How far past the stated length is worth saying out loud. A questionnaire is
#: written before anyone has timed it, so a few minutes either way is normal and
#: flagging it would be noise.
LOI_TOLERANCE = 1.3


def loi_against_promise(
    survey: SurveyDefinition, median_seconds: float | None
) -> dict[str, Any]:
    """What the document promised the respondent against what they actually spent.

    The number is in the document — "the survey will take approximately 15
    minutes" — and the real median is measured from the first respondents. Until
    now nothing compared them, and the two facts sat a metre apart.

    It is worth comparing because the gap costs money in two directions at once:
    a panel is priced on the stated length, so a survey running half as long
    again is being under-paid for, and the respondents who notice abandon
    part-way — which arrives as drop-off nobody attributes to the length.
    """
    stated = ((survey.metadata or {}).get("study_overview") or {}).get("loi_minutes")
    if not isinstance(stated, int) or median_seconds is None:
        return {"stated_minutes": stated, "measured_minutes": None, "over": False}
    measured = round(median_seconds / 60, 1)
    return {
        "stated_minutes": stated,
        "measured_minutes": measured,
        "over": measured > stated * LOI_TOLERANCE,
        "note": (
            f"The document tells respondents about {stated} minutes; the median "
            f"interview is running {measured}. Panel cost is priced on the stated "
            "length, and respondents who notice abandon part-way."
            if measured > stated * LOI_TOLERANCE
            else f"Stated {stated} minutes, running {measured}."
        ),
    }

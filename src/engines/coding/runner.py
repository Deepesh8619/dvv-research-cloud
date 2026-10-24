"""Orchestrator for verbatim coding workflow: sample → codeframe → batch → review."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import polars as pl

from src.engines.coding.codeframe import CodeFrame
from src.engines.coding.coder import batch_code_verbatims, generate_codeframe_from_sample

logger = logging.getLogger(__name__)


@dataclass
class CodingRun:
    """Result of a complete coding job."""

    question_var: str
    codeframe: CodeFrame
    df_coded: pl.DataFrame  # original df + coding_* columns
    n_auto_coded: int
    n_needs_review: int
    confidence_mean: float


def run_coding_job(
    df: pl.DataFrame,
    text_column: str,
    respondent_id_column: str,
    question_text: str,
    n_samples: int = 50,
    min_confidence: float = 0.8,
) -> CodingRun:
    """End-to-end: generate codeframe from sample, code all verbatims, return review queue."""

    # Sample for codeframe generation
    sample_rows = df.sample(n=min(n_samples, df.height), seed=42)
    sample_verbatims = [
        str(v).strip()
        for v in sample_rows[text_column].to_list()
        if v and (not isinstance(v, float) or v == v)
    ][:n_samples]

    if not sample_verbatims:
        raise ValueError("no valid verbatims to sample from")

    codeframe = generate_codeframe_from_sample(question_text, sample_verbatims)
    df_coded = batch_code_verbatims(
        df, text_column, respondent_id_column, codeframe, min_confidence
    )

    n_auto_coded = (~df_coded["needs_coding_review"]).sum()
    n_needs_review = df_coded["needs_coding_review"].sum()
    confidence_mean = df_coded["coding_confidence"].mean()

    return CodingRun(
        question_var=text_column,
        codeframe=codeframe,
        df_coded=df_coded,
        n_auto_coded=int(n_auto_coded),
        n_needs_review=int(n_needs_review),
        confidence_mean=float(confidence_mean),
    )


@dataclass
class ReviewQueueItem:
    """One verbatim awaiting human review."""

    respondent_id: int
    verbatim: str
    suggested_codes: list[str]
    confidence: float
    reasoning: str


def extract_review_queue(run: CodingRun) -> list[ReviewQueueItem]:
    """Extract verbatims that need human review, sorted by low confidence first."""
    review_df = run.df_coded.filter(pl.col("needs_coding_review")).sort("coding_confidence")

    queue = []
    for row in review_df.iter_rows(named=True):
        queue.append(ReviewQueueItem(
            respondent_id=int(row["respondent_id"]),
            verbatim=str(row[run.question_var] or ""),
            suggested_codes=row["codes"] or [],
            confidence=float(row["coding_confidence"]),
            reasoning=row["coding_reasoning"],
        ))
    return queue


def coding_run_to_artifact(run: CodingRun) -> dict:
    """Serialize one coding run for persistence (studies/<run>/coding.json).

    Everything a review UI needs without re-running any model: the codeframe
    (so a reviewer can re-assign codes), the summary counts, and the review
    queue sorted worst-confidence-first. Plain dicts only — this goes through
    json.dumps and back.
    """
    return {
        "question_var": run.question_var,
        "codeframe": {
            "allow_multi": run.codeframe.allow_multi,
            "version": run.codeframe.version,
            "codes": [
                {
                    "id": c.id,
                    "label": c.label,
                    "description": c.description,
                    "type": str(c.type),
                    "parent_id": c.parent_id,
                    "examples": list(c.examples or []),
                }
                for c in run.codeframe.codes
            ],
        },
        "summary": {
            "n_auto_coded": run.n_auto_coded,
            "n_needs_review": run.n_needs_review,
            "confidence_mean": run.confidence_mean,
        },
        "review_queue": [
            {
                "respondent_id": item.respondent_id,
                "verbatim": item.verbatim,
                "suggested_codes": item.suggested_codes,
                "confidence": item.confidence,
                "reasoning": item.reasoning,
                "status": "pending",  # a reviewer flips to accepted/corrected
            }
            for item in extract_review_queue(run)
        ],
    }


def code_open_ends(df: pl.DataFrame, datamap, variables: list[str] | None = None,
                   min_confidence: float = 0.8) -> tuple[pl.DataFrame, dict, dict[str, str]]:
    """Code each open question; returns (coded frame, coding.json blocks, failures).

    A respondent id is added when the file has none (a Decipher export doesn't):
    without it every question failed before the model was asked anything.
    """
    from src.engines.qc.variable_roles import open_end_columns

    if "respondent_id" not in df.columns:
        df = df.with_row_index("respondent_id", offset=1).with_columns(
            pl.col("respondent_id").cast(pl.Int64))
    names = variables or open_end_columns(df, datamap)
    blocks, failed = {}, {}
    for name in names:
        var = datamap[name] if name in datamap else None
        try:
            run = run_coding_job(df, name, "respondent_id",
                                 (var.question_text or var.label) if var else name,
                                 min_confidence=min_confidence)
        except Exception as exc:  # no model reachable, or nothing to code
            logger.warning("coding %s failed: %s", name, exc)
            text = str(exc)
            failed[name] = ("no answers to code" if "no valid verbatims" in text
                            else "no model was reachable; try again in a few minutes"
                            if "backends failed" in text or "unavailable" in text
                            else "the model's answer could not be used")
            continue
        df = run.df_coded.rename({
            "codes": f"{name}_codes", "coding_confidence": f"{name}_coding_confidence",
            "coding_reasoning": f"{name}_coding_reasoning",
            "needs_coding_review": f"{name}_needs_coding_review",
        })
        blocks[name] = coding_run_to_artifact(run)
    return df, blocks, failed

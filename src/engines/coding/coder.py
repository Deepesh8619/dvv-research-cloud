"""Batch verbatim coder: apply a codeframe to all verbatims, with confidence scores."""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from src.ai.router import ModelOutputError, run_task
from src.engines.coding.codeframe import CodeFrame

CODING_SCHEMA = {
    "type": "object",
    "properties": {
        "codes": {
            "type": "array",
            "items": {"type": "string", "description": "code IDs"},
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reasoning": {"type": "string"},
    },
    "required": ["codes", "confidence"],
}


@dataclass
class CodedVerbatim:
    """A verbatim with applied codes."""

    respondent_id: int
    verbatim: str
    codes: list[str]
    confidence: float
    reasoning: str
    needs_review: bool


def generate_codeframe_from_sample(
    question_text: str,
    sample_verbatims: list[str],
    timeout: int = 120,
) -> CodeFrame:
    """Generate a codeframe from sample verbatims via LLM."""
    from src.engines.coding.codeframe import (
        CODEFRAME_GENERATION_PROMPT,
        CODEFRAME_GENERATION_SCHEMA,
        Code,
    )

    prompt = CODEFRAME_GENERATION_PROMPT.format(
        question_text=question_text,
        n_samples=len(sample_verbatims),
        samples="\n".join(f"- {v[:200]}" for v in sample_verbatims[:10]),
    )
    try:
        result = run_task(
            "coding.codeframe",
            prompt,
            CODEFRAME_GENERATION_SCHEMA,
            timeout=timeout,
        )
    except (ModelOutputError, KeyError) as exc:
        raise ValueError(f"codeframe generation failed: {exc}") from exc

    codes = [
        Code(
            id=c["id"],
            label=c["label"],
            description=c["description"],
            examples=c.get("examples", []),
        )
        for c in result.content["codes"]
    ]
    return CodeFrame(question_var="", codes=codes)


def code_verbatim(
    verbatim: str,
    codeframe: CodeFrame,
    min_confidence_for_auto_apply: float = 0.8,
    timeout: int = 60,
) -> CodedVerbatim:
    """Code one verbatim and return codes + confidence."""
    codeframe_text = codeframe.to_prompt_text()
    prompt = f"""{codeframe_text}

Verbatim to code:
"{verbatim}"

Apply the most relevant codes. If unsure, leave codes empty.
Return only the JSON object, no other text."""

    try:
        result = run_task(
            "coding.apply",
            prompt,
            CODING_SCHEMA,
            timeout=timeout,
        )
    except (ModelOutputError, KeyError) as exc:
        return CodedVerbatim(
            respondent_id=0,
            verbatim=verbatim,
            codes=[],
            confidence=0.0,
            reasoning=f"Model error: {exc}",
            needs_review=True,
        )

    codes = result.content.get("codes", [])
    confidence = float(result.content.get("confidence", 0.0))
    reasoning = result.content.get("reasoning", "")

    return CodedVerbatim(
        respondent_id=0,
        verbatim=verbatim,
        codes=codes,
        confidence=confidence,
        reasoning=reasoning,
        needs_review=confidence < min_confidence_for_auto_apply,
    )


def batch_code_verbatims(
    df: pl.DataFrame,
    text_column: str,
    respondent_id_column: str,
    codeframe: CodeFrame,
    min_confidence: float = 0.8,
) -> pl.DataFrame:
    """Apply codeframe to all verbatims in a dataframe.

    Returns df with added columns: codes (list), confidence, reasoning, needs_review.
    """
    results = []
    for row in df.iter_rows(named=True):
        verbatim = row[text_column]
        resp_id = row[respondent_id_column]
        if not verbatim or (isinstance(verbatim, float) and verbatim != verbatim):  # NaN
            results.append({
                "respondent_id": resp_id,
                "codes": [],
                "confidence": 0.0,
                "reasoning": "empty",
                "needs_review": False,
            })
            continue
        coded = code_verbatim(str(verbatim), codeframe, min_confidence)
        coded.respondent_id = resp_id
        results.append({
            "respondent_id": coded.respondent_id,
            "codes": coded.codes,
            "confidence": coded.confidence,
            "reasoning": coded.reasoning,
            "needs_review": coded.needs_review,
        })
    return df.with_columns(
        pl.Series("codes", [r["codes"] for r in results]),
        pl.Series("coding_confidence", [r["confidence"] for r in results]),
        pl.Series("coding_reasoning", [r["reasoning"] for r in results]),
        pl.Series("needs_coding_review", [r["needs_review"] for r in results]),
    )

"""Intake brain step 1: classify an uploaded file.

Cheap signals first (extension, sniffed structure); the local LLM only breaks
ties for ambiguous tabular files. Every result carries a confidence and is
confirmed by a human before anything downstream runs (architecture §3.2).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.ai.router import BackendUnavailable, ModelOutputError, run_task

CATEGORIES = [
    "data_file",  # respondent-level data (sav/parquet/csv of records)
    "datamap",  # variable dictionary document
    "questionnaire",  # survey document (Word/PDF text)
    "verbatims",  # open-end text export
    "tab_spec",  # banner/table plan document
    "codeframe",  # code list for verbatim coding
    "unknown",
]

CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": CATEGORIES},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string"},
    },
    "required": ["category", "confidence", "reason"],
}

PROMPT = """You classify files arriving at a market research data processing firm.

File name: {name}
First lines / sniffed content:
{preview}

Classify into exactly one category:
- data_file: respondent-level survey data (one row per respondent)
- datamap: a variable dictionary (variable names, labels, value labels)
- questionnaire: a survey questionnaire document
- verbatims: open-ended text responses for coding
- tab_spec: a tab plan / banner specification
- codeframe: a list of codes/themes for verbatim coding
- unknown: none of the above
"""


@dataclass
class Classification:
    category: str
    confidence: float
    reason: str
    method: str  # "rule" or model name


def classify_file(path: str | Path, preview_bytes: int = 2000) -> Classification:
    path = Path(path)
    suffix = path.suffix.lower()

    if suffix in (".sav", ".zsav"):
        return Classification("data_file", 0.99, "SPSS data file extension", "rule")
    if suffix == ".parquet":
        return Classification("data_file", 0.99, "Parquet data file", "rule")
    if suffix in (".xlsx", ".xls"):
        # Binary format: a text preview is useless to the LLM. Excel uploads
        # to a DP shop are respondent-level exports in practice.
        return Classification("data_file", 0.85, "Excel workbook (tabular export)", "rule")

    try:
        preview = path.read_bytes()[:preview_bytes].decode("utf-8", errors="replace")
    except OSError:
        preview = ""

    if suffix == ".csv":
        first_line = preview.splitlines()[0] if preview else ""
        if first_line.count(",") >= 3:
            return Classification(
                "data_file", 0.75, "CSV with wide header (one row per respondent)", "rule"
            )

    try:
        result = run_task(
            "intake.classify",
            PROMPT.format(name=path.name, preview=preview),
            CLASSIFY_SCHEMA,
        )
        return Classification(
            category=result.content["category"],
            confidence=float(result.content["confidence"]),
            reason=result.content["reason"],
            method=f"{result.backend}:{result.model}",
        )
    except (BackendUnavailable, ModelOutputError) as exc:
        return Classification("unknown", 0.0, f"AI backend unavailable: {exc}", "rule")

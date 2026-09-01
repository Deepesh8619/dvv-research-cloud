"""Intake brain step 1: classify an uploaded file.

Cheap signals first (extension, sniffed structure); the local LLM only breaks
ties for ambiguous tabular files. Every result carries a confidence and is
confirmed by a human before anything downstream runs (architecture §3.2).
"""

from __future__ import annotations

import re
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


# Signals that a Word document is a questionnaire. Every one of these is a
# scripter convention taken from real client documents, not a guess about
# wording: a question label at the start of a line, a bracketed programmer
# note, or a spec-table row label. Two independent hits is the bar — one
# stray "TERMINATE" in a datamap should not decide the file's identity.
_QRE_SIGNALS: list[tuple[str, re.Pattern]] = [
    ("question labels", re.compile(r"^\s*(?:[A-Z]{0,3}\d+[a-z]?)\s*[.):]\s+\S", re.M)),
    ("programmer notes", re.compile(r"\[\s*(?:PN|P/N|PROGRAMMER)\b", re.I)),
    ("screen-out directives", re.compile(r"\bTERMINATE\b|\bSCREEN[- ]?OUT\b")),
    (
        "spec-table rows",
        re.compile(r"^\s*(?:Q Name|Response list|Among who|Q type)\s*:", re.I | re.M),
    ),
    ("randomisation directives", re.compile(r"\bRANDOMI[SZ]E\b|\bROTATE\b|\bANCHOR\b")),
]


def _docx_preview(path: Path) -> str:
    """Readable text from a .docx, for classification.

    A .docx is a ZIP, so `read_bytes()` hands the model the archive header and
    a wall of NULs — it was being asked to categorise binary noise on every
    Word upload. Extracting the real text is what makes both the rules below
    and any fallback model call meaningful.
    """
    try:
        from src.intake.questionnaire_loader import load_docx

        blocks = load_docx(path)
    except Exception:
        # Corrupt or unreadable: fall through to the model with no preview
        # rather than failing the upload outright.
        return ""
    return "\n".join(b.text for b in blocks if getattr(b, "text", ""))


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
    if suffix in (".docx", ".doc"):
        text = _docx_preview(path)
        hits = [name for name, pattern in _QRE_SIGNALS if pattern.search(text)]
        if len(hits) >= 2:
            return Classification(
                "questionnaire",
                0.95,
                f"Word document with {', '.join(hits)}",
                "rule",
            )
        # Not obviously a questionnaire (a datamap, tab plan or codeframe can
        # also arrive as .docx). Ask the model — but with the real text.
        return _classify_by_model(path, text)

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

    return _classify_by_model(path, preview)


def _classify_by_model(path: Path, preview: str) -> Classification:
    """Last resort: ask the model, with whatever readable preview we have."""
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

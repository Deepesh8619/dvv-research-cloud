"""Study manifest: tracks files and state through the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path


class FileType(StrEnum):
    SPSS_STUDY = "spss_study"
    CSV_DATA = "csv_data"
    VERBATIMS = "verbatims"
    TABPLAN = "tabplan"
    CODEFRAME = "codeframe"
    WORD_DOC = "word_doc"


class StudyStage(StrEnum):
    INTAKE = "intake"  # File uploaded, waiting classification
    CLASSIFIED = "classified"  # File type identified, awaiting QC approval
    QC_PASSED = "qc_passed"  # Data validated, ready for processing
    CODING_DONE = "coding_done"  # Verbatims coded, crosstabs ready
    RENDERED = "rendered"  # Decks (Excel/PPT) generated
    APPROVED_FOR_DELIVERY = "approved_for_delivery"  # Ready for client
    DELIVERED = "delivered"  # Sent to client


@dataclass
class FileManifest:
    """One uploaded file with metadata."""

    path: Path
    file_type: FileType | None = None
    classified_confidence: float = 0.0
    detected_n_respondents: int = 0
    detected_n_questions: int = 0


@dataclass
class QCReport:
    """Quality control findings."""

    passed: bool
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


@dataclass
class StudyRun:
    """Tracks one study through the entire pipeline."""

    run_id: str  # "study_20250704_abc123"
    client_name: str
    created_at: datetime
    source_file: FileManifest
    stage: StudyStage = StudyStage.INTAKE
    approval_chain: dict = field(default_factory=dict)  # {gate: (approved_by, timestamp)}
    qc_report: QCReport | None = None
    datamap_path: Path | None = None
    crosstabs_path: Path | None = None
    coding_path: Path | None = None  # coding.json: codeframes + review queues
    excel_deck_path: Path | None = None
    pptx_deck_path: Path | None = None
    slack_thread_id: str | None = None
    notes: str = ""

    def approve(self, gate: str, approved_by: str) -> None:
        """Mark a gate as approved."""
        self.approval_chain[gate] = (approved_by, datetime.now().isoformat())

    def is_approved(self, gate: str) -> bool:
        """Check if a gate was already approved."""
        return gate in self.approval_chain

    def needs_approval(self, gate: str) -> bool:
        """Check if a gate requires approval."""
        return not self.is_approved(gate)

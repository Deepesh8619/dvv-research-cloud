"""Approval gates: human review checkpoints in the workflow."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ApprovalGate:
    """One step where human approval is required."""

    name: str  # "classify", "qc", "delivery"
    description: str
    auto_pass_if: callable = None  # function(study_run) -> bool

    def should_skip(self, study_run) -> bool:
        """Auto-pass gate if condition is met."""
        if self.auto_pass_if is None:
            return False
        return self.auto_pass_if(study_run)


class ApprovalGates:
    """Standard approval gates for study processing."""

    CLASSIFY = ApprovalGate(
        name="classify",
        description="Verify file type detected correctly (SPSS, CSV, verbatims, etc)",
        auto_pass_if=lambda s: s.source_file.classified_confidence > 0.95,
    )

    QC = ApprovalGate(
        name="qc",
        description="Verify data quality checks passed (n, questions, variable mapping)",
        auto_pass_if=lambda s: s.qc_report and s.qc_report.passed and len(s.qc_report.issues) == 0,
    )

    DELIVERY = ApprovalGate(
        name="delivery",
        description="Final deck review before sending to client",
        auto_pass_if=None,  # Always requires human review
    )

    @staticmethod
    def all_gates() -> list[ApprovalGate]:
        """Return list of all gates in order."""
        return [ApprovalGates.CLASSIFY, ApprovalGates.QC, ApprovalGates.DELIVERY]

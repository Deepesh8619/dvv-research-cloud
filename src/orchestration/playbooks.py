"""Pre-configured playbooks for common study processing workflows."""

from __future__ import annotations

from pathlib import Path

from src.orchestration.orchestrator import StudyOrchestrator


class Playbooks:
    """Standard workflows."""

    @staticmethod
    def fast_intake(
        client_name: str,
        source_file: Path,
        work_dir: str | Path = "studies",
    ):
        """
        Fast intake: auto-approve all gates, no human review delays.
        Use for trusted data sources or internal testing.
        """
        orch = StudyOrchestrator(work_dir=work_dir)
        return orch.run_study(
            client_name=client_name,
            source_file=source_file,
            auto_approve_classify=True,
            auto_approve_qc=True,
            require_delivery_approval=False,
        )

    @staticmethod
    def standard_intake(
        client_name: str,
        source_file: Path,
        tab_plan_file: Path | None = None,
        work_dir: str | Path = "studies",
    ):
        """
        Standard intake: auto-approve classify/QC if confident, require delivery approval.
        Use for normal client projects.
        """
        orch = StudyOrchestrator(work_dir=work_dir)
        return orch.run_study(
            client_name=client_name,
            source_file=source_file,
            tab_plan_file=tab_plan_file,
            auto_approve_classify=True,
            auto_approve_qc=True,
            require_delivery_approval=True,
        )

    @staticmethod
    def strict_intake(
        client_name: str,
        source_file: Path,
        tab_plan_file: Path | None = None,
        work_dir: str | Path = "studies",
    ):
        """
        Strict intake: require human approval at every gate.
        Use for high-stakes projects or new data sources.
        """
        orch = StudyOrchestrator(work_dir=work_dir)
        return orch.run_study(
            client_name=client_name,
            source_file=source_file,
            tab_plan_file=tab_plan_file,
            auto_approve_classify=False,
            auto_approve_qc=False,
            require_delivery_approval=True,
        )

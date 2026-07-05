"""End-to-end orchestration test: file upload through delivery."""

from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd
import pytest

from src.adapters.spss import write_sav
from src.core.models import DataMap, Variable, VarType
from src.orchestration.manifest import StudyStage
from src.orchestration.playbooks import Playbooks


@pytest.fixture
def synthetic_study(tmp_path) -> tuple[Path, DataMap]:
    """Create a minimal synthetic SPSS study for testing."""
    # Create a small dataset
    df = pd.DataFrame({
        "respondent_id": [1, 2, 3, 4, 5],
        "age": [25, 35, 45, 55, 65],
        "income": [30000, 50000, 75000, 100000, 120000],
        "q1_satisfaction": [4, 5, 3, 5, 4],
        "q2_feedback": ["good", "excellent", "okay", "great", "good"],
    })

    # Create datamap
    variables = [
        Variable("respondent_id", label="Respondent ID", type=VarType.NUMERIC),
        Variable("age", label="Age", type=VarType.NUMERIC, values={25: "25", 35: "35", 45: "45"}),
        Variable("income", label="Annual Income", type=VarType.NUMERIC),
        Variable(
            "q1_satisfaction",
            label="How satisfied are you?",
            type=VarType.SINGLE,
            values={
                1: "Very Dissatisfied",
                2: "Dissatisfied",
                3: "Neutral",
                4: "Satisfied",
                5: "Very Satisfied",
            },
        ),
        Variable("q2_feedback", label="Additional feedback", type=VarType.TEXT),
    ]
    datamap = DataMap()
    for v in variables:
        datamap.add(v)

    # Write SPSS file
    spss_path = tmp_path / "test_study.sav"
    write_sav(df, datamap, str(spss_path))

    return spss_path, datamap


def test_fast_intake_workflow(synthetic_study):
    """Fast intake: auto-approve all gates, no delays."""
    spss_path, datamap = synthetic_study

    with TemporaryDirectory() as work_dir:
        study = Playbooks.fast_intake(
            client_name="TestClient",
            source_file=spss_path,
            work_dir=work_dir,
        )

        # Verify workflow completed (no tab plan so no decks)
        assert study.run_id.startswith("study_")
        assert study.client_name == "TestClient"
        assert study.stage == StudyStage.DELIVERED
        assert study.qc_report is not None


def test_standard_intake_workflow(synthetic_study):
    """Standard intake: auto-approve classify/QC, require delivery approval."""
    spss_path, datamap = synthetic_study

    with TemporaryDirectory() as work_dir:
        study = Playbooks.standard_intake(
            client_name="PharmaCo",
            source_file=spss_path,
            work_dir=work_dir,
        )

        # Verify workflow state (no tab plan so no decks)
        assert study.stage == StudyStage.APPROVED_FOR_DELIVERY
        assert "delivery" in study.approval_chain  # Marked for delivery review
        assert study.qc_report is not None
        assert study.run_id.startswith("study_")


def test_qc_detects_issues(tmp_path):
    """QC should flag small samples and high missingness."""
    # Create dataset with only 5 rows and high missingness
    df = pd.DataFrame({
        "respondent_id": [1, 2, 3, 4, 5],
        "var1": [1.0, None, None, None, None],  # 80% missing
    })

    variables = [
        Variable("respondent_id", label="ID", type=VarType.NUMERIC),
        Variable("var1", label="Var1", type=VarType.NUMERIC),
    ]
    datamap = DataMap()
    for v in variables:
        datamap.add(v)

    spss_path = tmp_path / "small.sav"
    write_sav(df, datamap, str(spss_path))

    with TemporaryDirectory() as work_dir:
        study = Playbooks.standard_intake(
            client_name="SmallTest",
            source_file=spss_path,
            work_dir=work_dir,
        )

        # QC should have warnings and issues (no tab plan means no decks)
        assert study.qc_report is not None
        assert len(study.qc_report.warnings) > 0  # Should warn about small sample
        assert len(study.qc_report.issues) > 0  # Should have issues (very small n)
        # When require_delivery_approval=True (standard_intake), stage is APPROVED_FOR_DELIVERY
        assert study.stage in (StudyStage.RENDERED, StudyStage.APPROVED_FOR_DELIVERY)


def test_study_approval_chain():
    """Approval gates track who approved what and when."""
    from datetime import datetime

    from src.orchestration.manifest import FileManifest, FileType, StudyRun

    manifest = FileManifest(path=Path("test.sav"), file_type=FileType.SPSS_STUDY)
    study = StudyRun(
        run_id="test_001",
        client_name="Test",
        created_at=datetime.now(),
        source_file=manifest,
    )

    # Initially no approvals
    assert not study.is_approved("classify")
    assert study.needs_approval("classify")

    # Simulate approval
    study.approve("classify", "analyst_john")
    assert study.is_approved("classify")
    assert not study.needs_approval("classify")

    # Check approval metadata
    approved_by, timestamp = study.approval_chain["classify"]
    assert approved_by == "analyst_john"
    assert timestamp  # ISO format datetime


def test_study_paths_created_correctly(synthetic_study):
    """Verify study completes even without tab plan (no deck paths)."""
    spss_path, _ = synthetic_study

    with TemporaryDirectory() as work_dir:
        study = Playbooks.fast_intake(
            client_name="PathTest",
            source_file=spss_path,
            work_dir=work_dir,
        )

        # Without tab plan, no decks are rendered
        assert study.stage == StudyStage.DELIVERED
        assert study.datamap_path is not None  # But datamap is saved
        assert study.run_id.startswith("study_")


def test_orchestrator_skips_tables_without_plan(synthetic_study):
    """If no tab plan provided, crosstabs are skipped gracefully."""
    spss_path, _ = synthetic_study

    with TemporaryDirectory() as work_dir:
        study = Playbooks.fast_intake(
            client_name="NoTabPlan",
            source_file=spss_path,
            work_dir=work_dir,
        )

        # Should still complete even without tab plan
        assert study.stage == StudyStage.DELIVERED

"""
MVP Demo: Full End-to-End DVV Research Cloud Pipeline

Demonstrates:
1. File intake & classification
2. Data quality checks
3. Verbatim coding (if open-ends present)
4. Crosstab generation with RIM weighting
5. Multi-format deck rendering (Excel + PPT)
6. Approval gates workflow

Usage:
    python scripts/run_mvp_demo.py [--client NAME] [--output DIR]
"""

import argparse
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.orchestration.playbooks import Playbooks

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description="DVV Research Cloud MVP - Full Pipeline Demo"
    )
    parser.add_argument(
        "--client",
        default="PharmaRx Co",
        help="Client name for study tracking",
    )
    parser.add_argument(
        "--output",
        default="mvp_output",
        help="Output directory for study results",
    )
    parser.add_argument(
        "--sample-data",
        default="data/pharma_study.sav",
        help="Path to sample SPSS file",
    )
    parser.add_argument(
        "--tab-plan",
        default="specs/pharma_tabplan.yaml",
        help="Path to tab plan YAML",
    )
    args = parser.parse_args()

    source_file = Path(args.sample_data)
    if not source_file.exists():
        logger.error(f"Sample data not found: {source_file}")
        logger.info("Run: python scripts/generate_sample_data.py")
        sys.exit(1)

    tab_plan = Path(args.tab_plan)
    if not tab_plan.exists():
        logger.warning(f"Tab plan not found: {tab_plan} - will skip crosstabs")
        tab_plan = None

    logger.info("=" * 70)
    logger.info("DVV Research Cloud MVP - Full Pipeline Demo")
    logger.info("=" * 70)
    logger.info(f"Client: {args.client}")
    logger.info(f"Source: {source_file}")
    logger.info(f"Output: {args.output}")
    logger.info("")

    # Run standard intake workflow
    logger.info("Starting orchestration workflow...")
    study = Playbooks.standard_intake(
        client_name=args.client,
        source_file=source_file,
        tab_plan_file=tab_plan,
        work_dir=args.output,
    )

    # Print results
    logger.info("")
    logger.info("=" * 70)
    logger.info("WORKFLOW COMPLETE")
    logger.info("=" * 70)
    logger.info(f"Study ID: {study.run_id}")
    logger.info(f"Stage: {study.stage}")
    logger.info(f"QC Report: {'PASSED' if study.qc_report.passed else 'FAILED'}")
    if study.qc_report.issues:
        logger.warning(f"  Issues: {study.qc_report.issues}")
    if study.qc_report.warnings:
        logger.info(f"  Warnings: {study.qc_report.warnings}")

    logger.info("")
    logger.info("Output Files:")
    if study.excel_deck_path:
        logger.info(f"  ✅ Excel Deck: {study.excel_deck_path}")
    if study.pptx_deck_path:
        logger.info(f"  ✅ PowerPoint Deck: {study.pptx_deck_path}")
    if study.datamap_path:
        logger.info(f"  ✅ Data Map: {study.datamap_path}")

    logger.info("")
    logger.info("Approval Chain:")
    for gate, (approver, timestamp) in study.approval_chain.items():
        logger.info(f"  {gate}: {approver} @ {timestamp}")

    logger.info("")
    logger.info("=" * 70)
    logger.info("✅ MVP DEMO COMPLETE")
    logger.info("=" * 70)

    return 0


if __name__ == "__main__":
    sys.exit(main())

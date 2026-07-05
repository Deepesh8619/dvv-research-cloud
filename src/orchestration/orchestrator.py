"""Main orchestrator: chains intake, QC, coding, crosstabs, rendering."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import polars as pl

from src.adapters.spss import read_sav
from src.adapters.tabular import read_tabular
from src.core.models import DataMap, VarType
from src.engines.coding.runner import run_coding_job
from src.engines.tabs.runner import deck_to_json, run_tab_plan
from src.engines.tabs.spec import TableSpec, TabPlan, auto_nets, load_tab_plan
from src.intake.classify import classify_file
from src.orchestration.approvals import ApprovalGates
from src.orchestration.manifest import QCReport, StudyRun, StudyStage
from src.renderers.excel import render_deck as render_excel
from src.renderers.pptx import render_deck as render_pptx

logger = logging.getLogger(__name__)


def load_study_data(path: str | Path) -> tuple[pl.DataFrame, DataMap]:
    """Load any supported study file into the canonical (DataFrame, DataMap) pair.

    Format dispatch lives here so nothing downstream — QC, coding, tabs,
    renderers — ever knows what the source file was.
    """
    suffix = Path(path).suffix.lower()
    if suffix in (".sav", ".zsav"):
        df_pd, datamap = read_sav(str(path))
    elif suffix in (".csv", ".xlsx", ".xls"):
        df_pd, datamap = read_tabular(path)
    else:
        raise ValueError(f"unsupported data file format: {suffix}")
    return pl.from_pandas(df_pd), datamap


# Auto tab plans on wide datafeed exports (500+ columns) could explode into
# hundreds of tables; cap to keep first-pass decks reviewable.
MAX_AUTO_TABLES = 30

# System/paradata columns in survey platform exports (Decipher et al.):
# respondent bookkeeping, device fingerprints, quota flags, hidden derived
# variables. Real questions look like S1/Q5r2/C2/D3.
_SYSTEM_EXACT = {
    "record", "uuid", "date", "markers", "status", "vendorid", "zip",
    "daytime", "realstart", "laststart",
}
_SYSTEM_PREFIXES = ("hid", "h_", "hcf", "dummy", "noanswer", "tt_", "geoinfor", "respinfor")
_CAMEL_SYSTEM_RE = re.compile(r"^[dvh][A-Z]")  # dTrack, vUserAgent, hSample


def _is_system_variable(name: str) -> bool:
    low = name.lower()
    if low in _SYSTEM_EXACT or low.endswith("flag"):
        return True
    if low.startswith(_SYSTEM_PREFIXES):
        return True
    return bool(_CAMEL_SYSTEM_RE.match(name))


def auto_tab_plan(datamap: DataMap, title: str = "Topline Tables") -> TabPlan | None:
    """Build a default tab plan from the datamap when the client sent no tab spec.

    Banner: up to 3 question variables with 2-6 short-labelled categories
    (demographic-like). Stubs: every other question variable with 2+ codes,
    with auto T2B/B2B nets and means on 5+ point scales. System/paradata
    columns are excluded entirely.
    """
    singles = [
        v
        for v in datamap.variables
        if v.type == VarType.SINGLE
        and v.values
        and len(v.valid_codes()) >= 2
        and not _is_system_variable(v.name)
    ]
    banner = [
        v.name
        for v in singles
        if 2 <= len(v.valid_codes()) <= 6
        and max(len(label) for label in v.values.values()) <= 24
    ][:3]
    stubs = [v for v in singles if v.name not in banner]
    if not banner or not stubs:
        return None

    tables = [
        TableSpec(stub=v.name, nets=auto_nets(v), mean=len(v.valid_codes()) >= 5)
        for v in stubs[:MAX_AUTO_TABLES]
    ]
    return TabPlan(title=title, banner=banner, tables=tables)


class StudyOrchestrator:
    """Orchestrates a study from file upload to client delivery."""

    def __init__(self, work_dir: str | Path = "studies"):
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)

    def run_study(
        self,
        client_name: str,
        source_file: Path,
        tab_plan_file: Path | None = None,
        min_coding_confidence: float = 0.8,
        auto_approve_classify: bool = False,
        auto_approve_qc: bool = False,
        require_delivery_approval: bool = True,
    ) -> StudyRun:
        """
        End-to-end: intake → QC → code → tabs → render.

        Args:
            client_name: Client name for tracking
            source_file: Path to uploaded SPSS/CSV/etc
            tab_plan_file: Optional path to YAML tab spec
            min_coding_confidence: Min confidence for auto-coding
            auto_approve_classify: Skip classify gate if confidence > 95%
            auto_approve_qc: Skip QC gate if all checks pass
            require_delivery_approval: Always require delivery approval

        Returns:
            StudyRun with all paths populated
        """
        run_id = f"study_{datetime.now().strftime('%Y%m%d')}_{uuid4().hex[:6]}"
        study_dir = self.work_dir / run_id
        study_dir.mkdir(parents=True, exist_ok=True)

        # 1. CLASSIFY
        logger.info(f"[{run_id}] Classifying {source_file.name}...")
        classification = classify_file(str(source_file))
        logger.info(
            f"[{run_id}] → {classification.category} (confidence: {classification.confidence:.2f})"
        )

        from src.orchestration.manifest import FileManifest, FileType

        # Map classification category to FileType
        category_to_type = {
            "data_file": FileType.SPSS_STUDY,
            "verbatims": FileType.VERBATIMS,
            "tab_spec": FileType.TABPLAN,
            "codeframe": FileType.CODEFRAME,
            "datamap": FileType.CSV_DATA,
            "questionnaire": FileType.WORD_DOC,
        }
        file_type = category_to_type.get(classification.category, FileType.SPSS_STUDY)

        manifest = FileManifest(
            path=source_file,
            file_type=file_type,
            classified_confidence=classification.confidence,
        )
        study = StudyRun(
            run_id=run_id,
            client_name=client_name,
            created_at=datetime.now(),
            source_file=manifest,
            stage=StudyStage.CLASSIFIED,
        )

        if auto_approve_classify and ApprovalGates.CLASSIFY.should_skip(study):
            # Auto-approve if confidence > 95%
            study.approve("classify", "auto")
        else:
            logger.info(
                f"[{run_id}] AWAITING CLASSIFICATION APPROVAL "
                f"(confidence: {classification.confidence:.2f})"
            )
            study.approve("classify", "manual_pending")

        # 2. LOAD DATA (once — QC and every engine reuse this frame)
        logger.info(f"[{run_id}] Loading {source_file.suffix} data...")
        try:
            df, datamap = load_study_data(source_file)
        except Exception as exc:
            study.qc_report = QCReport(passed=False, issues=[f"Failed to load file: {exc}"])
            study.stage = StudyStage.CLASSIFIED
            logger.error(f"[{run_id}] Load failed: {exc}")
            raise
        logger.info(f"[{run_id}] → {len(df)} respondents, {len(datamap.variables)} variables")

        # 3. QC (validate the loaded data)
        logger.info(f"[{run_id}] Running QC checks...")
        qc = self._run_qc(df, datamap)
        study.qc_report = qc
        logger.info(f"[{run_id}] QC: {'PASSED' if qc.passed else 'FAILED'}")
        if qc.issues:
            logger.warning(f"[{run_id}] QC issues: {qc.issues}")

        if auto_approve_qc and ApprovalGates.QC.should_skip(study):
            # Auto-approve if QC passed
            study.approve("qc", "auto")
        else:
            logger.info(f"[{run_id}] AWAITING QC APPROVAL")
            study.approve("qc", "manual_pending")

        study.stage = StudyStage.QC_PASSED

        # Persist the canonical pair: the navigator and any later re-compute
        # need the DataMap without re-parsing the source file (Excel parses
        # take ~20s on wide exports).
        study.datamap_path = study_dir / "datamap.json"
        study.datamap_path.write_text(datamap.to_json())
        data_parquet_path = study_dir / "data.parquet"
        df.write_parquet(str(data_parquet_path))

        # 4. VERBATIM CODING (if any open-ends)
        from src.core.models import VarType
        open_ends = [v for v in datamap.variables if v.type == VarType.TEXT]
        if open_ends:
            logger.info(f"[{run_id}] Found {len(open_ends)} open-end questions; auto-coding...")
            for var in open_ends:
                try:
                    coding_run = run_coding_job(
                        df,
                        var.name,
                        "respondent_id",
                        var.label,
                        min_confidence=min_coding_confidence,
                    )
                    df = coding_run.df_coded
                    logger.info(
                        f"[{run_id}] {var.name}: {coding_run.n_auto_coded} auto-coded, "
                        f"{coding_run.n_needs_review} need review"
                    )
                except Exception as e:
                    logger.error(f"[{run_id}] Coding failed for {var.name}: {e}")

            study.stage = StudyStage.CODING_DONE

        # 5. CROSSTABS & RENDERING
        if tab_plan_file and tab_plan_file.exists():
            tab_plan = load_tab_plan(str(tab_plan_file), datamap)
        else:
            # No tab plan provided: auto-generate one from the datamap
            tab_plan = auto_tab_plan(datamap, title=f"{client_name} — Topline Tables")
            if tab_plan:
                logger.info(
                    f"[{run_id}] Auto tab plan: banner={tab_plan.banner}, "
                    f"{len(tab_plan.tables)} tables"
                )

        if tab_plan:
            logger.info(f"[{run_id}] Running crosstabs...")
            deck_run = run_tab_plan(str(study_dir / "data.parquet"), tab_plan, datamap)
            study.crosstabs_path = study_dir / "crosstabs.json"
            deck_to_json(deck_run, study.crosstabs_path)

            # 6. RENDER DECKS
            logger.info(f"[{run_id}] Rendering Excel deck...")
            study.excel_deck_path = study_dir / f"{client_name}_deck.xlsx"
            render_excel(deck_run, str(study.excel_deck_path))
            logger.info(f"[{run_id}] → {study.excel_deck_path}")

            logger.info(f"[{run_id}] Rendering PPT deck...")
            study.pptx_deck_path = study_dir / f"{client_name}_deck.pptx"
            render_pptx(deck_run, str(study.pptx_deck_path))
            logger.info(f"[{run_id}] → {study.pptx_deck_path}")
        else:
            logger.info(f"[{run_id}] No usable variables for tables; skipping deck rendering")

        study.stage = StudyStage.RENDERED

        # 7. DELIVERY GATE
        if require_delivery_approval:
            logger.info(f"[{run_id}] AWAITING DELIVERY APPROVAL (decks ready for review)")
            study.approve("delivery", "pending")
            study.stage = StudyStage.APPROVED_FOR_DELIVERY
        else:
            logger.info(f"[{run_id}] Study complete and ready for delivery")
            study.stage = StudyStage.DELIVERED

        logger.info(f"[{run_id}] ✅ COMPLETE")
        return study

    def _run_qc(self, df: pl.DataFrame, datamap: DataMap) -> QCReport:
        """Validate the loaded data."""
        issues = []
        warnings = []
        metadata = {
            "n_respondents": len(df),
            "n_variables": len(datamap.variables),
        }

        if len(df) < 10:
            issues.append(f"Very small sample (n={len(df)})")
        if len(df) < 100:
            warnings.append(f"Small sample (n={len(df)})")

        if len(datamap.variables) < 1:
            issues.append("No variables found")

        # Missingness: high-missing columns are normal in survey exports
        # (skip logic), so summarize rather than flag each of potentially
        # hundreds of columns as an issue.
        n = max(len(df), 1)
        high_missing = [c for c in df.columns if df[c].null_count() / n > 0.5]
        moderate_missing = [c for c in df.columns if 0.2 < df[c].null_count() / n <= 0.5]
        if high_missing:
            warnings.append(
                f"{len(high_missing)} of {len(df.columns)} columns are >50% empty "
                "(often skip logic — review before weighting on them)"
            )
        if moderate_missing:
            warnings.append(f"{len(moderate_missing)} columns are 20–50% empty")

        passed = len(issues) == 0
        return QCReport(passed=passed, issues=issues, warnings=warnings, metadata=metadata)

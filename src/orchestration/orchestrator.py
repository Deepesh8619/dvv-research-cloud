"""Main orchestrator: chains intake, QC, coding, crosstabs, rendering."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import polars as pl

from src.adapters.spss import read_sav
from src.adapters.tabular import read_tabular
from src.core.models import DataMap, VarType
from src.engines.coding.runner import coding_run_to_artifact, run_coding_job
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
# Prefixes with no legitimate demographic meaning under any convention —
# excluded everywhere (stub candidacy AND banner candidacy).
_ALWAYS_JUNK_PREFIXES = ("noanswer", "geoinfor", "respinfor")
# Decipher-style exports also use hid_/h_/dummy_/tt_ for hand-recoded,
# analyst-cleaned demographics (e.g. Hid_S1 = a 3-category Female/Male/Other
# recode of a messier raw question, TT_AGE_RANGES = clean 18-24/25-34/... age
# bands, Hid_INCOME_Quota = income bands) — often the BEST banner candidates
# in a file, since they're pre-cleaned. The prefix alone can't distinguish
# these from true paradata, so it only excludes STUB candidacy (a "Hid_"
# stub table is usually a redundant recode of a question that's already its
# own stub); banner candidacy is judged by label quality instead (see
# _is_paradata_variable and the label-length check in auto_tab_plan).
_STUB_ONLY_PREFIXES = ("hid", "h_", "hcf", "dummy")
_CAMEL_SYSTEM_RE = re.compile(r"^[dvh][A-Z]")  # dTrack, vUserAgent, hSample
_OTHER_SPECIFY_RE = re.compile(r"r\d+oe$")  # Q5r98oe


def _is_system_variable(name: str) -> bool:
    """Excludes a variable from STUB candidacy: paradata plus recode-style
    prefixes that would otherwise duplicate their source question as a table.
    """
    low = name.lower()
    if low in _SYSTEM_EXACT or low.endswith("flag"):
        return True
    # The text behind an "Other (specify)" punch: free text, for verbatim coding.
    # A string column of few distinct answers is otherwise inferred as a choice.
    if _OTHER_SPECIFY_RE.search(low):
        return True
    if low.startswith(_ALWAYS_JUNK_PREFIXES + _STUB_ONLY_PREFIXES):
        return True
    return bool(_CAMEL_SYSTEM_RE.match(name))


def _is_paradata_variable(name: str) -> bool:
    """Excludes a variable from BANNER candidacy: only what's unambiguously
    junk regardless of naming convention (true system bookkeeping, device
    fingerprints). Deliberately narrower than _is_system_variable — a
    hid_/dummy_/tt_-prefixed recode can be an excellent banner even though
    it's skipped as a stub.
    """
    low = name.lower()
    if low in _SYSTEM_EXACT or low.endswith("flag"):
        return True
    if low.startswith(_ALWAYS_JUNK_PREFIXES):
        return True
    return bool(_CAMEL_SYSTEM_RE.match(name))


def _is_binary_flag(v) -> bool:
    """True for 0/1 (or 1/2) dummy variables with no real value labels.

    Multi-punch questions explode into one 0/1 sub-flag per option
    (C2br1, C2br2, …). These have exactly two codes and labels that are just
    the codes as strings ("0", "1") — meaningless as a banner ("0 (A) 1 (B)").
    A real demographic banner has descriptive labels ("Male"/"Female"), so
    require at least one non-numeric label to qualify.
    """
    labels = list(v.values.values())
    return all(str(label).strip().lstrip("-").isdigit() for label in labels if label != "")


MAX_BANNER_LABEL_LEN = 30


def suggest_banner_candidates(datamap: DataMap, limit: int = 3) -> list[str]:
    """Variables that WOULD make a good banner, for a human to choose from.

    Not auto-applied anywhere — a file-order guess reliably surfaces
    operational/QA variables (device type, OS, bot-detection flags) ahead of
    real demographics simply because they appear earlier in the export, and
    forcing an unrelated default banner onto every question the analyst asks
    about is confusing (e.g. a Country crosstab getting Vehicle-Purchase-
    Intent columns stapled on because that's what happened to auto-select).
    Exposed for a future "pick your banner" UI/API rather than guessed here.

    Candidacy is drawn from a wider pool than stub candidacy (see
    _is_paradata_variable): hand-recoded demographics — often the cleanest
    banners in the file — commonly carry a hid_/dummy_/tt_ prefix in
    Decipher-style exports, the same prefix true system paradata uses, so
    the prefix alone can't gate this; label quality (length, non-numeric)
    does instead. Binary 0/1 multi-punch flags are excluded ("0 (A) 1 (B)"
    is meaningless as a banner) regardless of prefix.
    """
    candidates = [
        v
        for v in datamap.variables
        if v.type == VarType.SINGLE
        and v.values
        and len(v.valid_codes()) >= 2
        and not _is_paradata_variable(v.name)
        and 2 <= len(v.valid_codes()) <= 6
        and max(len(label) for label in v.values.values()) <= MAX_BANNER_LABEL_LEN
        and not _is_binary_flag(v)
    ]
    return [v.name for v in candidates][:limit]


def auto_tab_plan(datamap: DataMap, title: str = "Topline Tables") -> TabPlan | None:
    """Build a default tab plan from the datamap when the client sent no tab spec.

    No default banner: every stub table is Total-only unless a banner is
    explicitly given (a manual tab_plan.yaml, or the chat navigator's
    show_table specifying one because the analyst's question named a
    comparison group). See suggest_banner_candidates for surfacing good
    banner options to a human rather than silently guessing one.

    Stubs: every question variable with 2+ codes, with auto T2B/B2B nets and
    means on 5+ point scales. System/paradata columns excluded.
    """
    singles = [
        v
        for v in datamap.variables
        if v.type == VarType.SINGLE
        and v.values
        and len(v.valid_codes()) >= 2
        and not _is_system_variable(v.name)
    ]
    if not singles:
        return None

    tables = [
        TableSpec(stub=v.name, nets=auto_nets(v), mean=len(v.valid_codes()) >= 5)
        for v in singles[:MAX_AUTO_TABLES]
    ]
    return TabPlan(title=title, banner=[], tables=tables)


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
        on_stage: Callable[[str], None] | None = None,
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
            on_stage: Called with a short stage name as each phase begins.
                A long pipeline is indistinguishable from a dead worker
                without it: the caller uses it to record progress so a run
                that is merely slow is not reported as failed, and so a run
                that really did die can be told apart from one still going.

        Returns:
            StudyRun with all paths populated
        """
        run_id = f"study_{datetime.now().strftime('%Y%m%d')}_{uuid4().hex[:6]}"
        study_dir = self.work_dir / run_id
        study_dir.mkdir(parents=True, exist_ok=True)

        # 1. CLASSIFY
        if on_stage:
            on_stage("classifying")
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
        if on_stage:
            on_stage("loading data")
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
        if on_stage:
            on_stage("quality check")
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
        if on_stage:
            on_stage("coding verbatims")
        from src.core.models import VarType
        open_ends = [v for v in datamap.variables if v.type == VarType.TEXT]
        if open_ends:
            logger.info(f"[{run_id}] Found {len(open_ends)} open-end questions; auto-coding...")
            coding_artifacts: dict[str, dict] = {}
            for var in open_ends:
                try:
                    coding_run = run_coding_job(
                        df,
                        var.name,
                        "respondent_id",
                        var.label,
                        min_confidence=min_coding_confidence,
                    )
                    # batch_code_verbatims adds GENERIC column names (codes,
                    # coding_confidence, ...) — with 2+ open-ends the second
                    # run would silently overwrite the first's results, so
                    # rename them per source variable immediately.
                    df = coding_run.df_coded.rename(
                        {
                            "codes": f"{var.name}_codes",
                            "coding_confidence": f"{var.name}_coding_confidence",
                            "coding_reasoning": f"{var.name}_coding_reasoning",
                            "needs_coding_review": f"{var.name}_needs_coding_review",
                        }
                    )
                    coding_artifacts[var.name] = coding_run_to_artifact(coding_run)
                    logger.info(
                        f"[{run_id}] {var.name}: {coding_run.n_auto_coded} auto-coded, "
                        f"{coding_run.n_needs_review} need review"
                    )
                except Exception as e:
                    logger.error(f"[{run_id}] Coding failed for {var.name}: {e}")

            if coding_artifacts:
                # Re-persist the parquet WITH the coded columns — it was first
                # written pre-coding, and tables/navigator read from the file,
                # not this in-memory frame. Without this rewrite every coded
                # column silently vanished at the end of the run (the coding
                # engine's output never reached a single downstream consumer).
                df.write_parquet(str(data_parquet_path))
                # Codeframes + review queues persist beside the deck so the
                # review-queue API/UI can serve them without re-running the LLM.
                study.coding_path = study_dir / "coding.json"
                study.coding_path.write_text(json.dumps(coding_artifacts, indent=1))

            study.stage = StudyStage.CODING_DONE

        # 5. CROSSTABS & RENDERING
        if on_stage:
            on_stage("tables and decks")
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

    def retab_with_survey_labels(
        self,
        run_dir: str | Path,
        client_name: str,
        survey,
        excel_filename: str | None = None,
        pptx_filename: str | None = None,
    ) -> dict:
        """Re-run tables/decks for a completed study with real survey labels.

        The questionnaire→data label merge (§3.2's datamap-merge fix): reload
        the persisted canonical pair from the run dir, merge the approved
        SurveyDefinition's question/option wording onto the inferred variables
        (label_merge — deterministic, conservative), then regenerate
        crosstabs.json and both decks in place. Deck filenames default to the
        original ones so the study's DB paths stay valid.

        Returns a summary dict (counts from the merge + whether tables were
        regenerated) for logging/API responses.
        """
        from src.engines.questionnaire.label_merge import (
            merge_labels_into_datamap,
            remap_data_codes,
        )

        run_dir = Path(run_dir)
        datamap_path = run_dir / "datamap.json"
        parquet_path = run_dir / "data.parquet"
        if not datamap_path.exists() or not parquet_path.exists():
            raise FileNotFoundError(
                f"run dir {run_dir} missing datamap.json/data.parquet "
                "(study predates artifact persistence)"
            )

        datamap = DataMap.from_json(datamap_path.read_text())
        merge = merge_labels_into_datamap(survey, datamap)
        # The data moves with the data map or the two disagree: a label-encoded
        # export's codes are ours, not the script's, and the tables have to be
        # cut on the questionnaire's numbering to mean anything.
        n_columns_remapped = remap_data_codes(parquet_path, merge.code_remaps)
        # Persist the enriched datamap so the navigator/API see real labels too.
        datamap_path.write_text(datamap.to_json())

        summary = {
            "n_variables_relabelled": merge.n_variables_relabelled,
            "n_value_labels_applied": merge.n_value_labels_applied,
            "n_codes_aligned": merge.n_codes_aligned,
            "n_columns_remapped": n_columns_remapped,
            "skipped_code_mismatch": merge.skipped_code_mismatch,
            "unmatched_questions": merge.unmatched_questions,
            "tables_regenerated": False,
        }
        if merge.n_variables_relabelled == 0 and n_columns_remapped == 0:
            return summary  # nothing changed; don't churn the decks

        tab_plan = auto_tab_plan(datamap, title=f"{client_name} — Topline Tables")
        if not tab_plan:
            return summary

        deck_run = run_tab_plan(str(parquet_path), tab_plan, datamap)
        deck_to_json(deck_run, run_dir / "crosstabs.json")
        render_excel(deck_run, str(run_dir / (excel_filename or f"{client_name}_deck.xlsx")))
        render_pptx(deck_run, str(run_dir / (pptx_filename or f"{client_name}_deck.pptx")))
        summary["tables_regenerated"] = True
        logger.info(
            f"[retab {run_dir.name}] {merge.n_variables_relabelled} variables relabelled; "
            "tables and decks regenerated with survey labels"
        )
        return summary

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

        # Respondent-quality checks (speeders / straightliners / duplicates /
        # gibberish open-ends) — the cleaning layer DP shops and the incumbent
        # platforms run before tables. Warnings, not hard issues: dropping a
        # respondent is a human judgement (the delivery gate exists for this),
        # so QC surfaces them without failing the run. Full per-respondent
        # detail rides in metadata for a future data-quality review UI.
        from src.engines.qc.respondents import run_respondent_qc, summarize_for_report

        respondent_qc = run_respondent_qc(df, datamap)
        warnings.extend(summarize_for_report(respondent_qc, len(df)))
        metadata["respondent_qc"] = {
            "checks_run": respondent_qc.checks_run,
            "checks_skipped": respondent_qc.checks_skipped,
            "n_flagged_respondents": len(respondent_qc.flagged_rows()),
            "speeders": respondent_qc.speeders,
            "straightliners": respondent_qc.straightliners,
            "duplicates": respondent_qc.duplicates,
            "gibberish_open_ends": respondent_qc.gibberish_open_ends,
            "duration_column": respondent_qc.duration_column,
            "grids_checked": respondent_qc.grids_checked,
        }

        passed = len(issues) == 0
        return QCReport(passed=passed, issues=issues, warnings=warnings, metadata=metadata)

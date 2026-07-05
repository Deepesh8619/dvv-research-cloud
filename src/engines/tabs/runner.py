"""Run a full tab plan against a study: spec in, computed tables out."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from src.core.models import DataMap
from src.engines.tabs.crosstab import CrosstabResult, crosstab
from src.engines.tabs.spec import TabPlan


@dataclass
class DeckRun:
    plan: TabPlan
    results: list[CrosstabResult]


def table_to_dict(r: CrosstabResult, table_id: str) -> dict:
    """Serialize one computed table to the JSON shape all viewers share.

    Used by deck_to_json (crosstabs.json for the Tables tab) and by the
    study navigator's show_table tool — one shape, every renderer.
    """
    return {
        "id": table_id,
        "stub": r.stub,
        "title": r.stub_label,
        "filter": r.filter_desc,
        "columns": [
            {
                "label": c.label,
                "letter": c.letter,
                "base": round(c.base, 1),
                "unweighted_base": c.unweighted_base,
                "effective_base": round(c.effective_base, 1),
            }
            for c in r.columns
        ],
        "rows": [
            {
                "label": r.row_labels[i],
                "pct": [round(float(r.col_pct[i, j]), 1) for j in range(len(r.columns))],
                "sig": list(r.sig[i]) if r.sig else [""] * len(r.columns),
                "kind": "category",
            }
            for i in range(len(r.row_labels))
        ]
        + [
            {
                "label": r.net_labels[g],
                "pct": [round(float(r.net_pct[g, j]), 1) for j in range(len(r.columns))],
                "sig": list(r.net_sig[g]) if r.net_sig else [""] * len(r.columns),
                "kind": "net",
            }
            for g in range(len(r.net_labels))
            if r.net_pct is not None
        ],
        "means": ([round(float(m), 2) for m in r.means] if r.means is not None else None),
        "mean_sig": list(r.mean_sig) if r.mean_sig else None,
    }


def deck_to_json(run: DeckRun, path: str | Path) -> Path:
    """Serialize computed tables to JSON so viewers (web UI) can render them."""
    tables = [table_to_dict(r, f"t{idx}") for idx, r in enumerate(run.results, start=1)]

    payload = {
        "title": run.plan.title,
        "weight": run.plan.weight,
        "confidence": run.plan.confidence,
        "min_base_for_test": run.plan.min_base_for_test,
        "tables": tables,
    }
    out = Path(path)
    out.write_text(json.dumps(payload, ensure_ascii=False))
    return out


def run_tab_plan(parquet_path: str | Path, plan: TabPlan, datamap: DataMap) -> DeckRun:
    results = []
    for table in plan.tables:
        results.append(
            crosstab(
                parquet_path,
                stub=table.stub,
                banner=plan.banner,
                datamap=datamap,
                weight=plan.weight,
                confidence=plan.confidence,
                min_base_for_test=plan.min_base_for_test,
                nets=table.nets or None,
                show_mean=table.mean,
                filter_=table.filter,
            )
        )
    return DeckRun(plan=plan, results=results)

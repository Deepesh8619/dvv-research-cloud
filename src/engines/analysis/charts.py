"""Chart rendering: reuse the crosstab engine for numbers, matplotlib for pixels.

The LLM never touches pixel data or percentages directly — it asks for a chart
by variable name, this engine computes the crosstab (same deterministic path
as show_table) and renders it. Agg backend only: this runs in a request
handler, never a GUI context, and must never try to open a display.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # must precede pyplot import — no display in a server process
import matplotlib.pyplot as plt

from src.core.models import DataMap
from src.engines.tabs.crosstab import crosstab
from src.engines.tabs.runner import table_to_dict
from src.engines.tabs.spec import auto_nets

CHART_TYPES = ("bar", "line", "pie", "heatmap")


def generate_chart(
    study_dir: Path,
    datamap: DataMap,
    stub: str,
    banner: list[str] | None = None,
    chart_type: str = "bar",
    filter_: dict[str, list[int]] | None = None,
) -> dict:
    """Compute a crosstab and render it as a PNG under {study_dir}/charts/.

    Returns a dict with chart_path (relative to study_dir), chart_type, title,
    and the underlying table data so the frontend can render both the image
    and a fallback/accessible table.
    """
    if chart_type not in CHART_TYPES:
        return {
            "success": False,
            "error": f"Unknown chart_type '{chart_type}'; must be one of {CHART_TYPES}",
        }
    if stub not in datamap:
        return {"success": False, "error": f"Unknown variable: {stub}"}

    banner = banner or []
    bad = [b for b in banner if b not in datamap]
    if bad:
        return {"success": False, "error": f"Unknown banner variable(s): {bad}"}

    parquet = study_dir / "data.parquet"
    if not parquet.exists():
        return {"success": False, "error": "Respondent data not available for this study"}

    try:
        result = crosstab(
            parquet,
            stub=stub,
            banner=banner,
            datamap=datamap,
            nets=auto_nets(datamap[stub]) or None,
            filter_=filter_,
        )
    except Exception as exc:
        return {"success": False, "error": str(exc)}

    table = table_to_dict(result, "chart")
    title = table["title"]

    charts_dir = study_dir / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.png"
    out_path = charts_dir / filename

    try:
        _render(table, chart_type, title, out_path)
    except Exception as exc:
        return {"success": False, "error": f"Chart rendering failed: {exc}"}

    return {
        "success": True,
        "chart_path": str(out_path),
        "chart_filename": filename,
        "chart_type": chart_type,
        "title": title,
        "table": table,
    }


def _render(table: dict, chart_type: str, title: str, out_path: Path) -> None:
    """Draw `table` (the show_table JSON shape) as `chart_type` to out_path."""
    columns = [c["label"] for c in table["columns"]]
    category_rows = [r for r in table["rows"] if r["kind"] == "category"]
    row_labels = [r["label"] for r in category_rows]
    # rows x columns percentage matrix
    data = [r["pct"] for r in category_rows]

    fig, ax = plt.subplots(figsize=(8, 5))

    if chart_type == "bar":
        _draw_grouped_bar(ax, row_labels, columns, data)
    elif chart_type == "line":
        _draw_line(ax, row_labels, columns, data)
    elif chart_type == "pie":
        _draw_pie(ax, row_labels, data)
    elif chart_type == "heatmap":
        _draw_heatmap(fig, ax, row_labels, columns, data)

    ax.set_title(title)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def _draw_grouped_bar(ax, row_labels, columns, data) -> None:
    import numpy as np

    n_rows, n_cols = len(row_labels), len(columns)
    x = np.arange(n_rows)
    width = 0.8 / max(n_cols, 1)
    for j, col_label in enumerate(columns):
        values = [data[i][j] for i in range(n_rows)]
        ax.bar(x + j * width, values, width, label=col_label)
    ax.set_xticks(x + width * (n_cols - 1) / 2)
    ax.set_xticklabels(row_labels, rotation=30, ha="right")
    ax.set_ylabel("% of respondents")
    ax.legend(fontsize="small")


def _draw_line(ax, row_labels, columns, data) -> None:
    for j, col_label in enumerate(columns):
        values = [data[i][j] for i in range(len(row_labels))]
        ax.plot(row_labels, values, marker="o", label=col_label)
    ax.set_ylabel("% of respondents")
    ax.tick_params(axis="x", rotation=30)
    ax.legend(fontsize="small")


def _draw_pie(ax, row_labels, data) -> None:
    # Pie only makes sense for a single series — use the Total column (index 0)
    values = [row[0] for row in data]
    ax.pie(values, labels=row_labels, autopct="%1.1f%%")
    ax.axis("equal")


def _draw_heatmap(fig, ax, row_labels, columns, data) -> None:
    import numpy as np

    matrix = np.array(data)
    im = ax.imshow(matrix, cmap="YlOrRd", aspect="auto")
    ax.set_xticks(range(len(columns)))
    ax.set_xticklabels(columns, rotation=30, ha="right")
    ax.set_yticks(range(len(row_labels)))
    ax.set_yticklabels(row_labels)
    for i in range(matrix.shape[0]):
        for j in range(matrix.shape[1]):
            ax.text(j, i, f"{matrix[i, j]:.0f}", ha="center", va="center", fontsize=8)
    fig.colorbar(im, ax=ax, label="% of respondents")

"""PowerPoint deck renderer: tables + charts + AI narratives per slide.

Each table becomes a slide with: chart (column %), table grid (codes + %s + sig letters),
and AI-generated narrative ("What changed and why").
"""

from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt

from src.ai.router import run_task
from src.engines.tabs.crosstab import CrosstabResult
from src.engines.tabs.runner import DeckRun

NARRATIVE_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string", "description": "One-line headline"},
        "insight": {
            "type": "string",
            "description": "2-3 sentence insight: what changed, direction, magnitude",
        },
    },
    "required": ["headline", "insight"],
}

NARRATIVE_PROMPT = (
    "You are a market research analyst. Write one-line headline and insight.\n"
    "Question: {stub_label}\nTable: {table_text}\n"
    "Sig letters (A/B/C = higher than those columns): {sig_text}\n"
    "What's surprising? What moved? Return only JSON."
)


def generate_narrative(result: CrosstabResult, timeout: int = 60) -> tuple[str, str]:
    """Generate a headline and insight for one table via Claude."""
    table_lines = []
    for i, label in enumerate(result.row_labels):
        pcts = " | ".join(f"{result.col_pct[i, j]:.0f}%" for j in range(len(result.columns)))
        table_lines.append(f"{label}: {pcts}")

    sig_lines = []
    for i, label in enumerate(result.row_labels):
        sigs = " | ".join(result.sig[i])
        sig_lines.append(f"{label}: {sigs}")

    prompt = NARRATIVE_PROMPT.format(
        stub_label=result.stub_label,
        table_text="\n".join(table_lines),
        sig_text="\n".join(sig_lines),
    )
    try:
        response = run_task("report.narrative", prompt, NARRATIVE_SCHEMA, timeout=timeout)
        return response.content["headline"], response.content["insight"]
    except Exception:
        return "No headline", "Analysis pending"


def render_deck(run: DeckRun, path: str | Path) -> Path:
    """Write the deck to .pptx and return the path."""
    prs = Presentation()
    prs.slide_width = Inches(10)
    prs.slide_height = Inches(7.5)

    # Title slide
    title_slide_layout = prs.slide_layouts[6]  # blank
    slide = prs.slides.add_slide(title_slide_layout)
    left = Inches(1)
    top = Inches(2.5)
    width = Inches(8)
    height = Inches(2)
    txBox = slide.shapes.add_textbox(left, top, width, height)
    tf = txBox.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = run.plan.title
    p.font.size = Pt(54)
    p.font.bold = True
    p.font.color.rgb = RGBColor(31, 42, 68)
    p.alignment = PP_ALIGN.CENTER

    # One slide per table
    for idx, result in enumerate(run.results, start=1):
        slide = prs.slides.add_slide(prs.slide_layouts[6])

        # Headline
        headline, insight = generate_narrative(result)
        left = Inches(0.5)
        top = Inches(0.3)
        width = Inches(9)
        height = Inches(0.6)
        title_box = slide.shapes.add_textbox(left, top, width, height)
        tf = title_box.text_frame
        p = tf.paragraphs[0]
        p.text = f"T{idx}. {result.stub_label}: {headline}"
        p.font.size = Pt(24)
        p.font.bold = True
        p.font.color.rgb = RGBColor(31, 42, 68)

        # Insight
        left = Inches(0.5)
        top = Inches(1.0)
        width = Inches(9)
        height = Inches(1.2)
        insight_box = slide.shapes.add_textbox(left, top, width, height)
        tf = insight_box.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = insight
        p.font.size = Pt(14)
        p.font.color.rgb = RGBColor(50, 50, 50)

        # Table: column headers + data
        rows, cols = len(result.row_codes) + 3, len(result.columns) + 1  # +3 for base rows
        left = Inches(0.5)
        top = Inches(2.3)
        width = Inches(9)
        height = Inches(4.7)
        table_shape = slide.shapes.add_table(rows, cols, left, top, width, height).table

        # Header
        for j, col in enumerate(result.columns):
            cell = table_shape.cell(0, j + 1)
            cell.text = col.label
            cell.fill.solid()
            cell.fill.fore_color.rgb = RGBColor(31, 42, 68)
            for paragraph in cell.text_frame.paragraphs:
                for run in paragraph.runs:
                    run.font.color.rgb = RGBColor(255, 255, 255)
                    run.font.bold = True
                    run.font.size = Pt(10)

        # Bases
        for j, col in enumerate(result.columns):
            cell = table_shape.cell(1, j + 1)
            cell.text = f"n={int(col.base)}"
            for paragraph in cell.text_frame.paragraphs:
                for run in paragraph.runs:
                    run.font.size = Pt(9)

        # Data rows
        for i, label in enumerate(result.row_labels):
            row_idx = i + 3
            cell = table_shape.cell(row_idx, 0)
            cell.text = label
            for p in cell.text_frame.paragraphs:
                for run in p.runs:
                    run.font.size = Pt(10)
            for j in range(len(result.columns)):
                cell = table_shape.cell(row_idx, j + 1)
                pct = f"{result.col_pct[i, j]:.1f}%"
                letters = result.sig[i][j]
                cell.text = f"{pct} {letters}" if letters else pct
                for p in cell.text_frame.paragraphs:
                    for run in p.runs:
                        run.font.size = Pt(9)

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(out))
    return out

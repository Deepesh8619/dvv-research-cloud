"""PowerPoint deck renderer: key findings, then one slide per table.

Every headline is a finding computed from the tables (`analysis.findings`),
with its table and base on the slide. A model's headline is used only when
every number in it is a cell, base or mean of that table; otherwise the
computed finding stands. The deck reads the same with no model reachable.
"""

from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt

from src.ai.router import run_task
from src.engines.analysis.findings import checked_row, findings, unverified_numbers
from src.engines.tabs.crosstab import CrosstabResult
from src.engines.tabs.runner import DeckRun, table_to_dict

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


def generate_narrative(
    result: CrosstabResult, table: dict, timeout: int = 60
) -> tuple[str, str] | None:
    """A model's headline and insight for one table, or None if any number in it
    is not in the table (or no model answered)."""
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
        headline, insight = response.content["headline"], response.content["insight"]
    except Exception:
        return None
    return None if unverified_numbers(f"{headline} {insight}", [table]) else (headline, insight)


def _text(slide, text: str, top: float, height: float, size: int, bold: bool = False,
          color: tuple[int, int, int] = (50, 50, 50)) -> None:
    box = slide.shapes.add_textbox(Inches(0.5), Inches(top), Inches(9), Inches(height))
    box.text_frame.word_wrap = True
    p = box.text_frame.paragraphs[0]
    p.text = text
    p.font.size, p.font.bold, p.font.color.rgb = Pt(size), bold, RGBColor(*color)


def _cited(f: dict) -> str:
    """'… 36% … 15% …  [T11: Checked n=52, Unchecked n=39]' — where each number comes from."""
    by_table: dict[str, dict[str, int]] = {}
    for c in f["cites"]:
        by_table.setdefault(c["table"].upper(), {})[c["column"]] = c["base"]
    return f"{f['text']}  [" + "; ".join(
        f"{t}: " + ", ".join(f"{col} n={n}" for col, n in cols.items())
        for t, cols in by_table.items()) + "]"


def render_deck(run: DeckRun, path: str | Path, questions: dict[str, str] | None = None,
                screeners: set[str] | None = None, narrate: bool = True) -> Path:
    """Write the deck to .pptx and return the path.

    `questions` (qid -> wording) and `screeners` come from the questionnaire
    when there is one; without them findings still read, less well named.
    `narrate=False` skips the model entirely (computed findings only).
    """
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

    confidence = run.plan.confidence  # `run` is reused below for text runs
    tables = [table_to_dict(r, f"t{idx}") for idx, r in enumerate(run.results, start=1)]
    found = findings(tables, questions, screeners)
    by_table: dict[str, dict] = {}
    for f in found:
        for tid in f["tables"]:
            by_table.setdefault(tid, f)

    key = [f for f in found if f["kind"] != "sample"][:6]
    if key:
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        _text(slide, "Key findings", 0.3, 0.6, 28, bold=True, color=(31, 42, 68))
        box = slide.shapes.add_textbox(Inches(0.5), Inches(1.1), Inches(9), Inches(5.6))
        box.text_frame.word_wrap = True
        for n, f in enumerate(key):
            p = box.text_frame.paragraphs[0] if n == 0 else box.text_frame.add_paragraph()
            p.text = _cited(f)
            p.font.size = Pt(13)
            p.space_after = Pt(10)
        _text(slide, "Every number is read from the table named in brackets; n is its "
              "unweighted base.", 6.9, 0.4, 10)

    # One slide per table
    for idx, (result, table) in enumerate(zip(list(run.results), tables, strict=True), start=1):
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        f = by_table.get(table["id"])
        told = generate_narrative(result, table) if narrate else None
        if told:
            headline, insight = told
        else:
            headline = result.stub_label
            insight = (f["text"] + (f" {f['caution']}" if f.get("caution") else "")) if f else ""
            if f and f["kind"] == "most_chosen" and (row := checked_row(table)):
                # One option of a multi-select: its own share, not the question's summary.
                insight = f"Chosen by {row['pct'][0]:.0f}% of respondents."
        _text(slide, f"T{idx}. {headline}", 0.3, 0.6, 22, bold=True, color=(31, 42, 68))
        _text(slide, insight, 1.0, 1.2, 14)
        total = result.columns[0].unweighted_base if result.columns else 0
        _text(slide, f"Source: T{idx}, base n={total}"
              + (f" ({result.filter_desc})" if result.filter_desc else "")
              + ". Letters: higher than that column at "
              f"{confidence:.0%} confidence.", 7.05, 0.35, 9)

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

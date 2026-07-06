# Part 3: Output Rendering (Excel & PowerPoint Decks)

**Location:** `src/renderers/excel.py` · `src/renderers/pptx.py`

## Purpose

Convert computed crosstabs into pretty, professional Excel and PowerPoint reports that clients can download and present.

## When It Runs

**Batch mode:** During orchestration, after all crosstabs are computed.

```
[Crosstab JSON] → Excel Renderer → deck.xlsx
              → PPT Renderer → deck.pptx
```

Users can download from "Reports" tab in the web UI.

## Excel Rendering

### Structure

```
Workbook
├── Summary sheet
│   ├── Study metadata (respondents, confidence, weighting)
│   └── High-level findings
├── Table of Contents
│   └── Links to each table
├── Table 1 sheet
│   ├── Title + description
│   ├── Crosstab (formatted table)
│   └── Notes (low-base warnings, filtering)
├── Table 2 sheet
│   ...
└── Table N sheet
```

### Formatting

**Headers:**
- Bold, centered, light gray background
- Significance legend ("(A) = significantly higher than column A")

**Data cells:**
- Percentages centered, right-aligned
- Significance letters as superscripts (e.g., 64.1%^A)
- Low-base cells marked with * (n < 30)

**Columns:**
- Stub (left): Question/category name
- Banner columns: Status categories
- Total: Overall result

### Example

```
                        Total    Yes (A)   No (B)   New (C)
Satisfaction (5pt)
  Very Satisfied         45.2      52.1*     38.4      47.8*
  Satisfied              38.1      35.7      42.3      39.2
  Neutral                12.4       8.2      15.1      10.6
  Dissatisfied            4.3       4.0       4.2       2.4

Bases (unweighted):    2,987    1,924     1,063       1,200
Bases (weighted):      3,100    2,015     1,085       1,285
```

### Code

```python
# src/renderers/excel.py
def render_deck(deck_run: DeckRun, output_path: str):
    """Convert crosstabs to Excel workbook."""
    workbook = openpyxl.Workbook()
    
    # Summary sheet
    add_summary_sheet(workbook, deck_run)
    
    # TOC
    add_toc_sheet(workbook, deck_run)
    
    # One sheet per crosstab
    for table in deck_run.tables:
        add_table_sheet(workbook, table)
    
    workbook.save(output_path)
```

## PowerPoint Rendering

### Structure

```
Presentation
├── Title slide (study name, date)
├── Slide 1: Table + AI narrative
├── Slide 2: Table + AI narrative
│   ...
└── Slide N: Table + AI narrative
```

### Formatting

**Each slide:**
- Title: Table name ("Satisfaction by Gender")
- Content: Native PPT table (not an image) — fully editable in PowerPoint
- Notes: AI-generated narrative ("The data shows that females are significantly more satisfied...")

**Tables:**
- Same significance letters and formatting as Excel
- Native PPT TableShape (clients can edit in PowerPoint)
- Row/column sizing optimized for readability

### AI Narrative Generation

After rendering the table, Claude generates a 2–3 sentence summary:

```python
# src/renderers/pptx.py
ai_narrative = generate_narrative(crosstab)
# "Satisfaction shows strong variation by gender. 
#  Females report 52% very satisfied vs. 48% males (A). 
#  This difference is statistically significant at 95% confidence."
```

Uses Claude Fable-5 (fast, cheap, sufficient for summarization).

### Code

```python
# src/renderers/pptx.py
def render_deck(deck_run: DeckRun, output_path: str):
    """Convert crosstabs to PowerPoint presentation."""
    prs = pptx.Presentation()
    
    # Title slide
    add_title_slide(prs, deck_run.study_name)
    
    # One slide per crosstab
    for table in deck_run.tables:
        slide = add_table_slide(prs, table)
        narrative = generate_narrative(table)  # Claude Fable-5
        add_notes_to_slide(slide, narrative)
    
    prs.save(output_path)
```

## Key Properties

| Aspect | Value |
|--------|-------|
| Input | Computed crosstabs (JSON) |
| Output | Excel (.xlsx) + PowerPoint (.pptx) |
| Deterministic | ✅ Yes — same crosstabs = same deck |
| Costs | LLM tokens for AI narratives only (cheap, Fable-5) |
| Editable | ✅ Yes — clients can edit tables in Excel/PPT |
| Reproducible | ✅ Yes — run again → identical deck (except AI narrative might vary) |

## Why Graphs Aren't Generated Here

The renderers only know how to:
- Format tables (borders, colors, alignment)
- Add text (titles, narratives)

They don't know how to:
- Render plots/charts
- Embed images

To add graphs, you'd need:
1. **Chart generation engine** (matplotlib/Plotly)
2. **Export to image** (PNG/SVG)
3. **Embed in Excel/PPT** (openpyxl images, python-pptx picture shapes)
4. **Update renderers** to call the new chart engine

Estimated effort: 4–6 hours for basic bar/line/pie charts.

## Workflow Example

```
Study upload
  ↓
auto_tab_plan() → crosstabs.json
  ↓
run_tab_plan() → computed crosstabs (JSON)
  ↓
render_excel() → study_Client_deck.xlsx
render_pptx()  → study_Client_deck.pptx
  ↓
Client downloads from "Reports" tab
```

## Debugging

If decks look wrong:
1. Check crosstab JSON — is significance testing correct?
2. Check formatting rules — are headers bold, cells centered?
3. Check AI narratives — are they generic/wrong?

Run a single table through the renderer in isolation:
```python
# Debug rendering
from src.engines.tabs.runner import DeckRun
from src.renderers.excel import render_deck

deck_run = load_cached_deck(study_id)
render_deck(deck_run, "debug.xlsx")
```

## Next Steps

Once Excel/PPT decks are delivered to clients, they can:
- Download and share with stakeholders
- Edit tables to customize analysis
- Present live from PowerPoint
- Print to PDF for distribution

---

**Related:** [[02-Crosstab-Engine]] · [[07-Code-Locations]] · [[06-Current-Status]]

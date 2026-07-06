# Why "Plot Graph for the Same" Failed

**The question:** "plot graph for the same" (presumably for the crosstab tables you were seeing)  
**The result:** "_(basic local mode)_" + weak answer  
**The reason:** Three system layers don't support it

## Layer 1: Auto-Analysis Doesn't Generate Graph Specs

[[01-Auto-Analysis]] produces **crosstab specifications only** — not graph specifications.

```
auto_tab_plan() output:
{
  "banner": ["Gender", "Region"],
  "tables": [
    TableSpec(stub="Q1", nets=True, mean=True),  // Table spec
    // ^ No "plots" or "charts" key
  ]
}
```

If graph specs were needed, you'd need:

```python
GraphSpec(
  stub="Q1",
  banner=["Gender"],
  chart_type="bar|line|pie",
  aggregate="count|percent|mean",
)
```

This doesn't exist. Auto-analysis is purely table-oriented.

## Layer 2: Crosstab Engine Only Computes Numbers

[[02-Crosstab-Engine]] is a pure **math engine**:

- Input: Respondent data
- Process: SQL grouping + percentages + z-tests
- Output: Numbers in rows/columns

```python
# src/engines/tabs/runner.py:crosstab()
def crosstab(parquet_path, stub, banner, datamap):
    # Returns: CrosstabTable
    # {
    #   "stub": "Q1",
    #   "banner": ["Gender"],
    #   "rows": [{...cells...}],
    #   "bases": [n1, n2, n3],
    # }
```

It does NOT generate images. To generate graphs, you'd need:

```python
def plot_crosstab(crosstab: CrosstabTable, chart_type: str) -> bytes:
    """Generate PNG from crosstab numbers."""
    import matplotlib.pyplot as plt
    
    fig, ax = plt.subplots()
    # Extract numbers from crosstab
    # Draw bars/lines/pie
    # Save to PNG
    # Return image bytes
```

This doesn't exist.

## Layer 3: Renderers Only Format Tables

[[03-Output-Rendering]] knows how to:
- Create Excel tables (openpyxl)
- Create PPT tables (python-pptx)
- Add text narratives (Claude)

They don't know how to:
- Generate charts
- Embed images in Excel/PPT
- Size/position plots

The renderers are **table-only**.

## Layer 4: Chat UI Can't Display Images

The web frontend (`web/components/...`) is built to render:
- Tables (native HTML `<table>`)
- Text (markdown + narratives)
- Links (navigate_to tool)

It does NOT render:
- PNG/SVG images
- Interactive charts
- Plotly/D3 visualizations

To add image support, you'd need:

```jsx
// web/components/ChatMessage.jsx
function ChatMessage({ message, tools }) {
  return (
    <>
      <Markdown content={message.content} />
      {tools?.map(tool => {
        if (tool.name === "show_table")
          return <CrosstabTable table={tool.result.table} />
        if (tool.name === "plot_data")
          return <Image src={tool.result.image_url} />  // NEW
      })}
    </>
  );
}
```

## Layer 5: Navigator Has No Graph Tool

[[04-Navigator-Fallback]] defines these tools:

```python
TOOLS = [
    { "name": "show_table", ... },
    { "name": "query_data", ... },
    { "name": "compare_tables", ... },
    { "name": "navigate_to", ... },
]
# No "plot_data" or "generate_chart" tool
```

When the user asks "plot graph", the navigator sees:
- Question contains word "plot"
- No tool named "plot_data"
- Can't map to existing tools

**Groq's behavior (if budget available):**
```
Navigator: "I don't have a tool to generate graphs. 
            I can show you the crosstab table instead. 
            Would you like me to compute Country × Status?"
```

**Ollama's behavior (current, weak model):**
```
Navigator: "_(basic local mode)_
            I cannot create plots from this data."
```

Ollama can't reason about "suggest an alternative," so it just says no.

## What Would Need to Be Built

To enable graph plotting:

### 1. Graph Generation Engine (1–2 hrs)

```python
# src/engines/plots/renderer.py
from matplotlib import pyplot as plt

def plot_crosstab(crosstab, plot_type="bar"):
    """
    Input: CrosstabTable from Part 2
    Output: PNG image bytes
    """
    fig, ax = plt.subplots(figsize=(10, 6))
    
    x = crosstab.row_labels
    colors = ["#1f77b4", "#ff7f0e", "#2ca02c"]
    
    for i, banner_col in enumerate(crosstab.banner):
        values = [cell.value for cell in crosstab.get_column(i)]
        ax.bar([j + i*0.25 for j in range(len(x))], values, label=banner_col)
    
    ax.set_ylabel("Percentage")
    ax.set_title(crosstab.title)
    ax.legend()
    
    # Save to bytes
    buffer = BytesIO()
    fig.savefig(buffer, format="png")
    return buffer.getvalue()
```

### 2. Navigator Tool (30 mins)

```python
TOOLS = [
    # ... existing tools ...
    {
        "name": "plot_data",
        "description": "Generate a bar/line/pie chart from a crosstab",
        "input_schema": {
            "type": "object",
            "properties": {
                "stub": {"type": "string"},
                "banner": {"type": "array", "items": {"type": "string"}},
                "plot_type": {"type": "string", "enum": ["bar", "line", "pie"]},
            },
            "required": ["stub", "plot_type"],
        },
    },
]
```

### 3. Tool Executor (30 mins)

```python
# api/routes/chat.py

def execute(name: str, params: dict) -> dict:
    if name == "plot_data":
        # Compute crosstab
        crosstab_result = crosstab(
            parquet=study_dir / "data.parquet",
            stub=params.get("stub"),
            banner=params.get("banner") or default_banner,
        )
        # Generate plot
        image_bytes = plot_crosstab(
            crosstab_result,
            plot_type=params.get("plot_type", "bar"),
        )
        # Save to temp file or base64
        image_url = upload_temp_image(image_bytes)
        return {
            "success": True,
            "image_url": image_url,
            "title": f"{params['stub']} by {', '.join(banner)}",
        }
```

### 4. Chat UI Enhancement (1–2 hrs)

```jsx
// web/components/ChatMessage.jsx

function renderToolResult(tool, result) {
  if (tool === "show_table") {
    return <CrosstabTable table={result.table} />
  }
  if (tool === "plot_data") {
    return (
      <figure>
        <img src={result.image_url} alt={result.title} />
        <figcaption>{result.title}</figcaption>
      </figure>
    )
  }
}
```

### 5. Regression Tests (30 mins)

```python
# tests/unit/test_plots.py

def test_plot_bar_chart():
    crosstab = create_test_crosstab()
    image = plot_crosstab(crosstab, "bar")
    assert image is not None
    assert len(image) > 0  # Valid PNG bytes
    
def test_plot_renders_all_categories():
    crosstab = create_test_crosstab(4_categories=True)
    image = plot_crosstab(crosstab, "bar")
    # Parse PNG, verify all categories present
```

## Total Effort

| Component | Time | Difficulty |
|-----------|------|------------|
| Plot engine | 1–2h | Easy (matplotlib is standard) |
| Navigator tool | 30m | Trivial |
| Executor | 30m | Trivial |
| UI rendering | 1–2h | Medium (image handling) |
| Tests | 30m | Easy |
| **Total** | **4–6h** | **Low–Medium** |

## Decision Point

This is **feature creep, not a blocker**:

- ✅ System works perfectly for **tables** (what clients want)
- ❌ Graphs are **nice-to-have** (future enhancement)
- ✅ [[04-Navigator-Fallback]] can suggest alternatives ("Here's the table instead")

**Recommendation:** Focus on fixing the Groq budget issue first (enable dev tier). Graphs can wait.

---

**Related:** [[02-Crosstab-Engine]] · [[03-Output-Rendering]] · [[04-Navigator-Fallback]] · [[06-Current-Status]]

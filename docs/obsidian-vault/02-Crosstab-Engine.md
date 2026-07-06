# Part 2: Crosstab Engine (DuckDB Computation)

**Location:** `src/engines/tabs/runner.py:crosstab()` + `stats.py`

## Purpose

Compute the actual numbers: percentages, significance tests, means. Everything deterministic, no LLM guessing.

## When It Runs

1. **Navigator mode (live):** When user asks "Show me X by Y" → engine computes on-the-fly
2. **Batch mode (pre-render):** During orchestration, compute all crosstabs in the deck once → store in JSON

## How It Works

### Step 1: User Question

```
"Show me Country from URL by Participant Status"
       ↓
Navigate translates to tool call:
show_table(stub="Country from URL", banner=["Participant Status"])
```

### Step 2: DuckDB Query

```sql
SELECT Country, Status, COUNT(*) as n
FROM data
GROUP BY Country, Status
```

Produces:
```
Country    Status       n
USA        Yes         1,234
USA        No            892
France     Yes           456
France     No            567
UK         Yes           234
UK         No             456
```

### Step 3: Compute Column Percentages

For each Status column:
- **Total in column:** Sum all n for that status
- **Row %:** n / column_total × 100

Example for "Yes" column (total=1,924):
```
USA         1,234 / 1,924 = 64.1%
France        456 / 1,924 = 23.7%
UK            234 / 1,924 = 12.2%
```

### Step 4: Significance Testing (Z-Test)

For each cell, test: **Is this column significantly higher/lower than baseline?**

- Null hypothesis: This column % = overall %
- Test: Two-tailed z-test at α = 0.05 (95% confidence)
- Result: 
  - p < 0.05 → Significant difference (mark with letter)
  - p ≥ 0.05 → No significant difference (no mark)

**Interpretation:** A (A) mark means "significantly higher than the baseline for this row."

### Step 5: Add Metadata

- **Base size:** Unweighted respondent count per column
- **RIM weighting:** If study applies rim weighting, use weighted base sizes
- **Low-base flag:** Mark cells with n < 30 (unreliable)
- **Means:** For numeric variables, compute average per column

### Step 6: Return JSON

```json
{
  "id": "live",
  "stub": "Country from URL",
  "banner": ["Participant Status"],
  "rows": [
    {
      "label": "USA",
      "cells": [
        {"value": 64.1, "base": 1234, "sig_letter": "A"},
        {"value": 23.7, "base": 456, "sig_letter": null},
        {"value": 12.2, "base": 234, "sig_letter": "B"}
      ]
    },
    ...
  ],
  "bases": [1924, 1315, 690],  // Column bases
}
```

## Key Properties

| Property | Value | Why |
|----------|-------|-----|
| Deterministic | ✅ Yes | Same data + same params = same result always |
| Cost | <0.5s | DuckDB is blazing fast even on 1M+ rows |
| Costs tokens | ❌ No | Pure SQL + scipy stats, no LLM |
| Reproducible | ✅ Yes | No randomness, no floating-point variance |
| Grounded | ✅ Yes | Numbers come from respondent-level data, not hallucination |

## Z-Test Formula

```
z = (p1 - p0) / sqrt(p0 * (1 - p0) / n)

Where:
  p1 = column percentage (e.g., 64.1% = 0.641)
  p0 = baseline percentage (e.g., 40% = 0.4)
  n = column base (e.g., 1,924)
  
If |z| > 1.96 → p < 0.05 → Significant at 95% confidence
```

## Example Output

Your screenshot showed:
```
                Total    Yes (A)   No (B)   New (C)  Used (D)  New (E)
USA             100.0    100.0     
France            0.0      0.0
UK                0.0      0.0
```

This means:
- All respondents for "USA" answered "Yes" (100.0%)
- "Yes" column shows (A) because it's significantly different from the overall %
- France/UK had 0 respondents, so no crosstab cells

## Why This Works

1. **No ambiguity:** Math is math — everyone agrees on the result
2. **Fast:** DuckDB computes millions of cells in <1s
3. **Trustworthy:** Numbers come from raw data, not LLM paraphrase
4. **Repeatable:** Run it again tomorrow with same data → identical result

## Why Graphs Aren't Generated Here

The crosstab engine is *tabular only*:
- Input: Raw respondent data
- Output: Numbers in rows/columns

Graphs require:
- **Visualization logic:** matplotlib/plotly library integration
- **Decision logic:** Which chart type? (bar, line, pie?)
- **Image generation:** Render + save PNG/SVG
- **Serialization:** Embed in chat or export to image format

None of this is in the crosstab engine. It's pure number-crunching.

## Next Steps → Part 3

Once numbers are computed, [[03-Output-Rendering]] formats them into Excel/PPT.

---

**Related:** [[01-Auto-Analysis]] · [[03-Output-Rendering]] · [[07-Code-Locations]] · [[09-Context-Trimming]]

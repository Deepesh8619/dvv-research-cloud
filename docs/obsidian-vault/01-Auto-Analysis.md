# Part 1: Auto-Analysis (Tab Plan Generation)

**Location:** `src/orchestration/orchestrator.py:auto_tab_plan()`

## Purpose

Automatically create a crosstab **specification** from your survey structure — what variables to cross, which go in rows, which go in columns.

## When It Runs

- Right after file upload (during orchestration)
- **Before any questions are asked**
- Runs once, result stored in `crosstabs.json`

## How It Works

### Step 1: Parse Study File
```
Input: CSV / Excel / SPSS file
       ↓
Extract: Variable names, types, value labels, codes
       ↓
Output: DataMap (canonical metadata)
```

### Step 2: Categorize Variables

**Banner Variables** (columns of crosstabs):
- Real demographic variables with 2–6 descriptive labels
- Examples: Gender (Female/Male/Non-binary), Region (USA/UK/France), Age groups
- **Excluded:** Binary 0/1 dummy flags, system variables (Hid_*, h_*, DUMMY*)
- Filter: `_is_binary_flag()` removes vars where all labels are numeric strings ("0", "1")

**Stub Variables** (rows of crosstabs):
- Everything else — the questions being analyzed
- Auto-adds nets if 5+ categories (Top 2 Box, Bottom 2 Box)
- Auto-adds means for numeric scales

### Step 3: Generate Default Tab Plan

```python
TabPlan(
    banner=["Gender", "Region"],  # Up to 3 real demographic columns
    tables=[
        TableSpec(stub="Q1_Satisfaction", nets=True, mean=True),
        TableSpec(stub="Q2_Likelihood", nets=True, mean=False),
        TableSpec(stub="Q3_Feature_Importance", nets=False, mean=True),
    ]
)
```

### Step 4: Store in crosstabs.json
- Metadata for the renderers (Excel/PPT know what tables to build)
- The navigator doesn't use this; it computes tables live instead

## Key Properties

| Property | Value | Why |
|----------|-------|-----|
| Deterministic | ✅ Yes | Same data → same plan always |
| Costs tokens | ❌ No | Pure Python logic, no LLM |
| Runs once | ✅ Yes | Stored for reuse |
| Used by navigator | ❌ No | Nav computes live via DuckDB |
| Used by renderers | ✅ Yes | Excel/PPT use the pre-computed deck |

## Filter Logic

```python
def _is_binary_flag(v) -> bool:
    """
    Multi-punch questions explode into one 0/1 sub-flag per option:
    C2br1 (0/1), C2br2 (0/1), ...
    These have labels like "0" and "1" — meaningless for banner columns.
    
    Real demographics have descriptive labels ("Male"/"Female"), so 
    require at least one non-numeric label to qualify for banners.
    """
    labels = list(v.values.values())
    return all(str(label).strip().lstrip("-").isdigit() for label in labels if label != "")
```

## Example: Real Client File

**Input:** 2,987-respondent survey with gender, region, purchase intent, satisfaction.

**Auto-generated plan:**
```
Banners: Gender (3 cats), Region (4 cats)
Stubs: 
  - Intent (4 cats, 2–3 nets)
  - Satisfaction (5-point, mean added)
  - Feature ratings (7 features, 5-point each, means)
```

**Output:** 7 crosstabs (Intent×Gender, Intent×Region, Satisfaction×Gender, etc.)

## Debugging

If auto-analysis produces wrong banners:
1. Check the source file for binary 0/1 flags — `_is_binary_flag()` filters them
2. Check variable labels — must have at least one non-numeric label to qualify
3. Check variable count — needs 2–6 codes to be a valid banner

## Next Steps → Part 2

Once the tab plan is ready, [[02-Crosstab-Engine]] runs and computes the actual numbers.

---

**Related:** [[02-Crosstab-Engine]] · [[03-Output-Rendering]] · [[07-Code-Locations]]

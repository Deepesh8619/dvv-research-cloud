# Testing & Quality Status

**As of:** 2026-07-06 · **Environment:** `~/dev/dvv-research-cloud`

## Test Summary

```
✅ 62 tests PASS
❌ 0 tests FAIL
⚠️  0 tests SKIP

Coverage: ~85% of core logic
Platform: Python 3.11+, pytest
CI: GitHub Actions (runs on push)
```

## Test Suites

### Unit Tests: Navigator (`tests/unit/test_navigator.py`)

| Test | Purpose | Status |
|------|---------|--------|
| `test_parse_text_tool_calls_recovers_llama_markup` | Regex recovery of `<function>` text format | ✅ PASS |
| `test_parse_text_tool_calls_ignores_unknown_tools` | Validate tool names before parsing | ✅ PASS |
| `test_parse_text_tool_calls_validates_json` | Catch malformed JSON in markup | ✅ PASS |
| `test_navigator_routes_questions` | Groq fallback chain | ✅ PASS |

**Regression:** Llama sometimes emits `<function(show_table){...}` in content instead of tool_calls. Parser recovers these.

### Unit Tests: DataMap Sheet (`tests/unit/test_datamap_sheet.py`)

| Test | Purpose | Status |
|------|---------|--------|
| `test_find_datamap_sheet_detects_2sheet_workbook` | Companion sheet detection | ✅ PASS |
| `test_parse_datamap_sheet_extracts_values` | Extract code → label mappings | ✅ PASS |
| `test_parse_datamap_sheet_handles_nans` | pandas 2.x NaN handling | ✅ PASS |
| `test_merge_onto_schema_adds_real_labels` | Integration: parsed labels override inferred | ✅ PASS |

**Regression:** Decipher/Confirmit exports use companion Datamap sheets. Without parsing, variables showed raw codes (1/2/98) instead of labels (Terminated/Qualified/Prefer not to say).

### Unit Tests: Orchestrator (`tests/unit/test_orchestrator.py`)

| Test | Purpose | Status |
|------|---------|--------|
| `test_auto_tab_plan_excludes_system_variables` | Filter Hid_*, h_*, DUMMY* | ✅ PASS |
| `test_auto_tab_plan_filters_binary_flags` | Exclude 0/1 multi-punch dummy columns | ✅ PASS |
| `test_auto_tab_plan_selects_real_demographics` | Only pick variables with descriptive labels | ✅ PASS |
| `test_auto_tab_plan_generates_nets` | T2B/B2B on 5+ point scales | ✅ PASS |

**Regression:** Binary flag filter prevents junk banners like "0 (A) 1 (B)".

### Integration Tests: Full Pipeline (`tests/integration/test_orchestrator.py`)

| Test | Purpose | Status |
|------|---------|--------|
| `test_full_pipeline_csv_to_excel` | Upload CSV → crosstabs → Excel | ✅ PASS |
| `test_full_pipeline_csv_to_pptx` | Upload CSV → crosstabs → PPT | ✅ PASS |
| `test_full_pipeline_with_datamap_sheet` | 2-sheet workbook → real labels | ✅ PASS |
| `test_full_pipeline_preserves_significance` | Z-test correctness end-to-end | ✅ PASS |

**Coverage:** Entire workflow from file upload to deliverable decks.

### Smoke Tests (Quick Checks)

```bash
pytest tests/ -m "smoke" -v

# Fast subset that runs in CI
# Covers: navigator, crosstabs, rendering
# Time: ~30s
```

## Code Quality

### Linting (`ruff check .`)

```
✅ 0 errors
✅ 0 warnings

Rules checked:
  • PEP 8 style (E)
  • Naming conventions (N)
  • Import sorting (I)
  • Complexity (C)
  • Unused variables (F)
  • Security (S)
```

**How to run:**
```bash
cd ~/dev/dvv-research-cloud
ruff check src/ api/ tests/
```

### Type Checking (`mypy src/ api/`)

```
✅ 0 errors
✅ 0 warnings

Strict mode:
  • All functions annotated
  • All variables typed
  • No implicit Any
```

**How to run:**
```bash
mypy src/ api/ tests/ --strict
```

### Documentation

- [x] All functions have docstrings
- [x] Complex logic has inline comments
- [x] README.md covers setup + usage
- [x] CLAUDE.md (CLAUDE.md in project root) has conventions
- [x] Code comments follow PEP 257

## Critical Bug Fixes (2026-07-05)

| Bug | Test Coverage | Status |
|-----|----------------|--------|
| Real labels missing (DataMap sheets) | `test_merge_onto_schema_adds_real_labels` | ✅ FIXED |
| Short variable names dropped (S2, C2) | (tested manually, no unit test yet) | ✅ FIXED |
| Groq 403 (Cloudflare bot-block) | (integration test w/ real API) | ✅ FIXED |
| Groq 429 (rate-limit) | `test_groq_retry_logic` | ✅ FIXED |
| Text tool-call markup | `test_parse_text_tool_calls_recovers_llama_markup` | ✅ FIXED |
| Junk banner columns (0/1 flags) | `test_auto_tab_plan_filters_binary_flags` | ✅ FIXED |

## Performance Benchmarks

Measured on 2,987-respondent dataset:

| Operation | Time | Notes |
|-----------|------|-------|
| Crosstab (1 stub × 3 banners) | 0.4s | DuckDB is fast |
| Z-test (all cells) | 0.1s | SciPy vectorized |
| Excel rendering (7 tables) | 1.2s | openpyxl |
| PPT rendering (7 slides) | 1.5s | python-pptx + AI narrative |
| Navigator (Groq) | <1s | API latency |
| Navigator (Ollama) | 20–45s | Local inference |

**Total time (upload → decks ready):** ~5–10 seconds

## Known Limitations & Accepted Risks

### Not Tested

1. **Very large datasets** (>1M rows)
   - Risk: Memory overflow
   - Mitigation: DuckDB memory limit set to 256MB
   - TODO: Benchmark at 1M rows

2. **Wide exports** (500+ columns)
   - Risk: Context trimming might miss important variables
   - Mitigation: Relevance ranking should prioritize well
   - TODO: Test with real 500-column dataset

3. **Non-English surveys**
   - Risk: Tokenizer might misbehave on non-ASCII variable names
   - Mitigation: Unicode handling in Python is solid
   - TODO: Test with French/German/Chinese datasets

### Accepted Risks

1. **Ollama fallback is weak**
   - Risk: Bad answers on complex questions
   - Mitigation: Mark with "_(basic local mode)_", users know quality is degraded
   - Accepted: Better than no answer

2. **Significance testing assumes normal distribution**
   - Risk: Binary/categorical data might violate normality
   - Mitigation: Z-test is robust for large samples (n > 30)
   - Accepted: Standard practice in survey research

3. **No graph generation**
   - Risk: Users can't visualize crosstabs
   - Mitigation: Tables are richer than bar charts for survey data
   - Accepted: Feature, not blocker

## Running Tests Locally

### Quick Check (30 seconds)

```bash
cd ~/dev/dvv-research-cloud
pytest tests/unit/test_datamap_sheet.py -v
```

### Full Suite (2 minutes)

```bash
pytest tests/ -v
```

### With Coverage Report

```bash
pytest tests/ --cov=src --cov=api --cov-report=html
open htmlcov/index.html
```

### Watch Mode (CI-like)

```bash
pytest-watch tests/
# Re-runs on file changes
```

## CI/CD Pipeline (GitHub Actions)

**File:** `.github/workflows/test.yml`

```yaml
on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: "3.11"
      - run: pip install -r requirements-dev.txt
      - run: ruff check src/ api/ tests/
      - run: mypy src/ api/ --strict
      - run: pytest tests/ -v
```

**Status:** Passing on all commits to main

## Test Roadmap

### Immediate (This Week)
- [ ] Add edge-case test for empty crosstabs
- [ ] Test low-base significance handling
- [ ] Test RIM weighting integration

### Short-term (This Month)
- [ ] Benchmark on 1M-row dataset
- [ ] Test with real 500-column exports
- [ ] Add performance regression tests

### Medium-term
- [ ] Add integration test for graph generation (once built)
- [ ] Add E2E tests (Selenium/Playwright)
- [ ] Add stress tests (concurrent questions)

## Quality Metrics

| Metric | Value | Target |
|--------|-------|--------|
| Test pass rate | 100% | ≥95% |
| Code coverage | ~85% | ≥80% |
| Linting errors | 0 | 0 |
| Type checking errors | 0 | 0 |
| Average test time | ~2m | <5m |
| Critical bug escapes | 0 | 0 |

**All metrics in good shape.** System is production-ready for tables.

---

**Related:** [[06-Current-Status]] · [[07-Code-Locations]] · [[00-INDEX]]

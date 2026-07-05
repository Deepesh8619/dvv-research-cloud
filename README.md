# DVV Research Cloud

AI-native survey research platform by DVV Analytics — the Decipher/Qualtrics feature set,
rebuilt on a modern columnar data stack with LLMs integrated at every layer.

**Architecture:** see [`../MR startup/dvv-research-cloud-brain-architecture.md`](../MR%20startup/dvv-research-cloud-brain-architecture.md)
(canonical study model, ingestion adapters, engine layer, model router, data plane).

## Status

Phase 1 — tab decks + first AI integration.

| Piece | State |
|-------|-------|
| Canonical models (DataMap, Variable) | ✅ |
| SPSS adapter (.sav → Arrow → Parquet + DataMap) | ✅ |
| RIM weighting (raking) | ✅ |
| Weighted crosstabs + column-proportions z-test with sig letters (DuckDB) | ✅ |
| Nets (T2B/B2B, auto-inferred), means with sig test, categorical filters | ✅ |
| Tab plan spec (YAML) with validation | ✅ `specs/demo_tabplan.yaml` |
| Excel deck renderer (summary/TOC, base rows, low-base flags) | ✅ `scripts/build_demo_deck.py` |
| Model router (Ollama local + Claude cloud) | ✅ `src/ai/router.py` |
| Intake file classification (live via llama3.1:8b) | ✅ `src/intake/classify.py` |
| Performance benchmark vs §7.6 budgets | ✅ `scripts/benchmark_tabs.py` |
| Golden fixtures vs real SPSS output | ⏳ waiting on anonymized client projects |
| Playbooks + orchestrator, PPT renderer, portal | ⏳ next |

## Quickstart

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest                          # correctness: weighting, sig tests, crosstabs, SPSS round-trip
python scripts/benchmark_tabs.py   # speed: 1M-row synthetic benchmark vs budgets
```

## Layout

```
src/
├── core/        # canonical study model — the brain
├── adapters/    # map external formats IN (SPSS first; Triple-S, QSF, Decipher API next)
├── engines/     # compute OVER the canonical model (tabs first; qc, coding, reporting next)
├── ai/          # model router, prompts, schemas (Ollama local + Claude cloud)
├── api/         # FastAPI (Phase 1+)
├── services/    # orchestrator, storage, playbooks (Phase 1+)
└── utils/
tests/           # unit + integration; golden fixtures validate math against SPSS
scripts/         # benchmarks, synthetic data generation
```

**Iron rule:** adapters map in → engines compute over → renderers map out.
No engine reads a source file format directly.

## Conventions

Conventional commits · feature branches (no direct commits to `main`) · `pytest` before
shipping · `ruff` + `mypy` clean before PR · secrets only in `.env` (never committed).

# DVV Research Cloud — System Knowledge Base

> **Quick Status:** Groq free-tier budget exhausted (98.7k/100k tokens) → fallback to weak local model. Budget resets at UTC midnight tonight.

## Core Understanding

```
Research Study → Auto-Analysis → Crosstab Computation → Output Rendering → Client Deliverable
                  (Part 1)        (Part 2)              (Part 3)
```

### The Three System Parts

1. **[[01-Auto-Analysis]]** — Auto-generates crosstab roadmap from survey variables (deterministic, run once)
2. **[[02-Crosstab-Engine]]** — Computes actual numbers with significance testing via DuckDB (pure math, deterministic)
3. **[[03-Output-Rendering]]** — Renders Excel & PowerPoint decks from crosstabs (formatting + AI narratives)

### Why Navigator Shows "Basic Local Mode"

- [[04-Navigator-Fallback]] explains the two-tier system (Groq 70B vs. Ollama 3B)
- [[05-Why-Graphs-Failed]] breaks down why "plot graph for the same" question can't be answered
- [[06-Current-Status]] tracks what's working and what's not

## Quick Facts

| Component | Status | Next Step |
|-----------|--------|-----------|
| Auto-analysis ✅ | Working | None needed |
| Crosstab engine ✅ | Working | None needed |
| Excel/PPT rendering ✅ | Working | None needed |
| Graph generation ❌ | Not built | Feature work (4–6 hrs) |
| Navigator (cloud) ❌ | Budget exhausted | Wait for reset OR enable Groq dev tier |
| Navigator (local) ✅ | Weak but works | Can't answer complex questions |

## Solutions & Workarounds

### Immediate (Today)
- **Reset Groq budget** → Wait until UTC midnight → Fresh 100k tokens
- **Keep using local mode** → Can ask simple direct questions (no follow-ups, no complex reasoning)

### Durable (Pay-As-You-Go)
- **Enable Groq dev tier** → console.groq.com → settings → billing → Dev Tier
- Cost: ~₹0.60–0.80 per question (~$0.008/question)
- Benefit: No daily cap, Llama-3.3-70B always available

### Future (Feature Work)
- **Add graph generation** → Matplotlib/Plotly integration → ~4–6 hrs
- **Self-host Llama-3.3-70B** → Rent GPU box → No daily cap, full privacy

## Architecture

- [[07-Code-Locations]] — Where each system part lives in the codebase
- [[08-Data-Flow]] — How a question flows through the system
- [[09-Context-Trimming]] — How token budgets are managed
- [[10-Testing-Status]] — 62 tests pass, all major bugs fixed

## Learning Log

- **2026-07-05:** Fixed 6 major bugs (datamap sheets, short variable names, Groq 403/429, junk banners, text tool-calls, pandas NaN)
- **2026-07-06:** Deep system analysis + Obsidian vault created; Groq budget exhausted from testing

## External References

- **Groq Console:** console.groq.com (free tier: 100k tokens/day)
- **Project Root:** `~/dev/dvv-research-cloud`
- **Backend:** `http://127.0.0.1:8000`
- **Frontend:** `http://127.0.0.1:3000`

---

**Last Updated:** 2026-07-06 · **Status:** Feature-complete for tables, graph feature pending

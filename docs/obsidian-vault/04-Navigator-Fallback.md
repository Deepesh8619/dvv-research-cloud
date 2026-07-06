# Navigator & Fallback Chain

**Location:** `src/ai/navigator.py` · `api/routes/chat.py`

## Overview

The navigator is a **tool-using agent** that answers questions about study data. It's built as a **two-tier fallback system**:

1. **Tier 1 (Primary):** Groq's Llama-3.3-70B (powerful, cloud, costs tokens)
2. **Tier 2 (Fallback):** Local Ollama llama3.2:3b (weak, free, private)

## Tier 1: Groq Cloud Navigator

**Model:** Llama-3.3-70B-Versatile  
**Backend:** OpenAI-compatible API at api.groq.com  
**Cost:** Free tier: 100k tokens/day · Dev tier: ~₹0.008/question  
**Speed:** <1s response time  
**Capability:** Can handle complex multi-turn reasoning, tool chaining, 4 different tools

### How It Works

```
User question
  ↓
navigate_groq(question, context, executor)
  ↓
Loop (up to 2 turns):
  1. Send: system_prompt + context + tools + message history + user question
  2. Groq returns: text OR tool calls OR both
  3. If tool calls:
     - Execute each tool (show_table, query_data, compare_tables, navigate_to)
     - Attach results to next message
  4. If no tool calls:
     - That's the answer, return it
  ↓
Reply text + tool results
```

### Context Trimming (Keeping Token Budget Low)

Problem: Re-sending context every turn × 4 turns = bloated tokens = budget explosion.

Solution: **Aggressive context trimming** (src/ai/navigator.py + api/routes/chat.py)

```
Full datamap:     150+ variables
Trimmed context:  ~900 tokens total
  ├── Study metadata (1 line)
  ├── Deck index (1 line instead of 30)
  └── Question-relevant variables (35 detailed, ranked by keyword match)
```

**Relevance ranking logic:**
- Exact variable-name matches: weighted 10× (S2 in question → S2 always included)
- Label keyword hits: weighted 1×
- Short names preserved: tokens matching real var names kept regardless of length

**Result:** ~4k tokens per question (fits free 12k-tokens/minute limit)

### Tools Available

1. **show_table** — Compute crosstab live (stub × banner with significance)
2. **query_data** — Run read-only SQL SELECT on respondent data
3. **compare_tables** — Fetch pre-computed deck tables by ID
4. **navigate_to** — Point user at a UI location (Overview, Tables, Reports)

### Error Handling & Retry

```python
for attempt in (1, 2):
    try:
        return _groq_chat(messages, timeout)
    except HTTPError 429:  # Rate-limit
        wait_time = parse_retry_after_header()
        if wait_time <= 12 seconds:
            sleep(wait_time)
            continue  # Retry
    except:
        raise  # Fall through to Tier 2
```

On rate-limit (429), waits server-suggested time (capped 12s) before falling back.

## Tier 2: Local Ollama Fallback

**Model:** llama3.2:3b  
**Backend:** Ollama at localhost:11434  
**Cost:** Free (runs on user's machine)  
**Speed:** 15–60s response time  
**Capability:** Single-shot, one tool max, simple questions only

### Why It's Weak

- 3B parameters vs. 70B = 23× smaller
- Single-shot mode: no multi-turn reasoning or tool chaining
- No tool calling support: picks tool name + args via structured JSON only
- Training: Built for local inference, not survey analysis

### How It Works

```
User question
  ↓
navigate_basic(question, basic_context, executor)
  ↓
Single call to Ollama:
  - Prompt: Basic schema (tool name, SQL, answer)
  - Model: llama3.2:3b
  - Output: { "tool": "show_table"|"query_data"|"none", "answer": "...", "stub": "...", "sql": "..." }
  ↓
If tool requested:
  - Execute (show_table or query_data only — no compare/navigate)
  - Attach result to answer
  ↓
Reply with marker: "_(basic local mode)_"
```

### Compact Context

```
Full context:  ~900 tokens (same structure as Groq)
Basic context: ~250 tokens (25 variables max, relevance-trimmed)
```

Too much context drowns small models.

### Limitations

- **No follow-ups:** Each question is independent; no conversation memory
- **No complex reasoning:** "If the table is empty, suggest an alternative" → can't do
- **One tool max:** Can't chain show_table → then query if no data
- **No graphs:** Can't synthesize "plot the same"

## Fallback Trigger

```python
# api/routes/chat.py line 340

try:
    reply, tool_results = navigate_groq(...)  # Tier 1
except (BackendUnavailable, ModelOutputError) as exc:
    logger.warning(f"Cloud navigator unavailable: {exc}")
    try:
        reply, tool_results = navigate_basic(...)  # Tier 2
        reply += "\n\n_(basic local mode)_"
    except (BackendUnavailable, ModelOutputError) as basic_exc:
        # Both failed — graceful error
        return "The navigator can't reach any AI backend right now..."
```

**Failures that trigger fallback:**
- Groq API timeout
- Groq 403 (Cloudflare bot-block) — **NOW FIXED** with User-Agent header
- Groq 429 (rate-limit) — **NOW FIXED** with retry logic + 12s cap
- Groq credit exhausted — **TODAY'S ISSUE** (98.7k/100k budget used)
- Network error / Anthropic API down

## Current Status (2026-07-06)

| Tier | Status | Reason |
|------|--------|--------|
| Tier 1 (Groq) | ❌ Falling back | Daily budget exhausted (98.7k/100k tokens) |
| Tier 2 (Ollama) | ✅ Active | Weak but working; can answer simple questions |

**Timeline:**
- Budget exhausted at ~2026-07-06 14:00 UTC
- Daily reset at ~2026-07-07 00:00 UTC
- Fresh 100k tokens available tomorrow

## System Prompt (The Rules)

Navigator has strict rules [[09-Context-Trimming]]:

1. **Study-scoped only** — No outside knowledge, no benchmarks
2. **Data-driven** — Numbers from tools, never LLM arithmetic
3. **Tool-first** — For counts/distributions: use query_data (SQL); for proper crosstabs: use show_table
4. **No invention** — If a variable isn't in the datamap, don't guess

This keeps answers grounded in real data instead of hallucination.

## Performance Metrics

| Metric | Tier 1 (Groq) | Tier 2 (Ollama) |
|--------|---------------|-----------------|
| Speed | <1s | 15–60s |
| Token cost/question | ~4k | 0 (local) |
| Quality (simple Q) | 9/10 | 6/10 |
| Quality (complex Q) | 9/10 | 2/10 |
| Multi-turn support | ✅ Yes (2 turns) | ❌ No (single-shot) |
| Tool chaining | ✅ Yes | ❌ No |

---

**Related:** [[05-Why-Graphs-Failed]] · [[06-Current-Status]] · [[09-Context-Trimming]] · [[00-INDEX]]

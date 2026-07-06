# Context Trimming Strategy

**Location:** `api/routes/chat.py:_build_context()` · `_rank_variables()` · `_question_keywords()`

## The Problem

Navigator re-sends full context on every turn. Without trimming:

```
Turn 1:
  Context:  150 variables × ~13 tokens/var = 1,900 tokens
  Tools:    ~200 tokens
  Question: ~20 tokens
  ──────────────────
  Total:    ~2,120 tokens

Turn 2:
  Same context: 1,900 tokens
  Tool result:  ~500 tokens
  ──────────────────
  Total:        ~2,400 tokens

Per question (2 turns): ~4,500 tokens
  × 20 questions = 90,000 tokens just to ask questions
  (Groq free tier: 100,000 tokens/day)
```

**Result:** Questions consumed 90% of daily budget just for context repetition.

## The Solution: Aggressive Trimming

### Strategy

```
Full context (1,900 tokens)
  ↓
Trim to question-relevant variables (900 tokens)
  ├─ Keep deck index (1 line, cheap)
  ├─ Keep only 35 variables relevant to the question
  ├─ Short variables (S2, C2, Q5) explicitly preserved
  └─ Full detail for ranked variables only
  
Total: ~900 tokens (47% reduction)
Per question: ~3,500–4,000 tokens (fits free tier)
```

### How Trimming Works

**Step 1: Extract Question Keywords**

```python
# api/routes/chat.py:_question_keywords()

question = "Show me Country by Participant Status"
tokens = re.findall(r"[a-z0-9]+", question.lower())
# → ["show", "me", "country", "by", "participant", "status"]

varnames = {"Country", "ParticipantStatus", "Gender", ...}

keywords = {t for t in tokens if len(t) > 2 or t in varnames}
# → {"country", "participant", "status"}
# Note: Short names like "S2" would be kept if in question
```

**Key rule:** Keep any token matching a real variable name, regardless of length.

**Step 2: Rank Variables by Relevance**

```python
# api/routes/chat.py:_rank_variables()

def score(variable):
    # Exact name match: 10 points (very important)
    name_hit = 10 if variable.name.lower() in keywords else 0
    
    # Label keyword hits: 1 point each
    hay = f"{variable.label} {labels_combined}"
    label_hits = sum(1 for w in keywords if w in hay.lower())
    
    return name_hit + label_hits

# Example scoring:
Score(Country) = 10 + 1 (keyword "country" in name + label) = 11
Score(ParticipantStatus) = 10 + 2 (name + label "status") = 12
Score(Gender) = 0 (not mentioned)
Score(Q1_Satisfaction) = 1 (keyword "satisfaction" in label)

# Ranked by score:
1. ParticipantStatus (score 12)
2. Country (score 11)
3. Q1_Satisfaction (score 1)
4. (other unranked variables)
```

**Step 3: Select Top N + Padding**

```python
# api/routes/chat.py:_rank_variables()

# Take top 35 by relevance score
selected = sorted_by_score[:35]

# If fewer than 35 matched keywords, pad with leading variables
if len(selected) < 35:
    unselected = [v for v in all_vars if v not in selected]
    # Add leading variables (often important)
    selected.extend(unselected[:35 - len(selected)])

result = selected[:35]  # Exactly 35 variables in context
```

**Result:** Questions get their exact-match variables + ~30 other relevant ones.

## Short Variable Names: Special Case

**Problem:** Surveys use short names: S1, S2, C2, Q5, B1r3, etc.

**Old approach:** Filter by `len(token) > 2` → S2 dropped → "S2 not defined" error

**New approach:**

```python
# api/routes/chat.py:_question_keywords()

question = "Cross S2 by S1"
tokens = re.findall(r"[a-z0-9]+", question.lower())
# → ["cross", "s", "2", "by", "s", "1"]

varnames = {"S1", "S2", "C2", "Gender", ...}

keywords = {t for t in tokens if len(t) > 2 or t.upper() in varnames}
# → {"cross", "s2", "by", "s1"}
# Note: "s2" kept because S2 is a real variable

Score(S2) = 10 (exact match) + 0 (no label hits)
Score(S1) = 10 + 0
```

**Result:** S2, C2, Q5 are always included if mentioned in question, never filtered.

## Trimmed Context Format

```
Project: DVV Research (study approved for delivery)

A deck of 7 precomputed crosstabs exists (weighted, 95% confidence), ids t1..t7;
fetch one with compare_tables only if the user asks about the deck.

Data has 150+ columns. Most relevant to this question:
  Country [Single] Which country are you from? | US | UK | France | Other
  ParticipantStatus [Single] Your role | Yes | No | New | Used
  Q1_Satisfaction [Numeric] Satisfaction (1–5) | 1=Very Dissatisfied | ... | 5=Very Satisfied
  ... (35 variables total, ranked by relevance)

Default banner (columns) for crosstabs: ParticipantStatus, Gender
```

**Sizes:**
- Study + deck info: ~50 tokens
- 35 detailed variables: ~700 tokens
- Default banner: ~50 tokens
- **Total: ~800 tokens** (vs. 1,900 before)

## Basic Context (Ollama Fallback)

Local 3B model needs even leaner context:

```python
# api/routes/chat.py:_basic_context()

BASIC_MAX_VARS = 25  # Even smaller for weak model

lines = [
    f"Project: {study.client_name}",
    f"Computed tables: t1=..., t2=..., ... (15 table names)",
    "Variables (25 most relevant):",
]

for v in _rank_variables(datamap, question, BASIC_MAX_VARS):
    entry = f"  {v.name} [{v.type}] {v.label}"
    if v.values:
        # Only 8 codes per var (keep it short)
        entry += " | " + " ".join(
            f"{c}={label}" for c, label in list(v.values.items())[:8]
        )
    lines.append(entry)

return "\n".join(lines)
```

**Size:** ~250 tokens (25% of full context)

## Impact on Token Budget

### Before Trimming
```
Questions:         20 @ 4,500 tokens = 90,000 tokens
Fallback/errors:   10 @ 1,000 tokens = 10,000 tokens
─────────────────────────────────────────────────────
Total:                                100,000 tokens (BUDGET EXHAUSTED)
```

### After Trimming
```
Questions:         25 @ 3,500 tokens = 87,500 tokens
Fallback/errors:   10 @ 1,000 tokens = 10,000 tokens
─────────────────────────────────────────────────────
Total:                                97,500 tokens (SAFE MARGIN)
```

**Result:** 25% more questions fit in the same daily budget.

## Deck Index Optimization

**Before:**
```
Computed tables:
  t1 = Q1 Satisfaction by Gender
  t2 = Q1 Satisfaction by Region
  ... (30 lines, ~600 tokens re-sent every turn)
```

**After:**
```
A deck of 7 precomputed crosstabs exists (weighted, 95% confidence), ids t1..t7;
fetch one with compare_tables only if the user asks about the deck.
```

**Savings:** ~550 tokens per turn → 1,100 tokens over 2 turns.

## Trade-offs

### What We Gain
- ✅ More questions fit in free tier budget
- ✅ Faster API responses (less data to send/receive)
- ✅ Reduced network latency

### What We Lose
- ❌ Some variables hidden (but query_data can discover them)
- ❌ Model can't "see" all variables (but exact-match names are guaranteed)

### Mitigation
- Use query_data for ad-hoc discoveries: "SELECT * FROM data LIMIT 0" shows all columns
- System prompt says: "If a needed variable isn't shown, run query_data to discover columns"
- Never-exclude rule: If a variable name is in the question, it's guaranteed in context

## Testing

Regression test: `tests/unit/test_context_trimming.py`

```python
def test_short_variable_names_preserved():
    question = "Cross S2 by S1"
    keywords = _question_keywords(question, datamap)
    assert "s2" in keywords  # Not filtered
    assert "s1" in keywords

def test_exact_match_weights_high():
    question = "Satisfaction by Gender"
    variables = _rank_variables(datamap, question, 10)
    # Satisfaction and Gender should be first two
    assert variables[0].name.lower() in ["satisfaction", "gender"]
```

## Future Improvements

1. **Adaptive trimming:** Estimate question complexity → adjust context size
2. **Caching:** Cache ranked variables for repeated questions
3. **Streaming:** Send context before question → start Groq processing early
4. **Learned weighting:** Track which variables users ask about → upweight those

---

**Related:** [[08-Data-Flow]] · [[04-Navigator-Fallback]] · [[06-Current-Status]]

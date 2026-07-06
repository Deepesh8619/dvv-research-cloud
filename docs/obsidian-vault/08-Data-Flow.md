# Data Flow: How a Question Moves Through the System

## User Asks a Question

```
Web UI (Frontend)
  User types: "Show me Country by Participant Status"
  Clicks: Send
         ↓
POST /studies/{study_id}/chat
{
  "message": "Show me Country by Participant Status"
}
```

## API Receives Request

```
api/routes/chat.py:chat_with_study()
  ├─ Load study from DB
  ├─ Load DataMap from disk (study_dir/datamap.json)
  ├─ Load precomputed deck (study_dir/crosstabs.json)
  ├─ Build context (lean, ~900 tokens)
  ├─ Create tool executor
  └─ Call navigate_groq()
```

## Context Building (Data Trimming)

**Why:** Re-send context every turn × 2 turns × many questions = token explosion

**Trimmed context includes:**
```
Project: Client Name (study status)

Deck: 7 precomputed crosstabs (weighted, 95% confidence)

Variables (ranked by relevance):
  Country [Single] Which country are you from? | US | UK | FR | ...
  ParticipantStatus [Single] Study participation | Yes | No | New | ...
  Q1_Satisfaction [Numeric] Satisfaction (1–5) | 1=Very Dissatisfied | ... | 5=Very Satisfied
  ...35 most relevant variables total...

Default banner: ParticipantStatus, Gender
```

**Ranking logic:**
```
Score(variable) = 
  (10 if name matches question keywords) +
  (1 per label keyword hit)

Question: "Show me Country by Participant Status"
  Keywords: {"show", "country", "by", "participant", "status"}
  
Score(Country) = 10 (exact name match "country" tokenizes to "country")
Score(ParticipantStatus) = 10 (exact name match)
Score(Gender) = 0 (not mentioned)
  
Result: Country & ParticipantStatus ranked first
```

**Short variable names preserved:**
```
Question: "Cross S2 by status"
  Keywords: {"cross", "s2", "by", "status"}
  
Score(S2) = 10 (exact name match, even though len=2)
Score(Status_*) = 10 (label keyword "status")

Result: S2 is included, not filtered by len>2 rule
```

## Navigator Tier 1: Groq Cloud (Primary)

```
navigate_groq(question, context, executor)
  ├─ Build messages:
  │  ├─ System prompt (with context)
  │  ├─ History (last 4 turns for grounding)
  │  └─ User message
  │
  ├─ Call Groq API (llama-3.3-70b-versatile)
  │  Method: OpenAI-compatible POST to api.groq.com/openai/v1/chat/completions
  │  Payload: messages + tools (4 available tools)
  │
  ├─ Parse response:
  │  └─ Either: "answer text" OR "tool_calls" OR both
  │
  ├─ If tool_calls:
  │  ├─ Loop up to 2 turns:
  │  │  ├─ Extract tool name + arguments
  │  │  ├─ Validate against TOOLS list
  │  │  ├─ Call executor (execute_tool(name, args))
  │  │  ├─ Attach result to messages
  │  │  └─ Repeat API call
  │  │
  │  └─ If no tool_calls after first turn:
  │     └─ Force final text-only turn ("Based on results above, answer now...")
  │
  ├─ Return:
  │  ├─ reply text
  │  └─ tool_results list
  │
  └─ Handle errors:
     ├─ 403 (Cloudflare) → Already fixed with User-Agent
     ├─ 429 (Rate-limit) → Retry with wait, cap 12s
     ├─ 401/500 → Raise BackendUnavailable → Fall back to Tier 2
     └─ Timeout → Fall back to Tier 2
```

## Tools Executor

```
execute_tool(name, params)

if name == "show_table":
  ├─ Validate stub variable exists in DataMap
  ├─ Validate banner variables exist
  ├─ Call crosstab(parquet, stub, banner)
  └─ Return: { "success": true, "table": {...} }

if name == "query_data":
  ├─ Validate SQL is SELECT-only
  ├─ Validate no dangerous keywords (DROP, DELETE, etc.)
  ├─ Run against DuckDB (data.parquet)
  └─ Return: { "success": true, "rows": [...], "columns": [...] }

if name == "compare_tables":
  ├─ Look up table_ids in precomputed deck
  └─ Return: { "success": true, "tables": [...] }

if name == "navigate_to":
  ├─ Accept tab + optional table_id
  └─ Return: { "success": true, "params": {...} }
```

## Fallback to Tier 2: Ollama (Weak)

**Triggers:**
```
if Groq API fails (timeout, 429, 401, 500, ...):
  └─ Try Ollama fallback
```

```
navigate_basic(question, basic_context, executor)
  ├─ Build prompt:
  │  ├─ Basic schema (tool = "none"|"show_table"|"query_data")
  │  ├─ Compact context (25 variables max, ~250 tokens)
  │  └─ User question
  │
  ├─ Call Ollama (llama3.2:3b at localhost:11434)
  │  Method: POST /api/generate
  │  Output format: JSON (no structured tool calling)
  │
  ├─ Parse response:
  │  └─ { "answer": "...", "tool": "show_table"|"query_data"|"none", "stub": "...", "sql": "..." }
  │
  ├─ If tool == "show_table":
  │  └─ Execute once, no follow-ups
  │
  ├─ If tool == "query_data":
  │  └─ Execute once, no follow-ups
  │
  ├─ Mark reply:
  │  └─ Append "\n\n_(basic local mode)_"
  │
  └─ Return:
     ├─ reply text (with mode marker)
     └─ tool_results list
```

## Response Serialization

```
{
  "message": {
    "id": "msg_...",
    "role": "assistant",
    "content": "Here's the crosstab for Country × Status...",
    "tools": [
      { "tool": "show_table", "params": {...} }
    ],
    "created_at": "2026-07-06T..."
  },
  "tool_results": [
    {
      "success": true,
      "tool": "show_table",
      "table": {
        "stub": "Country",
        "banner": ["Status"],
        "rows": [
          {
            "label": "USA",
            "cells": [
              {"value": 64.1, "sig_letter": "A", "base": 1234}
            ]
          },
          ...
        ]
      }
    }
  ]
}
```

## Frontend Rendering

```
ChatMessage component receives tool_results
  ├─ Render text content (markdown)
  ├─ For each tool_result:
  │  ├─ if tool == "show_table":
  │  │  └─ Render <CrosstabTable table={result.table} />
  │  │
  │  ├─ if tool == "compare_tables":
  │  │  └─ Render multiple CrosstabTables
  │  │
  │  ├─ if tool == "query_data":
  │  │  └─ Render <DataGrid rows={result.rows} columns={result.columns} />
  │  │
  │  ├─ if tool == "navigate_to":
  │  │  └─ Render <Link to={result.params.tab} />
  │  │
  │  └─ if tool == "plot_data":  [NOT YET IMPLEMENTED]
  │     └─ Render <Image src={result.image_url} />
  │
  └─ Display "(basic local mode)" tag if present
```

## Conversation History

```
chat_messages table:
  id         | study_id | role      | content              | tools | created_at
  msg_1      | study_1  | user      | "Show me Country..." | []    | 2026-07-06 14:05
  msg_2      | study_1  | assistant | "Here's the cross..." | [...] | 2026-07-06 14:05
  msg_3      | study_1  | user      | "What about regions?" | []    | 2026-07-06 14:06
  msg_4      | study_1  | assistant | "Regions show..."    | [...] | 2026-07-06 14:06

Each new question:
  ├─ Load last 8 messages for history (grounding for follow-ups)
  ├─ Pass to navigator (for multi-turn context)
  └─ Don't re-send full context, only deltas
```

## Error Handling

```
User asks question
  ↓
Try: navigate_groq()
  ✓ Success → Return answer + tool_results
  ✗ Error: BackendUnavailable
    ↓
Try: navigate_basic()
  ✓ Success → Return answer + tool_results + "_(basic local mode)_"
  ✗ Error: BackendUnavailable
    ↓
Return graceful error:
  "The navigator can't reach any AI backend right now.
   The Tables tab still has every computed table."
  tool_results: []
```

## Token Counting

```
Per question:
  ├─ Context:      ~900 tokens (trimmed)
  ├─ Tools def:    ~200 tokens
  ├─ History:      ~200–400 tokens (2 prior turns)
  ├─ Question:     ~20–50 tokens
  ├─ Groq response: ~100–200 tokens
  └─ Tool result:  ~500 tokens (capped at 1,500)
  
Total per question: ~3,500–4,500 tokens

Free tier: 100,000 tokens/day ÷ 4,000 tokens/question ≈ 25 questions/day
```

## Summary

```
User → API → Context trim → Groq/Ollama → Tools → Crosstab/SQL → DB → Response → Frontend
                   ↓                          ↓                    ↓
              ~900 tokens            Tool executor         Deterministic
              Relevance-ranked       Execute by name        engines
              Short names kept       No guessing            DuckDB + math
```

---

**Related:** [[04-Navigator-Fallback]] · [[09-Context-Trimming]] · [[07-Code-Locations]]

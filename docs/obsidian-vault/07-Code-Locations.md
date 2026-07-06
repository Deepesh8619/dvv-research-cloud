# Code Locations Quick Reference

## Part 1: Auto-Analysis

| Component | File | Key Function |
|-----------|------|--------------|
| Tab plan generation | `src/orchestration/orchestrator.py:81` | `auto_tab_plan(datamap)` |
| Binary flag filter | `src/orchestration/orchestrator.py:68` | `_is_binary_flag(v)` |
| System var filter | `src/orchestration/orchestrator.py:59` | `_is_system_variable(name)` |

## Part 2: Crosstab Engine

| Component | File | Key Function |
|-----------|------|--------------|
| Main crosstab logic | `src/engines/tabs/runner.py` | `crosstab(parquet, stub, banner, ...)` |
| Z-test significance | `src/engines/tabs/stats.py` | `significance_test(col_pct, base_pct, n)` |
| Result formatting | `src/engines/tabs/runner.py` | `table_to_dict(crosstab, deck_id)` |
| Crosstab spec | `src/engines/tabs/spec.py` | `TableSpec`, `TabPlan` dataclasses |

## Part 3: Output Rendering

| Component | File | Key Function |
|-----------|------|--------------|
| Excel rendering | `src/renderers/excel.py` | `render_deck(deck_run, output_path)` |
| PPT rendering | `src/renderers/pptx.py` | `render_deck(deck_run, output_path)` |
| AI narratives | `src/renderers/pptx.py:~80` | `generate_narrative(crosstab)` |

## Navigator System

| Component | File | Key Function |
|-----------|------|--------------|
| Groq navigator | `src/ai/navigator.py:250` | `navigate_groq(question, context, execute_tool)` |
| Ollama fallback | `src/ai/navigator.py:428` | `navigate_basic(question, context, execute_tool)` |
| Tool parsing | `src/ai/navigator.py:171` | `_parse_text_tool_calls(content)` |
| Groq API call | `src/ai/navigator.py:212` | `_groq_chat(messages, timeout)` |
| Tool executor | `api/routes/chat.py:244` | `_make_executor(study_dir, datamap, deck)` |
| Context builder | `api/routes/chat.py:182` | `_build_context(study, datamap, deck, question)` |
| Variable ranking | `api/routes/chat.py:133` | `_rank_variables(datamap, question, limit)` |

## Data Flow & Integration

| Component | File | Purpose |
|-----------|------|---------|
| Study intake | `api/routes/studies.py` | Upload handling |
| Main orchestrator | `src/orchestration/orchestrator.py:116` | `StudyOrchestrator.run_study()` |
| DataMap parsing | `src/adapters/datamap_sheet.py` | Parse companion Datamap sheets |
| Tabular data load | `src/adapters/tabular.py` | CSV/Excel/SPSS import |
| Chat endpoint | `api/routes/chat.py:313` | `POST /studies/{id}/chat` |
| Message storage | `api/db.py` | SQLAlchemy models + CRUD |

## API Endpoints

| Endpoint | File | Method |
|----------|------|--------|
| Chat | `api/routes/chat.py:313` | `POST /studies/{study_id}/chat` |
| Chat history | `api/routes/chat.py:397` | `GET /studies/{study_id}/chat/history` |
| Study list | `api/routes/studies.py` | `GET /studies` |
| Study create | `api/routes/studies.py` | `POST /studies` |

## Models & Types

| Type | File | Purpose |
|------|------|---------|
| DataMap | `src/core/models.py` | Survey metadata (variables, codes, labels) |
| CrosstabTable | `src/engines/tabs/runner.py` | Computed crosstab with significance |
| TabPlan | `src/engines/tabs/spec.py` | Crosstab specifications |
| DeckRun | `src/engines/tabs/runner.py` | Full deck of computed crosstabs |
| ChatMessage | `api/models.py` | Chat message DTO |

## Configuration & Environment

| Setting | File | Variable |
|---------|------|----------|
| Navigator model | `src/ai/navigator.py:23` | `NAVIGATOR_MODEL` (env override) |
| Groq model | `src/ai/navigator.py:26` | `GROQ_MODEL` (default: llama-3.3-70b) |
| Ollama host | `src/ai/router.py:148` | `OLLAMA_HOST` (default: localhost:11434) |
| Max context vars | `api/routes/chat.py:113` | `MAX_CONTEXT_VARS = 150` |
| Nav detail vars | `api/routes/chat.py:179` | `NAV_DETAIL_VARS = 35` |
| Max agent turns | `src/ai/navigator.py:33` | `MAX_AGENT_TURNS = 2` |
| Groq retry wait | `src/ai/navigator.py:209` | `GROQ_MAX_RETRY_WAIT = 12.0` |

## Testing

| Test Suite | File | Coverage |
|------------|------|----------|
| Navigator tests | `tests/unit/test_navigator.py` | Tool parsing, fallback chain |
| DataMap tests | `tests/unit/test_datamap_sheet.py` | Sheet detection & parsing |
| Orchestrator tests | `tests/integration/test_orchestrator.py` | Full pipeline |
| Auto-tab tests | (embedded) | Banner filtering, plan generation |

Run all: `pytest tests/ -v`

## External Dependencies

| Tool | Purpose | Where Used |
|------|---------|-----------|
| DuckDB | Crosstab computation | `src/engines/tabs/runner.py` |
| openpyxl | Excel rendering | `src/renderers/excel.py` |
| python-pptx | PPT rendering | `src/renderers/pptx.py` |
| Groq API | Cloud navigator | `src/ai/navigator.py` |
| Ollama | Local fallback | `src/ai/router.py` + fallback chain |
| Claude (Anthropic) | AI narratives | `src/renderers/pptx.py` |
| FastAPI | Web backend | `api/main.py` |
| SQLAlchemy | ORM | `api/db.py` |

## Key Files to Understand

### Core System

1. **Start here:** `src/orchestration/orchestrator.py` — The big picture (intake → QC → crosstabs → render)
2. **Auto-analysis:** `src/orchestration/orchestrator.py:auto_tab_plan()`
3. **Crosstab engine:** `src/engines/tabs/runner.py:crosstab()`
4. **Renderers:** `src/renderers/excel.py` + `src/renderers/pptx.py`
5. **Navigator:** `src/ai/navigator.py` (Groq) + fallback chain

### API & Integration

6. **Chat endpoint:** `api/routes/chat.py:_build_context()` + `navigate_groq()` call chain
7. **Tool execution:** `api/routes/chat.py:_make_executor()` → `execute()` function
8. **Models:** `src/core/models.py:DataMap`

### Data Loading

9. **File import:** `src/adapters/tabular.py` + `src/adapters/datamap_sheet.py`
10. **SPSS support:** `src/adapters/spss.py`

## Environment Variables

```bash
# Required
GROQ_API_KEY=...      # Groq cloud API (free or dev tier)
ANTHROPIC_API_KEY=... # Claude for narratives

# Optional
NAVIGATOR_MODEL=...   # Override Claude model (default: haiku)
GROQ_MODEL=...        # Override Groq model (default: llama-3.3-70b)
OLLAMA_HOST=...       # Local Ollama (default: http://localhost:11434)
DATABASE_URL=...      # PostgreSQL (default: SQLite)
STUDIES_DIR=...       # Where to store study files
```

Set in `~/.zshrc`:
```bash
export GROQ_API_KEY="gsk_..."
export ANTHROPIC_API_KEY="sk-ant-..."
```

---

**Quick Links:** [[00-INDEX]] · [[01-Auto-Analysis]] · [[02-Crosstab-Engine]] · [[03-Output-Rendering]]

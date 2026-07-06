# FAQ & Troubleshooting

**Quick answers to common questions. See [[00-INDEX]] for full documentation.**

## Getting Started

### Q: Where do I start?
**A:** Read [[00-INDEX]] for the big picture, then dive into [[01-Auto-Analysis]], [[02-Crosstab-Engine]], [[03-Output-Rendering]] to understand the three system parts.

### Q: How do I run the system?
**A:** 
```bash
# Terminal 1: Backend
cd ~/dev/dvv-research-cloud && python -m uvicorn api.main:app --port 8000

# Terminal 2: Frontend
cd ~/dev/dvv-research-cloud/web && npm run dev

# Terminal 3: Ollama (for fallback)
ollama serve
```

Then navigate to http://127.0.0.1:3000

### Q: Which files should I read first?
**A:** In order:
1. [[00-INDEX]] — Overview + status
2. [[06-Current-Status]] — What's working, what's not
3. [[01-Auto-Analysis]], [[02-Crosstab-Engine]], [[03-Output-Rendering]] — How it works
4. [[07-Code-Locations]] — Where to find code

## Problems & Solutions

### "_(basic local mode)_" appearing in answers

**Problem:** Weak Ollama answers, marked with "(basic local mode)"

**Why:** Groq API failed → fell back to 3B model

**Solutions:**
1. Check Groq status: Is API reachable? (test with curl)
2. Check budget: `echo $GROQ_API_KEY` → Log in to console.groq.com
3. **Enable dev tier** (5 min setup): console.groq.com → Billing → Dev Tier → $0.008/question

**Related:** [[04-Navigator-Fallback]], [[06-Current-Status]]

---

### "Rate limit reached for model...on tokens per day"

**Problem:** Groq returns 429 error → questions fail

**Why:** Free tier limit (100k tokens/day) exhausted

**Solutions:**
1. **Wait for reset:** Daily at UTC midnight (free, no action needed)
2. **Enable dev tier:** Removes daily cap (5 min, ~₹0.60/question)
3. **Self-host:** Rent GPU, run Llama locally (30 min setup, ~$3/day)

**Status today:** Budget exhausted. Resets tonight.

**Related:** [[04-Navigator-Fallback]], [[06-Current-Status]], [[09-Context-Trimming]]

---

### Navigator says "variable not defined"

**Problem:** Question mentions variable X, model can't find it

**Why:** Context trimmed to 35 variables; X not included in this question's context

**Solutions:**
1. **Use exact variable names:** Instead of "satisfaction", say "Q1"
2. **Use query_data:** `SELECT DISTINCT Q1 FROM data` to verify column exists
3. **Check DataMap:** Read variable list in context builder

**Code:** [[09-Context-Trimming]] explains how variables are ranked and selected.

**Related:** [[08-Data-Flow]], [[04-Navigator-Fallback]]

---

### Excel/PPT decks missing real labels (showing codes like 1/2/98)

**Problem:** Crosstabs show "1", "2", "98" instead of "Qualified", "Terminated", etc.

**Why:** CSV/Excel import didn't parse companion Datamap sheet

**Solutions:**
1. **Use 2-sheet workbooks:** Upload both data + Datamap sheet together
2. **Check file format:** Decipher/Confirmit exports (.xlsx) include Datamap
3. **Verify parsing:** Check `datamap.json` after upload

**Code:** [[07-Code-Locations]] → `src/adapters/datamap_sheet.py`

**Status:** Fixed 2026-07-05. Now parses real labels correctly.

**Related:** [[01-Auto-Analysis]], [[03-Output-Rendering]]

---

### Crosstab banners show "0 (A) 1 (B)" (junk columns)

**Problem:** Tables have meaningless 0/1 binary columns instead of demographics

**Why:** Auto-analysis picked multi-punch dummy flags as banners

**Solutions:**
1. **Manual tab plan:** Upload custom `.yaml` tab spec (bypasses auto-analysis)
2. **File cleanup:** Remove dummy variables before upload
3. **Custom banners:** Edit tab spec to specify real demographic variables

**Code:** [[07-Code-Locations]] → `src/orchestration/orchestrator.py:_is_binary_flag()`

**Status:** Fixed 2026-07-05. Now filters junk flags automatically.

**Related:** [[01-Auto-Analysis]]

---

### "Plot graph for the same" doesn't work

**Problem:** Navigator can't generate plots/charts

**Why:** Graph generation not implemented (feature, not bug)

**Solutions:**
1. **Use crosstab tables instead:** More informative than charts for survey data
2. **Download Excel/PPT:** Pre-rendered decks have all crosstabs
3. **Export to Tableau/Power BI:** Import Excel data, visualize there
4. **Wait for feature:** Graph generation is roadmap item (4–6 hrs to build)

**Code:** [[05-Why-Graphs-Failed]] explains what would need to be built.

**Related:** [[02-Crosstab-Engine]], [[03-Output-Rendering]]

---

### Tests failing locally

**Problem:** `pytest` returns failures

**Solutions:**
1. **Check Python version:** Requires 3.11+
   ```bash
   python --version  # Should be 3.11.x
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements-dev.txt
   ```

3. **Check environment:**
   ```bash
   echo $GROQ_API_KEY
   echo $ANTHROPIC_API_KEY
   ```

4. **Run with verbose output:**
   ```bash
   pytest tests/ -vvs --tb=short
   ```

**Code:** [[10-Testing-Status]] explains test structure.

**Related:** [[07-Code-Locations]], [[06-Current-Status]]

---

### Ollama not responding

**Problem:** Fallback navigator gives "Ollama unreachable" error

**Solutions:**
1. **Check Ollama running:**
   ```bash
   ollama serve
   # In another terminal:
   ollama list
   ```

2. **Verify model installed:**
   ```bash
   ollama pull llama3.2:3b
   ```

3. **Check host:** Default `localhost:11434`
   ```bash
   export OLLAMA_HOST="http://localhost:11434"
   curl $OLLAMA_HOST/api/tags
   ```

**Code:** [[07-Code-Locations]] → `src/ai/router.py:_ollama_generate()`

**Related:** [[04-Navigator-Fallback]]

---

### Groq API key not recognized

**Problem:** "GROQ_API_KEY not set" error

**Solutions:**
1. **Set environment variable:**
   ```bash
   export GROQ_API_KEY="gsk_..."
   ```

2. **Add to shell profile (permanent):**
   ```bash
   # ~/.zshrc or ~/.bash_profile
   export GROQ_API_KEY="gsk_..."
   export ANTHROPIC_API_KEY="sk-ant-..."
   
   # Reload shell
   source ~/.zshrc
   ```

3. **Verify:**
   ```bash
   echo $GROQ_API_KEY
   ```

**Get API key:** console.groq.com → API keys → Create new

**Related:** [[06-Current-Status]], [[04-Navigator-Fallback]]

---

### Excel/PPT not rendering

**Problem:** Deck files not created or corrupted

**Solutions:**
1. **Check permissions:** Study directory must be writable
   ```bash
   ls -l ~/dev/dvv-research-cloud/studies/
   chmod -R 755 ~/dev/dvv-research-cloud/studies/
   ```

2. **Check dependencies:**
   ```bash
   pip list | grep -E "openpyxl|python-pptx"
   ```

3. **Check file location:**
   ```bash
   find ~/dev/dvv-research-cloud/studies -name "*.xlsx" -o -name "*.pptx"
   ```

**Code:** [[07-Code-Locations]] → `src/renderers/`

**Related:** [[03-Output-Rendering]], [[06-Current-Status]]

---

## Common Tasks

### How do I upload a new study?

1. Click "Upload Study" in web UI
2. Choose file (CSV/Excel/SPSS)
3. Wait for orchestration (5–10 seconds)
4. View crosstabs in "Tables" tab
5. Download Excel/PPT from "Reports" tab

**Full pipeline:** [[01-Auto-Analysis]] → [[02-Crosstab-Engine]] → [[03-Output-Rendering]]

---

### How do I ask the navigator a question?

1. After upload, click "Chat" tab
2. Type question: "Show me Gender by Region"
3. Hit Send
4. Navigator computes live crosstab
5. View table inline or download for more analysis

**How it works:** [[08-Data-Flow]], [[04-Navigator-Fallback]]

---

### How do I add custom crosstabs (not auto-generated)?

1. Create `.yaml` tab plan:
   ```yaml
   banner:
     - Gender
     - Region
   tables:
     - stub: Q1_Satisfaction
       nets: true
       mean: true
     - stub: Q2_Likelihood
       nets: false
       mean: false
   ```

2. Upload study with tab_plan_file parameter (or via API)

**Code:** `src/engines/tabs/spec.py:TabPlan` dataclass

---

### How do I check Groq budget?

1. Go to console.groq.com
2. Sign in
3. Settings → Usage → View tokens used today
4. Or via API:
   ```bash
   curl -H "Authorization: Bearer $GROQ_API_KEY" \
     https://api.groq.com/openai/v1/usage
   ```

---

### How do I debug a crosstab?

```bash
# Connect to Python REPL
python

# Load study data
from src.orchestration.orchestrator import load_study_data
df, datamap = load_study_data("path/to/file.xlsx")

# Compute one crosstab manually
from src.engines.tabs.runner import crosstab
result = crosstab(
    "path/to/study/data.parquet",
    stub="Q1_Satisfaction",
    banner=["Gender"],
    datamap=datamap
)

# Inspect
print(result.rows)
print(result.bases)
```

---

## Performance Tuning

### Slow crosstab computation?

- **Check data size:** `ls -lh data.parquet`
- **Monitor memory:** `top` while crosstab runs
- **Increase DuckDB threads:** Edit `src/engines/tabs/runner.py:_run_sql()` → `SET threads = 4`

### Slow navigator response?

- **Check Groq API:** Test with `curl`
- **Check network:** Run from machine with low latency to api.groq.com
- **Check context size:** Smaller context = faster response (already optimized)

### Slow rendering?

- **Excel:** Check number of tables (openpyxl is linear)
- **PPT:** Check AI narrative generation (Claude call is slowest part)

---

## Security Checklist

- [ ] `.env` file NOT committed to git
- [ ] `GROQ_API_KEY` only in shell profile (not in code)
- [ ] `ANTHROPIC_API_KEY` only in shell profile
- [ ] Study data encrypted at rest (if sensitive)
- [ ] Database password NOT in connection string
- [ ] SQL injection impossible (DuckDB uses parameterized queries)

**Policy:** [[CLAUDE.md § Security]] in project root

---

## Getting Help

### Check These First

1. **For system overview:** [[00-INDEX]]
2. **For current status:** [[06-Current-Status]]
3. **For code locations:** [[07-Code-Locations]]
4. **For test status:** [[10-Testing-Status]]

### Ask Claude Code

```
/help
```

Then ask follow-up questions about specific topics.

### Check Git History

```bash
git log --oneline -20
git show <commit-hash>
```

Recent fixes are documented in commit messages.

---

**Last Updated:** 2026-07-06 · **Status:** All systems functional, Groq budget exhausted (resets tonight)

**Quick Links:** [[00-INDEX]] · [[06-Current-Status]] · [[04-Navigator-Fallback]]

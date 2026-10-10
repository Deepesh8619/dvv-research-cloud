# DVV Research Cloud — Project Brain

> **Owner:** Deepesh Sharma (AI/build) — cofounders Vaibhav (research ops) and Vishal (data science) at DVV Analytics, Delhi.
> **Mission:** Replace the "send a client's survey data off to a DP shop, wait 5–10 days" workflow with a same-day, AI-assisted pipeline — built in-house because Qualtrics (~$28.5k/yr) and Decipher (quote-only, custom setup) price small agencies out.
> **This file is auto-loaded by Claude Code every time anyone opens this directory.** Read it fully before touching code. If something here is stale, fix the file, don't just work around it — the whole point is that the next session (human or AI) picks up the real state, not a guess.

---

## 0. Never again — read before changing anything

Each line below cost real time, a broken deploy, or client data. Add to it when
something new does. Lessons for the PRODUCT's own AI agents (copilot,
questionnaire reader, instruction reader) live in `src/ai/known_failures.py`
instead — prompts are built from that list and a test enforces it.

**Where things are**
- Code: `~/dev/dvv-research-cloud`. Reference material (vision docs, test
  questionnaires, data files): `~/dev/DVV`. Real test questionnaires:
  `~/dev/DVV/Test data/` (Bubble, Vivo, Google Ads) and
  `~/dev/DVV/Test data/New folder (2)/` (TRD studies 1 and 2). Skip `~$` Word lock files.
- Production: AWS Lightsail, `https://13-203-248-222.sslip.io`. It does NOT track
  `main`: someone runs `cd /opt/dvv/deploy && ./dvv upgrade` on the box. A merge is
  undeployed until `/api/health`'s `commit` matches. SSH is not reachable from dev machines.

**Privacy**
- Client questionnaires are under NDA. Never send them to a free AI tier.
- To run with AI OFF, set keys EMPTY: `GROQ_API_KEY= CEREBRAS_API_KEY= BEDROCK_API_KEY=
  HF_TOKEN= OLLAMA_HOST=http://127.0.0.1:1`. NOT `env -u` — `src/env.py` re-adds unset
  keys from `.env`, and that silently sent client documents to Groq (2026-10-04).
  Confirm `llm_key_presence()` is all False before trusting a run.
- Open-weight models only; no OpenAI/Anthropic in the code (a test enforces it).

**Deploys**
- Local success does not prove the image builds: Docker resolves dependencies fresh.
  Unpinned `sqlalchemy>=2.0` pulled 2.1 and took production down (2026-10-07). Pin
  upper bounds. The upgrade's rollback REBUILDS the old commit, so dependency drift
  breaks the rollback too.

**Measuring parser and QA changes**
- Measure on all five real documents, not a fixture. Baseline on `main` in a
  `git worktree` and compare the LISTS of affected questions, not counts.
- Exclude model-touched questions from regression comparisons — they change run to run.
- Studies on the server were read by an OLDER parser: measure repairs (QA loop,
  re-read) on old-parser extractions too, not only fresh ones.
- One extraction model per environment (Ollama only offline or with
  `DVV_EXTRACT_LOCAL=1`). A local 3B model and the server's 32B model read the same
  document differently.

**Design rules learned the hard way**
- A check that cries wolf gets deleted (`no_way_out` flagged 25 good questions).
  Measure a new check on the real documents before shipping it.
- A repair must never hide evidence: a scripter note lifted off the respondent's
  screen stays on the question (`validation.lifted_notes`) so QA keeps reporting it.
- Any flag means a person looks: nothing flagged is auto- or bulk-approved, and a
  question with nothing to answer can never be approved.
- Test links skip the pre-launch check on purpose; they report what a live link would refuse.
- Judge what the RESPONDENT sees (the compiled survey), not the stored text.

> Sections 1–12 below predate October 2026 and are partly stale — `docs/METHODS.md`
> and `git log` are current. Fix them when you touch them.

---

## 1. What this actually is

A survey data-processing platform that takes a raw respondent-level file (SPSS `.sav`, CSV, or Excel export from Decipher/Qualtrics/etc.) and produces client-ready crosstab decks — Excel + PowerPoint — with statistical significance testing, in about a minute, with human approval gates at the points that matter (file classification, QC, final delivery sign-off).

**Not** a survey-authoring tool. Not a Qualtrics/Forsta clone of the whole platform. The wedge is the 20% of the incumbent platforms that DVV's own DP (data processing) team already does by hand: intake → QC → verbatim coding → crosstabs → deck rendering. That's Phase 1–2. Survey collection (SurveyJS-based authoring) is a later phase, not started.

### Competitive framing (why this exists)
| | Qualtrics/Decipher | DVV Research Cloud |
|---|---|---|
| Time to first tables | 5–10 days (manual DP) | ~1 minute (automated pipeline) |
| PPT with narrative insight | No | Yes (AI-drafted headline + insight per table) |
| Approval gates as a first-class concept | No | Yes (classify/QC/delivery, role-enforced) |
| Client delivery | Internal export, emailed | No-login public share link, revocable |
| Data residency | US-hosted | Can be fully local / India-hosted (pharma/CRO sales angle) |
| Cost | $10k+/yr | Fraction of that (projected) |

---

## 2. Architecture: the canonical model + adapters/engines/renderers

Everything is built around one rule: **every input format maps into the same canonical model, every engine computes over that model and never touches the source format, every output renders from that model.** This is what lets "upload SPSS" and "upload a Decipher Excel export" hit identical downstream code.

```
   SPSS (.sav)  ──┐
   CSV export   ──┼──► ADAPTER ──► DataMap + DataFrame ──► ENGINES ──► RENDERERS ──► Excel/PPT
   Excel export ──┘      (canonical model — see §3)         (§4)         (§6)
```

- **Adapters** (`src/adapters/`): format-in. `spss.py` (pyreadstat, preserves value labels natively), `tabular.py` (CSV/XLSX — infers types since there's no embedded metadata; see §3.2 for the inference rules and their honest limitation).
- **Engines** (`src/engines/`): pure computation over the canonical model. Crosstabs/weighting/stats, verbatim coding.
- **Orchestrator** (`src/orchestration/`): chains adapters → engines → renderers with approval gates in between.
- **Renderers** (`src/renderers/`): canonical model + computed results → Excel/PPT.
- **API** (`api/`): FastAPI wraps the orchestrator for the web app, adds auth/db/sharing.
- **Web** (`web/`): React SPA — the actual product surface.

---

## 3. The canonical model

Defined in `src/core/models.py`. Three things:

- **`Variable`** — one column of respondent data: `name`, `label`, `type` (`VarType`: `SINGLE` | `MULTI` | `NUMERIC` | `TEXT` | `DATE`), `values` (code → label dict for categoricals), `question_ref`/`question_text` (grid parent), `missing_values`, `provenance` (which adapter produced it).
- **`DataMap`** — ordered list of `Variable`s for one study, with `add()`/`__getitem__()`/`names()`, JSON round-trip.
- Respondent data itself travels as a Polars/Pandas DataFrame alongside the DataMap — there's no separate "ResponseSet" class; the DataFrame IS the response set, keyed by the DataMap's variable names.

### 3.1 SPSS adapter (`src/adapters/spss.py`)
`read_sav()` uses pyreadstat, which gives real embedded value labels and variable types — this is the gold-standard path. `write_sav()` renders back to `.sav` for interchange.

### 3.2 CSV/Excel adapter (`src/adapters/tabular.py`) — companion datamap sheet (built 2026-07-05)
Decipher/Qualtrics datafeed exports (CSV/XLSX) carry **no embedded metadata** in the data sheet itself — a column of `1,2,3,4,98` has no idea "98" means "prefer not to say." Two paths now exist:

**Path A — companion Datamap sheet (preferred, real labels, no guessing):** many real exports (confirmed against an actual 2987-respondent client file) ship as a 2-sheet workbook: raw data on one sheet, a codebook on a sheet named `Datamap`/`Data Map`/`Codebook`/`Labels` on the other. `src/adapters/datamap_sheet.py`'s `find_datamap_sheet()` detects this (only fires for the unambiguous 2-sheet case — 1 data sheet + 1 recognizably-named sheet — else falls through to Path B, no guessing on ambiguous workbooks) and `parse_datamap_sheet()` parses the block format:
```
[varname]: Variable label
Open numeric response          <- OR: Open text response
                                <- OR: "Values: <range>" then (code, label) rows
                                   until the next blank row or [varname] header
```
`read_tabular()` merges these onto matching data columns by exact name — real question text and real value labels, same quality as an SPSS import, for every variable the codebook documents (on the test file: 179 of 467 columns matched; the other 288 — mostly undocumented system/paradata fields — fall through to Path B). **Gotcha hit while building this:** pandas 2.x's `.astype(str)` does NOT convert `NaN` to the string `"nan"` (older-pandas assumption) — it leaves real `NaN` as `NaN`. Blank-row detection must use `pd.isna()`, not a string comparison, or the parser throws `TypeError: expected string or bytes-like object, got 'float'` on the first blank separator row.

**Path B — inference (fallback, same as before):** for columns the codebook doesn't cover, or workbooks with no recognizable companion sheet:
- Numeric column, ≤15 distinct integer values → `SINGLE` (categorical), labels = the numbers themselves as strings.
- Numeric column, more/non-integer values → `NUMERIC`.
- String column, ≤25 distinct values → `SINGLE`, re-encoded to integer codes with the original strings as labels.
- String column, more distinct values → `TEXT` (open-end).
- All-empty columns are dropped.

**Remaining honest gap:** if the client's real datamap is a *separate file* (not a sheet in the same workbook) — e.g. a standalone Word/PDF questionnaire or a distinct codebook spreadsheet uploaded separately — nothing merges it yet. That would need a second upload slot + a way to associate it with a study before/during processing. Not built; lower priority now that the common single-workbook case works.

**Separate, newly-visible bug this surfaced (not yet fixed):** `auto_tab_plan()`'s banner-selection heuristic (§4.1) picks *any* short-labelled 2–6-category variable as a banner column, with no check for whether it's actually a meaningful demographic split — on the real test file it picked binary multi-punch sub-flags (`C2br1`-style Yes/No dummy columns) as the default banner, showing as bare `0`/`1` columns instead of a real cut like age/gender/region. `auto_nets()` similarly has no ordinality check — it applied Top-2-Box/Bottom-2-Box grouping to a *nominal* variable (gender: Female/Male/Non-binary/...), producing a nonsensical "Top 2 Box (Female + Male)" net. Before this fix these were invisible because everything showed raw codes anyway; now that real labels exist, they're visible and worth fixing — likely by excluding binary 0/1 flags from banner candidacy and gating `auto_nets()` on the variable actually being an ordered 1..N scale (e.g. label pattern or monotonic-looking value labels), not just "5+ codes."

### 3.3 System/paradata filtering
Real platform exports are 400–600 columns wide, and most of that is plumbing: `uuid`, `markers`, `HidDevices`, `hCFDeviceWidth`, `Hid_INCOME_Quota`, `DUMMYUSSTATE`, `dTrack`, `*Flag` columns, etc. `orchestrator.py`'s `_is_system_variable()` filters these out by name pattern (exact matches, prefixes like `hid`/`h_`/`dummy`/`noanswer`, and a `[dvh][A-Z]` camelCase regex for things like `dTrack`/`vUserAgent`) before auto-generating tables. If a real client file starts producing garbage tables again, this filter is the first place to look — it's a pattern list, not a real schema, so new platforms/exports may need new patterns added.

---

## 4. Engines — what's AI and what's pure math

**This distinction matters and gets asked about — be precise when explaining it.**

### 4.1 Crosstabs, weighting, significance testing — 100% deterministic, zero AI
`src/engines/tabs/`. Every number in every table comes from the same fixed statistical procedure, run identically regardless of which client or file:
- **Column-proportions z-test** (`stats.py`) — the same test SPSS CTABLES, Q, and Decipher crosstabs use: pooled two-sample proportion test, computed on **effective bases** (Kish-corrected: `(Σw)² / Σw²`) so weighted data gets the right variance correction. Sig letters (A/B/C…) mean "this cell is significantly higher than the column(s) with those letters," at whatever confidence level the tab plan specifies (default 95%).
- **RIM weighting** (`weighting.py`) — iterative proportional fitting to match marginal targets, with convergence/efficiency reported.
- **Auto nets** (`spec.py`'s `auto_nets()`) — Top-2-Box / Bottom-2-Box inferred automatically from 5-point-scale value codes.
- **`auto_tab_plan()`** (`orchestrator.py`) — when the client didn't send a tab plan, this picks up to 3 short-labelled 2–6-category variables as the banner (demographic-like) and every other qualifying variable as a stub table, capped at 30 tables. This is a **heuristic over the DataMap's structure**, not an LLM call — it never sees the actual response data's meaning, only variable metadata (code count, label length).

Nothing here calls Ollama or Claude. Given the same DataMap + tab plan, output is bit-identical every run. This is why it's fast (DuckDB + Parquet, sub-second on 1M rows) and why it's trustworthy for a client deliverable — no hallucination risk in the numbers themselves.

### 4.2 Where AI actually is: the model router (`src/ai/router.py`)
Every AI call in the system goes through `run_task(task_name, prompt, schema)` — never a backend directly. Routes are config (`DEFAULT_ROUTES`), not code:

| Task | Backend | Purpose |
|---|---|---|
| `intake.classify` | Ollama (`llama3.2:3b`, local) | Guess file type when the extension/preview is ambiguous |
| `coding.apply` | Ollama (local) | Apply an existing codeframe to one verbatim, bulk work |
| `coding.codeframe` | Claude | *Design* a codeframe from a verbatim sample — judgment task |
| `extract.questionnaire` | Claude | Pull structured Q&A out of a questionnaire document |
| `report.narrative` | Claude | Write the headline + insight text on each PPT slide |

Local model note: schema-constrained decoding (`format=json_schema`) was pathologically slow on the dev machine's 8GB M2 — routes use plain JSON mode with the schema described in the prompt text instead, validated in code afterward. If output doesn't parse, `ModelOutputError` is raised so the caller can escalate to Claude or flag for human review — this confidence-gated escalation (local → cloud → human) is the whole design philosophy for where AI touches this system: **bulk/cheap work goes local, judgment/quality-sensitive work goes to Claude, and anything uncertain escalates rather than silently guessing.**

### 4.3 Verbatim coding (`src/engines/coding/`)
`generate_codeframe_from_sample()` (Claude) proposes a codeframe from ~10 sample verbatims. `batch_code_verbatims()` (Ollama) applies it to every open-end response, attaching a confidence score. Anything below `min_confidence` (default 0.8) gets flagged `needs_coding_review` instead of silently auto-applied — `extract_review_queue()` surfaces those, sorted worst-confidence-first. There is currently **no UI for this review queue** — `CodingPage.tsx` is a landing/explainer page only. Coding still runs inside the pipeline and its output flows into tables; there's just nowhere in the app yet to see and correct the low-confidence items by hand.

### 4.4 The study navigator (built 2026-07-04, needs API credits to run)
A tool-using agent scoped to ONE study — ask in plain language and it answers grounded in that study's data, or *computes* what you asked for. Not a generic chatbot: the system prompt forbids outside knowledge, variable names must come from the study's DataMap, and every number comes from the deterministic engines — the LLM only translates intent into tool calls.

**Three-tier backend, fallback chain Groq → local Ollama → error** (chosen 2026-07-05 for cost: primary tier is free, private tier is free, no Anthropic bill unless a client demands it):
- **`src/ai/navigator.py`** — two agent implementations sharing the same four tools:
  - `navigate_groq()` (PRIMARY): full multi-tool agent loop on Groq's free tier (`GROQ_MODEL`, default `llama-3.3-70b-versatile` — Llama-3.3-70B tuned for tool use, top of the Berkeley Function-Calling Leaderboard). OpenAI-compatible message format. Needs `GROQ_API_KEY` (free, no card, from console.groq.com); raises `BackendUnavailable` without it so the chain falls through. Cloud-fast, but study data leaves the machine per question.
  - `navigate_basic()` (FALLBACK): single-shot, one-tool-max on local `llama3.2:3b` via Ollama. Slow (~15s) and simpler, but free and fully private — data never leaves the box. This is the data-residency sales story and the zero-network demo path.
  - `navigate()` + `_claude_messages()`: the original Anthropic agent loop, still present and tested but not in the default chain (account has no credits). `NAVIGATOR_MODEL` env, default `claude-haiku-4-5-20251001`.
  - Four tools (all backends): `show_table` (live crosstab — arbitrary stub × banner × subgroup filter, auto-nets, means — via the same `crosstab()` engine as the deck), `query_data` (read-only DuckDB SELECT against `data.parquet`, keyword-guarded sandbox), `compare_tables` (fetch precomputed deck tables), `navigate_to` (point at a tab).
- **`api/routes/chat.py`** — `POST /studies/{id}/chat` + history. Chain: `navigate_groq` → on `BackendUnavailable`/`ModelOutputError` → `navigate_basic` (with a question-relevance-trimmed context, `_basic_context()`, since a 150-var context drowns a 3B model) → on failure → graceful message. Context from `datamap.json` (persisted per run; pre-persistence studies degrade to SQL-only) + `crosstabs.json` + default banner. **Study files live in the orchestrator run dir (`Path(study.excel_path).parent`), NOT `STUDIES_DIR/{study_id}`** — the latter holds only the uploaded source; an earlier draft pointed there and every tool silently reported "not available."
- **Frontend** — `ChatBot.tsx`, floating panel on `ProjectPage`, renders tool results inline (tables with sig letters, SQL grids), persists history per study.
- **To enable the smart tier:** add `export GROQ_API_KEY=...` to `~/.zshrc`, restart the backend from an interactive shell. Without it, the local basic mode answers (verified: datamap question answered in 14s at ₹0). Advanced/local-scale path (data-residency clients): rent a GPU — RTX 3090 ~$0.13/hr or India-based JarvisLabs — and point Ollama at a 70B tool-use model; that's task #9.
- Unit tests in `tests/unit/test_navigator.py`: Groq loop, Anthropic loop, basic single-shot, live table computation, subgroup filters, SQL write-guard — all with mocked models, none hit a real API.

**No scipy dependency** (removed 2026-07-05): `stats.py`'s `z_critical` used `scipy.stats.norm.ppf` — the only scipy call in the codebase, dragging in a 30MB native package that repeatedly half-installed and broke imports on the 8GB dev machine. Replaced with `_norm_ppf()`, a pure-Python Acklam probit accurate to ~1e-9 (verified exact to 6 decimals against known z at 90/95/99%). If a filesystem hiccup corrupts the venv again (symptom: `ModuleNotFoundError` for a submodule of an installed package, or `uninstall-no-record-file`), the fix is `rm -rf .venv && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt` — in-place `--force-reinstall` fails once RECORD files are gone.

---

## 5. Orchestration: the pipeline

`src/orchestration/orchestrator.py`'s `StudyOrchestrator.run_study()` is the single place the whole flow is chained:

```
1. CLASSIFY   — src/intake/classify.py: rule-based fast paths for .sav/.parquet/.xlsx
                (binary formats — a text preview is useless to an LLM) and wide-header
                CSVs; falls back to Ollama only for genuinely ambiguous files.
2. LOAD DATA  — load_study_data() dispatches to the right adapter by extension.
                Loaded ONCE; QC and every downstream engine reuse the same frame.
3. QC         — sample size, variable count, missingness. High-missingness on survey
                exports is summarized as ONE warning ("N of M columns >50% empty —
                often skip logic"), not N separate issues — real surveys are legitimately
                sparse from routing logic, this isn't a data quality bug most of the time.
4. CODE       — if any TEXT variables exist, run verbatim coding (§4.3).
5. TABLES     — load or auto-generate (§4.1) a tab plan, run crosstabs.
6. RENDER     — Excel + PowerPoint decks (src/renderers/).
7. DELIVER    — approval gate; once approved, share link becomes servable (§7.3).
```

`src/orchestration/approvals.py`'s `ApprovalGates` define auto-pass thresholds (classify auto-approves above 95% confidence, QC auto-approves if it passed clean) — everything else waits for a human. `src/orchestration/playbooks.py` wraps common configurations (`fast_intake` = auto-approve everything, `standard_intake` = auto-approve classify/QC but always require delivery sign-off, `strict_intake` = human approves every gate). The web API currently always uses the `standard_intake`-equivalent config (see `api/config.py`'s `AUTO_APPROVE_*` env vars).

---

## 6. Backend (FastAPI) — `api/`

- **`main.py`** — app setup, mounts `auth`, `studies`, `public` routers under `/api`, serves the built React app from `web/dist` if present.
- **`config.py`** — env-driven settings. `JWT_SECRET` persists to a gitignored `.jwt_secret` file (so `--reload` during dev doesn't invalidate every session). `ADMIN_EMAIL`/`ADMIN_PASSWORD` bootstrap the first Owner account.
- **`db.py`** — SQLAlchemy models: `StudyDB` (note the column is `study_metadata`, not `metadata` — that name is reserved by SQLAlchemy's declarative API), `UserDB`, `ShareLinkDB`. Helper functions (`create_study`, `approve_gate`, `create_share_link`, etc.) follow one pattern throughout: **JSON columns only persist on reassignment of a new object** (`approval_chain = dict(study.approval_chain or {}); approval_chain[gate] = ...; study.approval_chain = approval_chain` — mutating in place silently doesn't save). This bit the project once already; if a new JSON-column feature seems to "not save," check this first.
- **`auth.py`** — JWT (HS256, 7-day expiry), bcrypt password hashing (pinned to `bcrypt==4.0.1` — newer bcrypt breaks passlib's version probe), `UserRole` StrEnum, `require_role(*roles)` dependency factory.
- **`routes/studies.py`, `routes/auth.py`, `routes/public.py`** — see §7 for the route list and the role table.

### 6.1 Two Pythons — don't mix them up
The backend server runs on **system Python** (`python -m uvicorn ...`, no venv activation). Tests and lint run on **`.venv/bin/python`**. When adding a dependency, install it in both: `pip install X && .venv/bin/pip install X`. This split exists because it's how the project happened to get set up, not by design — worth unifying eventually, but until then, forgetting one side is the single most common "works in tests, crashes the server" bug in this repo.

### 6.2 Upload handling
Files stream to disk in 1MB chunks (`await file.read(chunk_size)` loop) — **never buffer the whole upload in memory.** This was a real bug: the original `content = await file.read()` implementation held a multi-GB file entirely in RAM (twice, once for the buffer and again when pandas/pyreadstat loaded it) on an 8GB-RAM dev machine. Verified byte-identical on a 48MB test file after the fix. The frontend (`NewProjectPage.tsx`) shows live upload progress via axios `onUploadProgress`.

---

## 7. Auth & sharing model

Two audiences, two mechanisms, deliberately kept separate:

### 7.1 Internal accounts (JWT, roles)
| Action | Owner | Analyst | Reviewer |
|---|---|---|---|
| Upload / view tables & reports | ✅ | ✅ | view only |
| Approve classify / QC gates | ✅ | ✅ | ✅ |
| **Sign off for delivery** | ✅ | ❌ | ❌ |
| **Create/revoke client share link** | ✅ | ✅ | ❌ |
| Manage users (`/admin`) | ✅ | ❌ | ❌ |
| Archive project | ✅ | ❌ | ❌ |

Enforced server-side via `require_role()` on each route — not just hidden UI buttons. `approved_by` on every approval comes from the authenticated JWT user (`get_current_user`), never from client-supplied data — a request body `approved_by` field would let anyone claim to be anyone, so the endpoint ignores any such field and fills it server-side.

### 7.2 Public share links (no login)
`POST /studies/{id}/share` (Owner/Analyst) creates a token (`secrets.token_urlsafe(24)`). `api/routes/public.py` has **zero auth imports by design** — it's meant to be auditable at a glance as "nothing in this file can leak internal data to someone without a valid token." Two rules:
1. The public payload (`GET /public/studies/{token}`) is deliberately thin: `client_name`, `status`, `created_at`, `excel_available`/`pptx_available`. Never QC issues, approval history, or any internal metadata.
2. Public downloads only serve when `study.status` is `approved_for_delivery` or `delivered` — a client can never receive a report that hasn't been through Owner sign-off, even with a valid link, because the gate is checked at request time, not link-creation time.

Frontend: `/share/:token` renders `SharePage.tsx` **outside** the authenticated `Shell` — no sidebar, no internal nav, DVV-branded standalone page.

---

## 8. Frontend (React + Vite + Tailwind) — `web/`

Design reference: Forsta/Decipher-style enterprise UI — navy top bar (`#1f2a44` = `primary-500` in `tailwind.config.js`), left rail nav, white cards on slate-100 background. Not flashy; the data is the product.

- **`App.tsx`** — routing. `/login` and `/share/:token` render outside `<Shell>`/`<RequireAuth>` (no login needed for either); everything else is wrapped.
- **`auth/AuthContext.tsx`** — `useAuth()` hook, JWT in localStorage, `api/client.ts`'s axios interceptor attaches `Authorization: Bearer` and bounces to `/login` on 401.
- **`ProjectPage.tsx`** — the core workspace. Tabbed: Overview (pipeline status, QC), **Tables** (the actual interactive crosstab viewer — sig letters as hoverable superscripts, base-row toggle, NET rows, means), Reports (downloads), Activity (approval timeline). Share-link creation lives here too.
- **`CrosstabTable.tsx`** — renders the `CrosstabsPayload` JSON (written by `src/engines/tabs/runner.py`'s `deck_to_json()` and served by `GET /studies/{id}/crosstabs`) as an HTML table matching the Excel deck's visual language.
- **`AdminPage.tsx`** — real user list/creation (Owner-only), backed by `authApi`.
- Vite dev proxy (`vite.config.ts`) forwards `/api/*` to `localhost:8000` — the frontend always talks to `/api`, never a hardcoded host, so this works identically in dev and behind a reverse proxy in prod.

---

## 9. Current status (as of 2026-07-04) — read this before assuming anything is or isn't done

**Built and verified working end-to-end, live, in the browser, against a real 519-column Decipher export:**
- SPSS + CSV + Excel adapters, canonical model, QC, auto-tables, RIM weighting, significance testing, Excel + PPT rendering
- Verbatim coding pipeline (codeframe generation + batch apply + confidence gating) — runs, but no review-queue UI
- Full orchestrator with approval gates
- FastAPI backend, SQLite persistence, streaming uploads with progress
- JWT auth with 3 roles, server-enforced
- Public client share links, revocable, gated on delivery status
- React SPA: dashboard, project list/search, project workspace with live interactive crosstab viewer, login, admin, share page
- 50 automated tests (unit + integration), `ruff` lint clean, `tsc` clean

**Explicitly NOT built — don't assume these exist:**
- Anthropic API credits — the navigator (§4.4) and PPT narratives return graceful fallbacks until the account is funded
- Datamap-merge for Excel/CSV imports (labels currently show as raw codes for non-SPSS files) — top of backlog, see §3.2
- Verbatim coding review-queue UI (backend logic exists, `CodingPage.tsx` is a landing page)
- Remote/hosted deployment — everything currently runs on localhost only; share links only work on whatever machine is running the servers
- PostgreSQL / multi-tenant DB (still SQLite, single-tenant)
- Survey authoring / data collection (Phase 3+ in the original plan, not started)
- Password-change UI (the seeded admin password `changeme123` should be rotated but there's no in-app flow to do it yet)

---

## 10. Conventions specific to this project

(These sit on top of, not instead of, the general conventions in the parent `~/Desktop/AI/CLAUDE.md`.)

- **Tests:** `.venv/bin/python -m pytest tests/ -q` — must stay green (50 tests as of this writing). `tests/unit/` for engines, `tests/integration/` for cross-component flows (orchestrator, SPSS round-trip, deck rendering).
- **Lint:** `.venv/bin/ruff check src/ api/` — `pyproject.toml` ignores `B008` for `api/**` only (FastAPI's `Depends(...)`-in-default-argument idiom, which ruff otherwise flags).
- **Frontend type-check:** `cd web && npx tsc --noEmit`.
- **Before claiming a fix works:** hit the actual running server/browser, not just "the code looks right." This codebase has already had multiple bugs (Vite entry point location, SQLAlchemy JSON mutation, OAuth2 form vs Basic auth) that passed a read-through but failed on first real use. Verify live.
- **Model division of labor:** Claude Fable (or whichever top-tier model is directing) does architecture, planning, and verification; a cheaper model (Haiku) executes well-specified builds from a written plan, to control token cost on mechanical work. When delegating, the plan file must be complete enough that the builder doesn't need to re-derive decisions — and the directing model must actually test the output live afterward, not trust the builder's self-report.
- **Commit messages:** explain *why*, reference the real bug/root-cause when fixing one, not just what changed.

---

## 11. Quick start

```bash
# Backend (system python, not venv)
cd /Users/deepeshsharma/Desktop/AI/dvv-research-cloud
python -m uvicorn api.main:app --reload --port 8000

# Frontend (separate terminal)
cd web && npm run dev   # → http://localhost:3000

# Tests + lint (use .venv here, not system python)
.venv/bin/python -m pytest tests/ -q
.venv/bin/ruff check src/ api/
cd web && npx tsc --noEmit
```

Seeded login: `deepsharma616@gmail.com` / `changeme123` (Owner role — **rotate this**).

---

## 12. Roadmap (priority order as of this writing)

1. Datamap-merge for CSV/Excel imports (kills the "raw codes instead of labels" gap — highest-leverage fix, since most real client files won't be SPSS)
2. Fund the Anthropic API account (unblocks navigator §4.4 + PPT narratives)
3. Verbatim coding review-queue UI
4. Hosting (so share links work outside localhost — this is what makes the "send a client a link" pitch actually real)
5. PostgreSQL + multi-tenant (org-scoped row-level security)
6. Password-change / self-service account UI
7. Remote Ollama (Vast.ai GPU box) for production-scale local-model throughput

---

## Related docs (older, may be superseded by this file where they conflict)
- `../MR startup/decipher-qualtrics-replication-research.md` — original market research
- `../MR startup/dvv-research-cloud-brain-architecture.md` — original full-system architecture spec (Postgres multi-tenant, six-engine vision) — this file (`CLAUDE.md`) reflects what's *actually built* as of 2026-07-04; the architecture doc reflects the *original full vision*, some of which is still ahead (see §12).
- `../MR startup/decipher-qualtrics-full-feature-map.md` — feature-by-feature competitive comparison

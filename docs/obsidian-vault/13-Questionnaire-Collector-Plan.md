# Questionnaire Ingestion + Survey Programming ("Collector") — Full Plan

**Researched/planned by:** Fable 5, 2026-07-07 · **Status:** Plan approved for build, tasks queued for Opus 4.8

---

## Key decisions made in this plan (worth knowing, even though not re-litigating)

1. **`SurveyDefinition` is a NEW model, separate from `DataMap`** — DataMap describes collected data; SurveyDefinition describes the instrument. They join via an explicit `QuestionVariableLink` table, never by mutation. Same model whether a survey was extracted from an old Word doc or authored fresh in DVV — this is the strategic unification that makes "migrate off Decipher" a first-class path.

2. **SurveyJS Form Library (MIT, free) for rendering — explicitly NOT the paid Survey Creator.** DVV authors surveys in its own React builder writing DVV's own `SurveyDefinition`; a server-side compiler translates to SurveyJS JSON only at publish time. Cost: $0. The paid Creator would make SurveyJS's JSON the source of truth instead of DVV's own model — wrong direction.

3. **No new "Questionnaire" navigator specialist.** Extends the existing **Steward** specialist with 2-3 new tools instead (`get_question_details`, `explain_question_base`, `search_questionnaire`) — keeps the token-economy win from the specialist architecture rather than adding a 5th specialist for what's mostly context enrichment.

4. **Expression language is a safe, serializable AST — never raw code.** `{op: "eq"|"in"|"and"|...}`, always paired with the verbatim routing sentence it was derived from. Same language powers ingestion routing, quota cells, and SurveyJS `visibleIf` compilation. Decipher's `<exec>` (arbitrary embedded Python) is an explicit non-goal.

5. **Hallucinated routing logic is the top named risk on real client data**, and it's made *detectable from data*, not just reviewable: every extracted `show_if` gets cross-validated against the actual dataset via DuckDB (non-missing rows of the linked variable should be a subset of rows satisfying the routing condition). Mismatches get flagged `ROUTING_DATA_MISMATCH`.

6. **v1 scope is deliberately small** — explicitly OUT: loops/dynamic blocks, MaxDiff/Conjoint, multi-language, SMS/offline fielding, panel S2S APIs (redirect-only, matching how most agency traffic actually runs), incentives, custom themes. Matches Decipher's own quota-vs-effort tradeoffs, not a compromise unique to DVV.

7. **Status codes match industry convention exactly** (Lucid/Cint): complete=10, terminate=20, security=30, overquota=40 — so agency-supplied vendor redirect URLs work without translation.

Full research citations (Forsta/Decipher Word importer behavior, XML scripting primitives, Qualtrics Survey Flow model, SurveyJS licensing, LLM extraction failure modes, panel redirect protocols) are in the original planning transcript — ask if you want the source list surfaced again.

---

## Task Queue (15 tasks, 2 phases)

### Phase A — Questionnaire Ingestion

| # | Task | Size | Depends on |
|---|---|---|---|
| T1 | `SurveyDefinition` core model | Medium | none — **do first** |
| T2 | Expression evaluator + SurveyJS compiler | Medium | T1 |
| T3 | Questionnaire document intake (docx/PDF → segmented text) | Medium | none — parallel with T1/T2 |
| T4 | LLM extraction pipeline (text → draft SurveyDefinition) | **Large** | T1, T3 — **biggest de-risking task** |
| T5 | Question↔Variable linker + routing/data cross-validator | Medium-Large | T1, T4 |
| T6 | Persistence + API for questionnaire lifecycle | Medium | T4 |
| T7 | Review & correction UI | Large | T6 |
| T8 | Navigator enrichment (Steward tools) | Medium | T5, T6 |

### Phase B — Collector (survey programming & fielding)

| # | Task | Size | Depends on |
|---|---|---|---|
| T9 | Respondent runtime: serve + capture | Large | T2, T6 |
| T10 | Quota engine + status/redirect protocol | Medium-Large | T9 |
| T11 | Response materialization → DataMap → existing engines | Medium | T9 — **the actual payoff, prioritize over T12** |
| T12 | Authoring UI v1 (question builder) | Large | T1, T2, T6 |
| T13 | Field monitor dashboard | Medium | T10 |
| T14 | Fielding quality flags → Steward QC | Small | T11 |
| T15 | End-to-end pilot + hardening (launch gate) | Medium | T7, T10-T13 |

**Parallelization for multiple build sessions:** `{T1,T3} → {T2,T4} → {T5,T6} → {T7,T8} ∥ {T9→T10/T11, T12} → T13/T14 → T15`

**Full task descriptions** (each self-contained enough to hand to a fresh Opus 4.8 build session with zero prior context) are in the original plan — see task tracker for the working copy used during builds.

---

## Post-Phase-A Production Review (2026-07-07) — first real client questionnaire

**Reviewed by:** Fable 5, after Phase A (T1–T8) shipped and was run against a real 80-question client doc on Render. All findings below are confirmed from that run, not hypothetical. This section appends to — does not rewrite — the plan above.

### What happened in production

1. **All 80 questions came back `EXTRACTION_UNAVAILABLE`.** Root cause: `ANTHROPIC_API_KEY` was never set in the Render dashboard (it's `sync: false` in `render.yaml` — a manual post-deploy step that was missed). The pipeline behaved exactly as designed ("failure-tolerant, flag and continue") and that design made the outage *worse*: `extract_question`/`verify_question` in `src/engines/questionnaire/extractor.py` catch `BackendUnavailable`/`ModelOutputError`/`KeyError` and discard the exception — and `src/ai/router.py` line ~178 raises `BackendUnavailable("ANTHROPIC_API_KEY not set")`, so the discarded message literally named the root cause. The reviewer saw 80 identical opaque flags; diagnosis required reading Render env vars by hand. The run also burned 160 sequential doomed LLM attempts (extract + verify per chunk) with no circuit breaker.
2. **Render free tier has no persistent disk** (per `render.yaml`'s own comment): `/app/studies` — where T6 writes every questionnaire JSON — is wiped on every deploy/restart. Every uploaded, hand-reviewed, approved questionnaire is currently disposable in production.
3. **Duplicate qid validation errors (S6, S7)** appeared from the real doc's screener section — the loader's regex chunking (`_QID_RE` in `src/intake/questionnaire_loader.py`) produced two chunks claiming the same qid.

### Assessment of the four review questions

**Q1 — Error-swallowing in `extractor.py`: adequate for production? No.** The "flag and continue" *shape* is right (partial draft beats crash on question 40), but discarding the exception detail is not. Scope note: this is confined to `extractor.py` — `linker.py` is fully deterministic (duckdb + rapidfuzz, zero LLM calls), no change needed there. What's needed:

- Preserve `type(exc).__name__` + `str(exc)` per failed chunk (a `failure_reason` alongside the flag — e.g. in `Question.validation` or `ExtractionResult`), surfaced in the review UI payload and via T8's Steward tools.
- Split the flag taxonomy by actionability: config error (`ANTHROPIC_API_KEY not set` — one fix, one place) vs. transient backend outage (retryable) vs. model-rejected-input (a real document problem). Router already distinguishes these in its messages; the extractor just has to stop throwing them away.
- **Document-level circuit breaker:** if the first N chunks (say 3) all fail with `BackendUnavailable`, abort the run and set the questionnaire row to `error` with the reason — an 80/80 failure is one root cause, not 80 review items, and shouldn't cost 160 API attempts to discover.
- Startup/health preflight reporting which LLM keys are configured, so a missing key is caught at deploy, not at first client upload.

Verdict: **small dedicated task (T16 below)**, not a fold-in — every not-yet-built task it could fold into is Phase B, and this blocks trusting any further Phase A production use.

**Q2 — Ephemeral disk vs. Phase B / T9: T9 must NOT proceed as scoped.** This is not "fix the infra first" (buy a disk) — it's the cleaner reframe: **build T9 DB-backed from day one.** Postgres is already provisioned (`dvv-postgres` in `render.yaml`) and already holds users/studies/questionnaire metadata. Three separate reasons the disk answer is wrong for Phase B even on a paid plan:

- Live respondent responses lost on redeploy is unrecoverable, reputation-level damage — nothing else in the system has that property.
- T10's quota state needs *atomic concurrent increments* (two respondents hitting a quota cell simultaneously). JSON-on-disk was never going to survive that; Postgres row-level locking is the natural fit. The disk finding just forces the right call earlier.
- Mid-interview resume/partial capture implies per-answer writes; a DB row per session is the honest model anyway.

T6's JSON-on-disk choice for instrument definitions was reasonable *by convention* but is invalidated *by this deployment*: an approved 80-question questionnaire embodies hours of human review labor, which is what actually gets lost on restart. Fix is cheap (T17 below) because `SurveyDefinition.to_json()/from_json()` already exist — persist the blob in a Postgres text/JSONB column on the existing `QuestionnaireDB` row instead of `definition_path`. This should land **before** any more real-document review sessions, and definitely before T9. Waiting until T15 is wrong: by then review labor and pilot response data would both have been lost at least once.

**Q3 — v1 non-goals: all still right.** Nothing today implicates loops, MaxDiff/Conjoint, multi-language, SMS/offline, S2S APIs, incentives, or themes — today's failures were ops and error-reporting, not scope. The one *implicit* assumption the non-goals list never named — "the filesystem persists" — is the assumption today falsified. Corrected via T17/T9-rescope, not via scope changes.

**Q4 — Other flaws the real doc exposed (found in this review, fresh eyes on `api/routes/questionnaire.py`):**

1. **Review-API deadlock on duplicate qids (confirmed live-fireable by today's doc).** `PATCH .../questions/{qid}` re-validates the *whole* survey and 422s if `validate()` returns *any* problems — including pre-existing ones like the doc's own `duplicate qid: S6`. And `_apply_update` doesn't allow editing `qid` itself. Net effect: a document extracted with duplicate qids can never be repaired through the API — every edit to *any* question is rejected, and the one field that would fix it isn't editable. Fix: make PATCH reject only on *newly introduced* problems (diff the problem sets before/after), and allow qid renames (updating block `question_ids` and `approved_qids` consistently).
2. **Blocking flags are unclearable → `approve-all` is unreachable.** Flags are deliberately never cleared on edit (reasonable), but `approve-all` hard-blocks on `_BLOCKING_FLAGS` regardless of per-question approval — so a question flagged `EXTRACTION_UNAVAILABLE` or `AMBIGUOUS_ROUTING` can be hand-fixed, individually approved, and *still* block the questionnaire forever. Per-question approval should count as flag resolution (or add an explicit resolve action).
3. **Duplicate qids silently corrupt `SurveyDefinition._index`** (`__post_init__` dict comprehension — last duplicate wins), so GET/PATCH-by-qid silently addresses the wrong question. Compounding #1.
4. **Extraction thread has no survival story.** 80 chunks × 2 sequential LLM calls on a daemon thread; a Render restart mid-run (free tier restarts are routine) kills the thread and leaves the row on `processing` forever — the `error` flip only fires on an in-process exception. Needs at least a staleness timeout; a resumable job is T9-adjacent work.
5. **S6/S7 duplicate qids — judgment call: the system worked.** `validate()` catching a genuine ambiguity in a messy screener is the designed behavior, and the LLM extract stage never even ran on this doc, so the loader can't be fully exonerated or convicted yet. Verdict: don't harden `_QID_RE` speculatively; re-run this doc after T16 lands and keys are set, and only open a loader task if duplicates persist with real extraction. The *urgent* part of the duplicate-qid story is item #1 (the repair deadlock), not the segmentation.

### Task-queue changes

New tasks, in the same self-contained style as the Phase A/B tables. Sequence: **T16 + T17 before anything else in Phase B; T18 before the next real review session; T9 re-scoped as below.**

| # | Task | Size | Depends on |
|---|---|---|---|
| T16 | Extraction failure diagnostics + circuit breaker | Small-Medium | none — **do first** |
| T17 | Questionnaire persistence → Postgres | Small | none — parallel with T16 |
| T18 | Review-API repair paths (delta validation, qid rename, flag resolution) | Medium | T17 preferred (edit the DB copy) |
| T9′ | Respondent runtime — **re-scoped: Postgres-backed from day one** | Large | T2, T6, **T17** |

**T16 — Extraction failure diagnostics + circuit breaker (Small-Medium).** In `src/engines/questionnaire/extractor.py`: stop discarding exceptions in `extract_question`/`verify_question` — record `{"error_type": type(exc).__name__, "error_detail": str(exc)}` on the result (suggested home: `Question.validation["extraction_error"]`, matching the existing `routing_verbatim` stash pattern) and include it in `_extraction_summary` (e.g. `failure_reasons` counter keyed by error_type). Distinguish flags by actionability: keep `EXTRACTION_UNAVAILABLE` for transient `BackendUnavailable`, add `EXTRACTION_CONFIG_ERROR` for config-shaped failures (router message contains "not set" / missing-key), keep `ModelOutputError` distinct as model-rejected-input. In `extract_survey_definition`: circuit-break — if the first 3 chunks all fail with `BackendUnavailable`, raise instead of continuing, so `api/routes/questionnaire.py::_run_extraction`'s existing except-path flips the row to `error` (extend `update_questionnaire_status` to store an `error_detail` string shown by the status endpoint). Add a preflight to the health/startup path reporting which of `GROQ_API_KEY`/`OPENAI_API_KEY`/`ANTHROPIC_API_KEY` are present (booleans only, never values). Surface `error_detail` through `_question_payload` and through T8's Steward navigator tools. Tests: mock `run_task` raising `BackendUnavailable("ANTHROPIC_API_KEY not set")` → expect config flag + detail preserved + circuit break after 3 chunks; mock `ModelOutputError` → per-chunk flag, no circuit break.

**T17 — Questionnaire persistence → Postgres (Small).** Replace the `definition_path` file convention in `api/routes/questionnaire.py` with a `definition_json` TEXT (or JSONB) column on the existing `QuestionnaireDB` model in `api/db.py`. `SurveyDefinition.to_json()/from_json()` already exist, so `_load_definition`/`_save_definition` become column reads/writes; keep `definition_path` writing as an optional local-dev artifact or drop it. Keep the uploaded source .docx/.pdf ephemeral (acceptable loss — it's the client's file, re-uploadable; the *review labor* in the JSON is what must survive). Migration: on read, if the column is empty but a path exists on disk, backfill. Rationale: Render free tier wipes `/app/studies` on every deploy/restart; an approved questionnaire embodies hours of human review. Do NOT extend this to `datamap.json`/crosstabs in this task — that's a separate decision with its own tradeoffs.

**T18 — Review-API repair paths (Medium).** In `api/routes/questionnaire.py`: (a) PATCH should compute `validate()` before and after the edit and 422 only on *newly introduced* problems — today a doc-level pre-existing error (e.g. `duplicate qid: S6` from a real screener) blocks every edit to every question; (b) allow `qid` rename via PATCH, updating `Block.question_ids`, `approved_qids`, and any `show_if`/`terminate_if` expressions referencing the old qid — this is the only way to *fix* a duplicate-qid extraction; (c) rebuild `SurveyDefinition._index` after mutation (duplicate qids currently make `__getitem__` silently return the last duplicate); (d) flag resolution: either clear a question's `_BLOCKING_FLAGS` members on individual approve, or add `POST .../questions/{qid}/resolve-flag` — today `approve-all` is unreachable for any doc that ever had a blocking flag, because flags are never clearable; (e) staleness guard: a questionnaire stuck on `processing` longer than N minutes (thread killed by a restart) is reported as `error` by the status endpoint. Tests: dup-qid fixture → rename via PATCH succeeds and un-blocks other edits; approved question with `EXTRACTION_UNAVAILABLE` no longer blocks approve-all.

**T9′ re-scope (replaces T9's storage assumption; size stays Large).** Respondent sessions and answers are Postgres rows from the first commit — `RespondentSession` (id, survey id+version, status code, embedded/url params, timestamps) and per-answer or per-session-JSONB answer storage, written incrementally as the respondent progresses (enables resume + partial capture). No JSON-on-disk for anything a respondent produces. This also pre-positions T10: quota counters become transactional Postgres updates (row-level locking), not file state. T11 reads responses from the DB when materializing to DataMap. Everything else in T9's original scope (SurveyJS serving via the T2 compiler, status/redirect codes 10/20/30/40) is unchanged.

**No-change calls:** T12–T15 stand as scoped (T15 inherits the env-preflight from T16 as a launch-gate check). The v1 non-goals list stands unchanged. `questionnaire_loader.py` segmentation: no hardening task yet — re-test after T16 with keys configured; open one only if duplicate qids reproduce alongside real extraction output.

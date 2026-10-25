# Build queue

The tasks the builder agents take from, in waves. Design and screens are in
`docs/artifacts/2026-10-25-build-blueprint.html`. A task is **ready** when all of
its `needs` are merged. Every task runs in its own worktree under `~/dev`. Builder
agents never commit: the orchestrator verifies (full pytest with AI keys empty,
ruff, vitest, `npm run build`, and a browser check for UI work), then commits,
opens the PR, waits for CI and merges.

Rules for every task:
- No new services and no new Python or npm dependencies unless the card says so.
- Tests run on SQLite. Postgres-only paths get a test that is skipped unless
  `DVV_TEST_PG` names a database.
- Every new table goes in `api/db.py`, placed after `ProjectChunkDB` under a
  `# --- <task id> ---` comment. `Base.metadata.create_all` provisions it; columns
  added to an existing table go through `_ensure_new_columns`.
- Errors shown to users are plain sentences. A raw exception never reaches the browser.
- Client text goes only to Bedrock or a local model (`router.run_private`).

| Status | Meaning |
|---|---|
| ready | can start now |
| running | an agent has it (worktree named) |
| review | built, orchestrator verifying |
| done | merged (PR number) |
| blocked | waiting on `needs` |

---

## Wave 1: foundations

### F1: Job queue, worker and workflow runner  · platform-builder · done (#157)
**Why:** background work runs in FastAPI `BackgroundTasks` today. If the server
restarts, those jobs are lost, and they can't be retried or seen.
**Files:** `api/db.py` (JobDB, WorkflowRunDB), `src/platform/__init__.py`,
`src/platform/jobs.py`, `src/platform/worker.py`, `src/platform/workflows.py`,
`tests/unit/test_platform_jobs.py`, `tests/unit/test_platform_workflows.py`.
**Tables:**
- `jobs`: id (str pk, "job_<hex12>"), kind, study_id (nullable, indexed),
  payload (Text json), state (queued|running|done|failed|cancelled, indexed),
  attempts (int 0), max_attempts (int 3), run_after (str ISO UTC), locked_by,
  heartbeat_at, result (Text json), error (Text), workflow_run_id (indexed), step,
  created_by, created_at, finished_at.
- `workflow_runs`: id ("wfr_<hex12>"), workflow, study_id, state
  (running|done|failed|cancelled), inputs (Text json), started_at, finished_at, error.
**API (`src/platform/jobs.py`):**
- `HANDLERS: dict[str, Callable[[dict, Session], dict]]` and the decorator `handler(kind)`.
- `enqueue(db, kind, payload, *, study_id=None, max_attempts=3, delay_s=0, workflow_run_id=None, step=None, created_by=None) -> JobDB` (commits).
- `claim(db, worker_id) -> JobDB | None`. On Postgres: `SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1`, oldest first, `run_after <= now`. On SQLite: pick the oldest candidate, then `UPDATE jobs SET state='running' ... WHERE id=:id AND state='queued'`, and succeed only if rowcount == 1.
- `heartbeat(db, job_id)`, `complete(db, job_id, result: dict)`, `fail(db, job_id, error: str)`. A failure with attempts < max_attempts goes back to queued, with `run_after = now + 30 s · 2^attempts`; otherwise it is marked failed.
- `reap(db, stale_s=300) -> int`: running jobs whose heartbeat is older than stale_s go back to queued.
- `run_one(db, job) -> None`: looks up the handler, runs it, then calls complete or fail. An unknown kind fails with "No handler for <kind>". A handler exception is stored as `f"{type(e).__name__}: {e}"`, truncated to 500 characters.
**Worker (`src/platform/worker.py`):** `run_once(session_factory, worker_id) -> bool`
(True if it ran a job); `main()` loops: reap every 60 s, sleep 1 s when idle, stop
cleanly on SIGTERM/SIGINT. Run with `python -m src.platform.worker`. A heartbeat thread
updates every 30 s while a job runs.
**Workflows (`src/platform/workflows.py`):**
- `@dataclass(frozen=True) Step(name: str, kind: str, after: tuple[str, ...] = ())`;
  `@dataclass(frozen=True) Workflow(name: str, steps: tuple[Step, ...])`. Validation
  raises ValueError on unknown `after` names, duplicates or cycles.
- `WORKFLOWS: dict[str, Workflow]` and `register_workflow(wf)`.
- `start(db, workflow_name, study_id, inputs: dict, created_by=None) -> WorkflowRunDB`: enqueues every step with no `after`. Each step's payload is `{"inputs": inputs, "results": {dep_name: dep_result}}`.
- `advance(db, run_id) -> None`: called by `run_one` after a job with a `workflow_run_id` finishes. It enqueues each step whose deps are all done and that has no job yet. When every step is done, the run is done. When any step has failed for good, the run is failed and the step's error is copied to the run.
- `cancel(db, run_id)`: queued jobs are cancelled, and the run is cancelled.
**Acceptance (tests):** enqueue → claim → complete; two `claim` calls never
return the same job; retry backoff and final failure; reap returns a dead job to
the queue; unknown kind fails cleanly; a three-step diamond workflow (A → B, A → C,
B+C → D) runs to done through `run_once`, with D's payload carrying B's and C's
results; a failing step fails the run and leaves D unqueued; cancel; validation
rejects a cycle.

### F2: Study graph  · platform-builder · done (#158)
**Why:** the Study Thread (lineage) and "what would this change" (impact).
**Files:** `api/db.py` (GraphNodeDB, GraphEdgeDB), `src/platform/graph.py`,
`tests/unit/test_platform_graph.py`.
**Tables:**
- `graph_nodes`: id (str pk = f"{study_id}:{kind}:{ref}"), study_id (indexed), kind, ref, label, props (Text json), version (int 1), created_at, updated_at.
- `graph_edges`: id (str pk = f"{src}|{kind}|{dst}"), study_id (indexed), src (indexed), dst (indexed), kind, props (Text json), created_at.
**Direction:** an edge points from the thing that depends to the thing it comes
from: `cell —computed_from→ variable`, `sentence —cites→ cell`.
**API:**
- `node_id(study_id, kind, ref) -> str`.
- `upsert_node(db, study_id, kind, ref, label="", props=None) -> str`: insert, or update the label and props and bump the version when they changed. No commit, so callers batch.
- `link(db, study_id, src, dst, kind, props=None) -> str`: idempotent.
- `unlink_kind(db, study_id, src, kind)` removes a node's outgoing edges of one kind, for rewiring.
- `lineage(db, study_id, node, max_depth=12) -> list[dict]` walks upstream. `impact(...)` walks downstream. Each returns `{"id","kind","ref","label","props","depth","via"}`, with each node once at its shallowest depth, ordered by depth then id. Both use one recursive CTE (`sqlalchemy.text`) that runs on SQLite and Postgres, depth-limited and safe on cycles.
- `delete_study_graph(db, study_id) -> int`.
**Acceptance:** an upsert twice gives one row, with the version bumped only on change;
link is idempotent; lineage of a cell through variable → question → data_file
returns depths 1, 2, 3; impact from a question finds a sentence through a cell; a
cycle terminates; max_depth is honoured; other studies' nodes never appear.

### F3: Vector store interface and pgvector  · platform-builder · done (#159)
**Why:** near-duplicate open ends, reusing corrections, norms. Search in
`src/engines/rag/store.py` is a dot product over a project's rows in Python.
**Files:** `src/platform/vectors.py`, `src/engines/rag/store.py` (use the
interface), `api/db.py` (in `_ensure_new_columns`, Postgres only:
`CREATE EXTENSION IF NOT EXISTS vector`, a nullable `project_chunks.embedding
vector(768)` column, and an HNSW index `USING hnsw (embedding vector_cosine_ops)`.
Each step is wrapped so a database without the extension logs one warning and carries on),
`deploy/docker-compose.yml` (both `postgres:16` images → `pgvector/pgvector:pg16`),
`tests/unit/test_platform_vectors.py`.
**API:**
- `class VectorStore(Protocol)` with `search(db, study_id, vector, *, kinds, k, model) -> list[tuple[float, ProjectChunkDB]]` and `write(db, row, vector) -> None` (sets `vector_json` always, plus `embedding` when supported).
- `NumpyStore`: today's behaviour, vectorised with numpy (one matrix product per study).
- `PgVectorStore`: `ORDER BY embedding <=> CAST(:q AS vector) LIMIT :k`, filtered by study_id, kinds and model. The vector is passed as the text literal `'[x,y,...]'`, so no new Python dependency. It is used only when the dialect is postgresql, the column exists, and `len(vector) == 768`.
- `get_store(db) -> VectorStore`, whose choice is cached per engine.
- `near_duplicates(db, study_id, kind="verbatim", threshold=0.95, model=...) -> list[tuple[str, str, float]]`: pairs of refs above the threshold, using numpy on any backend (a project's verbatims fit in memory).
**Acceptance:** the existing rag and copilot tests still pass unchanged;
`NumpyStore` ranks the same as the old loop on a fixed set; `near_duplicates`
finds a planted pair and nothing else; `get_store` returns `NumpyStore` on
SQLite; a `DVV_TEST_PG`-gated test checks the pgvector ordering matches numpy.
**Deploy note for the PR:** the image swap keeps Postgres 16, so the existing data
volume works unchanged. The founders run `./dvv upgrade` as usual.

---

## Wave 2: wiring

### F4: Background work on the queue, plus live events  · platform-builder · done (#164)
**Why:** coding and translation drafts still run in FastAPI `BackgroundTasks`, so a
restart loses them. The control room and "Needs you" need a live stream.
**Files:** `api/db.py` (EventDB), `src/platform/events.py`, `src/platform/jobs.py`
(JobError), `src/platform/worker.py` (`start_background`), `api/main.py` (start it),
`api/routes/platform.py` (new) and its `include_router` line, `api/routes/studies.py`
(coding), `api/routes/questionnaire.py` (translation draft), `tests/conftest.py`
(env), `tests/unit/test_platform_events.py`, and the existing coding and translation
route tests.
- **Errors users can read:** add `class JobError(Exception)` in jobs.py. In `run_one`, a `JobError` stores `str(e)`. Any other exception is logged with its traceback and stored as "Something went wrong running this job. It has been logged."
- **events table:** `seq` (Integer pk, autoincrement), study_id (indexed), kind, data (Text json), created_at. Then `emit(db, study_id, kind, data) -> int` (commits, returns seq) and `since(db, study_id, after_seq, limit=200) -> list[dict]`.
- **In-process worker:** `worker.start_background(session_factory) -> threading.Thread` runs the `main()` loop in a daemon thread, stopped by an Event. `api/main.py` starts it at startup unless `DVV_INPROCESS_WORKER=0`, so production needs **no** new service. `tests/conftest.py` sets `DVV_INPROCESS_WORKER=0`, and tests drain the queue with a helper `drain(factory)` that calls `run_once` until it returns False (put it in `src/platform/worker.py`).
- **Handlers:** `coding.run` and `translation.draft` take the bodies of today's background functions. They keep writing the same `study_metadata` job status the web app reads, and add `job_id` to it. They emit `job.done` or `job.failed` events. The routes enqueue instead of `background.add_task`, and keep their 202 and 409 behaviour.
- **Routes** (`api/routes/platform.py`): `GET /api/jobs/{job_id}` (authenticated; a job with a study_id needs the same study access the study routes use) returns id, kind, state, attempts, result and error. `GET /api/studies/{study_id}/events?after=N` returns `text/event-stream` (each event as `id: seq`, `event: kind`, `data: json`). It polls `since()` each second, sends a `: keepalive` comment every 15 s, and closes after 300 s so the client reconnects with `after`. It uses the normal Authorization header, because the web app will read it with fetch streaming, not EventSource.
**Acceptance:** the coding and translation route tests pass after a `drain()`; a job
failing with JobError shows that message, and any other failure shows the generic
one; `since()` returns events after a seq, in order; the SSE route streams an emitted
event (TestClient, read the first lines); `/api/jobs/{id}` is 404 for an unknown id
and 403 without study access.

### G1: The questionnaire writes its graph  · engine-builder · done (#162)
**Why:** "what changes if I edit Q9", and the base the Study Thread starts from.
**Files:** `src/engines/questionnaire/graph_sync.py`, the call sites in
`api/routes/questionnaire.py` (approve-all, and any route that saves an approved
questionnaire), `api/routes/graph.py` (new) and its include line,
`tests/unit/test_graph_sync.py`, `tests/integration/test_graph_routes.py`.
- `sync_questionnaire(db, study_id, survey) -> dict` uses `src.platform.graph`. It writes one `question` node per question (ref qid, label = first 200 characters of the text, props `{type, block}`) and edges from the question to the questions it depends on:
  - `shown_if` for each qid in show_if;
  - `terminates_on` for each in terminate_if;
  - `masked_by` for each mask's source_qid (and exclude_qid);
  - `piped_from` for each pipe's source_qid;
  - `derived_from` for each approved derive rule's sources.
- Before writing, it calls `unlink_kind` for each kind, and removes question nodes no longer in the survey (and their edges), so a re-approve leaves the graph exactly in step. It returns `{nodes, edges}`.
- It is called after approve-all commits. A graph failure is logged and never fails the approval.
- **Routes:** `GET /api/studies/{study_id}/graph/lineage?node=<kind>:<ref>` and `/graph/impact?node=...`. Both are authenticated with study access, return the `lineage()` or `impact()` list, and give 404 for an unknown node.
**Acceptance:** a survey where Q3 is shown if Q2, Q5 is masked by Q4, and Q6 pipes Q5 gives the right edges; impact of Q4 reaches Q5 and Q6; removing Q6 and re-syncing drops its node and edges; approving through the route writes the graph; a broken graph write still approves.

### X1: Paths through the questionnaire  · engine-builder · done (#161)
**Why:** the data the Logic X-ray draws (line thickness, exits, unreachable stations).
**Files:** `src/engines/fielding/dryrun.py`, `tests/unit/test_dryrun_paths.py`.
- During `simulate`, record each synthetic respondent's sequence of SHOWN qids and their disposition.
- Add to `DryRunReport` and `to_dict()`:
  - `transitions`: `[{"from": qid, "to": qid | "END" | "TERMINATE" | "OVERQUOTA", "n": int}]`, sorted by n descending;
  - `paths`: the 50 most common distinct sequences, `[{"qids": [...], "n": int, "disposition": str}]`;
  - `exits`: `[{"qid": qid, "disposition": str, "n": int}]`, the last question shown before a non-complete ending.
- `questions_never_shown` stays as it is: it is the unreachable list.
- No change to the existing fields or their meaning. Memory is bounded: paths are counted with a Counter of tuples, never stored per respondent beyond that.
**Acceptance:** on a three-question survey with a screen-out at Q1 for code 2 and Q3 shown only if Q2 = 1, the transitions, path counts and exits match a hand count at a fixed seed; n = 1,000 runs in under a second; existing dry-run tests pass unchanged.

### A1: Typing and timing evidence from exports  · engine-builder · done (#163)
**Why:** 3 of 5 real exports carry per-open-end keystrokes, characters, pasting and
speech-to-text, and `variable_roles` classes them as system columns and throws them
away. They are the strongest sign that a person did, or didn't, write an answer.
**Files:** `src/engines/qc/paradata.py`, `src/engines/qc/evidence.py` (add a
`paradata` block), `tests/unit/test_paradata.py`.
- `paradata_columns(columns) -> dict[str, dict[str, str]]` maps each open end to its trace columns, by the Decipher naming in `variable_roles._SYSTEM_NAME_RE`. For example `hQ15OE_KeyStrokeCount`, `hQ15OE_CharacterCount`, `hQ15CopyResponse`, `hQ15OE_SpeechToTextText` and `hQ15OE_ManualTyping` give `{"Q15": {"keystrokes": col, "chars": col, "paste": col, "stt": col, "manual": col}}`. Base names match case-insensitively, with the `h` prefix and `OE` and `_` suffixes stripped.
- `paradata_features(df, columns_map) -> pl.DataFrame` has one row per respondent (the `respondent_id` or `record` column, as `code_open_ends` finds it), with columns `n_open_ends_traced`, `n_pasted`, `n_speech`, `max_chars_per_key`, and `pasted_qids` (a comma-joined string).
- A paste is either a CopyResponse value that is truthy, or chars ≥ 20 with keystrokes < chars × 0.3 when no speech-to-text text is present. Speech-to-text counts separately: it is a person speaking, not pasting.
- **Evidence:** the per-respondent evidence endpoint gains `paradata: {pasted: [...], speech: [...], max_chars_per_key}` when the data has traces, and nothing when it doesn't.
**Acceptance:** the TRD2 shape (an answer of 1,168 characters with 1 keystroke) is flagged as pasted; a speech-to-text answer is not; data with no trace columns gives an empty map and no evidence block; Q15 and Q15OE map to the same base.

## Wave 3: screens

Shared rules for every screen:
- Follow the mockup in `docs/artifacts/2026-10-25-build-blueprint.html` (section 1, screens A–E) for layout and copy, using the app's own Tailwind tokens (slate, `primary-*`, `shadow-panel`), `InfoTip`, and `web/src/api/client.ts`.
- **No new npm dependency.** Draw diagrams in plain SVG.
- Each feature gets a `help.json` entry and an InfoTip.
- Each component gets a vitest test; each route gets an integration test.
- Accessibility: every control is reachable by keyboard and visibly focused, and colour is never the only signal (pair it with text or an icon).
- Copy is in sentence case and says what happens. Errors say what to do.

### R0: Lifecycle rail and "Needs you" (screen A)  · ui-builder · done (#168)
**Backend:** `GET /api/studies/{id}/lifecycle` (signed in), from a new `src/engines/lifecycle.py: lifecycle(db, study) -> dict`. It returns `stages`: eight items in the order Brief, Questionnaire, Proof, Field, Verify, Data, Tables, Insight, each `{key, label, state: "done"|"attention"|"todo", detail, tab}`. It also returns `needs_you: [{kind, title, detail, tab}]`. Derive the states only from facts already stored; compute none of them:
- **Questionnaire** is done when approved, attention when draft with flags.
- **Proof** is done when the last dry run had nothing impossible or never-fired, if a result is stored. Otherwise it is todo.
- **Field** shows completes against the target from links and quotas.
- **Verify** is attention when cleaning has unreviewed flagged respondents.
- **Data** is done when a data file is processed. **Tables** is done when tables exist. **Insight** is done when a report or deck exists.
- **Brief** is done when the study has a description or template.

`needs_you` lists each attention item. Each stage's `tab` names the ProjectPage tab or route that opens it.
**Web:** `web/src/components/LifecycleRail.tsx` sits under the project header in `ProjectPage.tsx`. It shows eight segments, each a button that opens its tab, with the state shown by both a top-border colour and a text mark (✓, "needs you", nothing). Below the rail, a "Needs you" list appears when non-empty. It refreshes on the `DATA_CHANGED` event.
**Acceptance:** route tests on a study at three points (new, approved with a link, data processed) give the expected states; the component test shows the states and that a click opens the right tab.

### X2: Logic X-ray (screen B)  · ui-builder · done (#166)
**Web:** `web/src/components/LogicXray.tsx`, rendered in `DryRunPanel.tsx` under the existing results whenever a report has `transitions`.
- **Layout, no library:** stations (questions) sit left to right in questionnaire order (`questions_never_shown` included). Each transition is a path. Adjacent stations are joined by a straight segment on the main line; a transition that skips stations is an arc above the line. TERMINATE and OVERQUOTA exits drop below the line to a labelled exit marker.
- Stroke width is proportional to `n / n_respondents` (minimum 1.5 px). Unreachable stations are drawn dashed in the warning colour with the label "nobody reaches it".
- The SVG scrolls horizontally inside its own container. Station labels are the qids, with the full question text in a `<title>`.
- **Side panel:** the selected station shows its share of respondents and where they go next. Clicking or pressing Enter on a station selects it. The panel lists the unreachable questions and the exits with their counts.
- **Copy:** "Line thickness = share of simulated respondents. Dashed = nobody reaches it."
**Acceptance:** a vitest test on a report fixture checks the station count, that an arc is drawn for a skip, an exit marker for a terminate and dashing for an unreachable question, and that selecting a station updates the panel.

### T1: Study Thread (screen C)  · ui-builder · done (#167)
**Backend:** `GET /api/studies/{id}/tables/{table_id}/provenance?row=<i>&col=<j>` (signed in), in `src/engines/tabs/provenance.py`. It finds the table in the study's processed tables (find where `crosstabs.json` / `deck_to_json` output is stored and read it the same way the Tables view does), and returns `{value, label, column, steps: [...]}`. Each step is `{title, detail}`, in this order, skipping a step when the fact is missing:
1. The cell: value, row and column labels, weighted and unweighted base.
2. The base: the column's filter text and the table's filter.
3. The question: the stub's question text, its type, and its routing, masks and pipes in plain words, from `src.platform.graph.lineage` of the `question` node when it exists, otherwise from the survey.
4. Cleaning: how many respondents were removed and for which reasons, from the cleaning decisions the cleaning engine stores.
5. Weight: its name, plus efficiency = effective base ÷ unweighted base for that column, when weighted.
6. The data file: its name, when it was uploaded and by whom, and its row count.

It writes a `cell` node with `computed_from` (to the question) through the graph, so that wave 4's citations can point at it.
**Web:** `web/src/components/StudyThread.tsx`, a drawer from the right. `CrosstabTable` gets an optional `onCellClick(row, col)`. The ProjectPage table view opens the drawer and highlights the cell. The steps are drawn as a vertical thread of knots, as in the mockup. Escape and a close button dismiss it, and focus returns to the cell.
**Acceptance:** a route test on a processed fixture study returns the steps in order with the right base, and returns 404 for a bad table, row or column; the component tests check the knots render and that Escape closes the drawer.

### A2: Authenticity score and session timeline (screen D)  · ui-builder · done (#169)
**Backend:** `src/engines/qc/authenticity.py` already exists. Read it first, and extend it rather than writing a parallel one. The score is a sum of named signals, each `{key, label, weight, detail}`, built from:
- the paradata evidence (A1);
- speed against the median (`seconds` / `median_seconds`, which evidence already returns);
- straight-lining flags;
- near-duplicate open ends, from `src.platform.vectors.near_duplicates` when the study has a verbatim index. Skip this signal when there is none.

The score is `1 - min(1, Σ weights)`, with weights as module constants and a comment saying they are a starting point, to be calibrated by research task R9. The evidence endpoint returns `authenticity: {score, signals}`.
**Web:** in `CleaningPanel.tsx`'s evidence panel, an "Authenticity 0.22" pill (text, plus colour by band), the signals listed in plain words, and a time-per-question bar strip when per-question times exist (find whether exports carry them; if they don't, show only total time against the median). Pasted questions are marked with text, not colour alone.
**Acceptance:** unit tests check that a pasted, fast respondent scores lower than a typed, median-speed one, and that the signals are listed; the component test shows the pill and the signals.

### C1: Control room (screen E)  · ui-builder · done (#170)
**Web:** `web/src/lib/useStudyEvents.ts` is a hook that reads `GET /api/studies/{id}/events` by **fetch streaming** with the Authorization header the API client uses. It parses SSE frames, reconnects with `after=<last id>` when the stream closes, and backs off on errors (1 s, then 2, 4, up to 30 s). `web/src/components/ControlRoom.tsx` sits on the FieldingPage above the links. It shows:
- quota gauges, from the link quota endpoints;
- completes against the target;
- LOI and incidence when the existing field report has them;
- a supplier table, from the existing vendors report: completes, flagged share, cost.

It refreshes on any event and every 30 s regardless. "Updated N s ago" ticks.
**Backend:** emit a `respondent.complete` event, through `src.platform.events.emit`, from the complete route in `api/routes/fielding.py`. Wrap it so an event failure never fails a complete. Emit `respondent.terminate` from the screen-out path too.
**Acceptance:** a route test checks that completing a session emits an event; the hook test (mock fetch with a ReadableStream) checks frame parsing and that it reconnects with `after`; the component test shows the gauges and the supplier rows.

## Wave 4: insight and learning

Shared rules are as in wave 3. Any model call goes through `src.ai.router.run_private`, and everything must work with no model at all.

### R: Cited report (R1 checker + R2 writer, screen F)  · engine-builder + ui-builder · done (#173)
**Why:** a report sentence is the claim a client repeats. Every sentence must point at the cells it rests on, and code, not a model, decides whether it stands.

**R1 checker:** `src/engines/analysis/citations.py: check(text, cites, tables) -> {"ok": bool, "problems": [str]}`. `cites` is a list of `{table, row, col}`, with row and col as indexes into `runner.table_to_dict` tables. The rules:
1. Every number in the text must be the pct (±0.5) or a base of a cited cell, or the gap in points (±1) between two cited cells. Reuse the label-stripping from `findings.unverified_numbers`, so "18-34" in a label is not a claim.
2. A comparison ("ahead of", "more than", "higher", "lower", "than", "leads", "vs", "versus", "outperform", "significant") needs at least two cited cells. "Significant" or "significantly" needs the sig letter: the higher cell's `sig` contains the other column's letter.
3. A cited cell whose column has an unweighted base under 30 is a problem ("base under 30 — say it is indicative").
4. Every problem is a plain sentence, saying what to change.

**R2 report:** stored as `study_metadata["report"] = {"sentences": [{id, text, cites, ok, problems, source: "finding"|"model"|"person"}], "drafted_at"}`.
- `POST /studies/{id}/report/draft` (202) enqueues the job `report.draft`, using the F4 queue and its `drain` in tests. It takes the top 12 findings (`analysis.findings`, which already carry citations). Each finding's own text is the sentence. When a private model is configured, the model may rewrite each one for a client (same numbers, plain register). The rewrite is kept only if `check` passes against the finding's cites; otherwise the finding's text is kept. With no model, the report is the findings' texts.
- `GET /studies/{id}/report` returns the report.
- `PUT /studies/{id}/report/sentences/{sid}` takes `{text, cites?}`. It re-checks and stores the sentence with `source: "person"`. A failed check is stored with its problems, never silently dropped.
- `POST .../sentences` adds a sentence and `DELETE` removes one.

**Web:** `ReportPanel.tsx` on the Reports tab, above Findings.
- Each sentence shows numbered citation chips (`T12·c3`). Hovering or focusing a chip shows that cell's value and base.
- A refused sentence is struck through, with its problems underneath. Click a sentence to edit it in place.
- "Draft report" starts the job and polls it. "Add sentence" opens an editor with a table/row/column picker for the cites.

**Deck:** `renderers/pptx.render_deck` adds a "Report" slide after the title slide, carrying the accepted sentences when a report exists. Each sentence's citations go into that slide's speaker notes.

**Acceptance:**
- checker unit tests: a correct sentence passes; a wrong number, an unsupported comparison, "significantly" without sig, and a low base each fail with their sentence;
- the draft job with a fake model keeps a good rewrite and replaces a bad one with the finding text;
- with no model, the draft is the finding texts;
- a PUT re-check;
- the deck has the Report slide, with notes;
- component tests for chips, refused sentences and editing.

### N1: Norms from past studies  · engine-builder · done (#175)
**Why:** "34% chose Brand A" means little alone. Across the agency's past studies the same question has a range.
- `src/engines/analysis/norms.py: norms_for(db, study_id, table) -> dict | None`.
- **Matching:** find other studies' tables whose question matches this one, using the `question` vectors in `project_chunks` (same embedding model; `src.platform.vectors` / numpy cosine at or above 0.90), whose row labels overlap with at least 80% (case-insensitive), and whose studies have processed tables.
- **Result:** for each row label of this table, `{label, n_studies, min, median, max}` of the matched tables' Total column, plus `matched: [{study_id, title}]`. It needs at least 2 matched studies, otherwise it returns None. The label for that case is "Not enough past studies yet".
- **Route:** `GET /studies/{id}/tables/{table_id}/norms`.
- **Web:** in `StudyThread.tsx`, a "Compared with past studies" step: "Across 4 past studies: 18–31% (median 24%)" for the clicked row, or "Not enough past studies yet".
- Studies only ever see norms from studies in the same deployment; this is one organisation per deployment, so there's nothing to filter.

**Acceptance:** with three synthetic studies with indexed questions and tables, a 4th study's matching table gets the right range; a different question does not match; one past study gives None.

### W1: The QA certificate and field-close workflows  · platform-builder · done (#174)
Read `src/platform/workflows.py` and `jobs.py` (wave 1, F4) first.

**qa_certificate** (`src/workflows/qa_certificate.py`) is three steps: `qa.expert_checks` (the existing expert checks) → `qa.dry_run` (simulate n=1000, fixed seed, on the approved questionnaire) → `qa.certificate`.
- The certificate step writes `STUDIES_DIR/{id}/logic_certificate.pdf` with PyMuPDF (already a dependency; no new one), one page:
  - study title, questionnaire version and a sha256 of its definition_json;
  - the date, and the respondents simulated;
  - dispositions, plus counts of rules that never fired, impossible rules and unreachable questions (listing their qids);
  - the expert-check notes;
  - a verdict: **"Issued"** only with zero unreachable and zero impossible, otherwise **"Not issued — fix these first"** with the list.
- It records `study_metadata["certificate"] = {issued, at, sha256, run_id}`.
- **Routes:** `POST /studies/{id}/certificate` starts the workflow (202, returns run_id); `GET /workflow-runs/{run_id}` returns the run state and its steps' states; `GET /studies/{id}/certificate.pdf` downloads the PDF (404 until one exists).
- **Web:** in `DryRunPanel.tsx`, an "Issue logic certificate" button. It shows progress through the three steps and then either "Issued — download" or the blockers.

**field_close** (`src/workflows/field_close.py`) has these steps:
1. build the data file, reusing the function behind `POST /studies/{id}/fielding/materialize` (find it in `api/routes/materialize.py`, and call the engine function, not the route);
2. run the tab plan;
3. `report.draft` when that job kind is registered;
4. the deck.

- **Route:** `POST /studies/{id}/workflows/field-close` (owner/analyst).
- **Web:** no UI beyond a button on the control room, "Close fieldwork and build deliverables", behind a confirmation step written into the page.

**Acceptance:**
- the certificate workflow runs through `drain` on a synthetic approved survey and writes a PDF whose text (`fitz` `get_text`) contains "Issued";
- a survey with an unreachable question writes "Not issued" and names it;
- the run route shows the step states;
- field_close runs end to end on the golden-path fixture and leaves tables and a deck.

### K1: Cleaning decisions become suggestions  · engine-builder · done (#172)
**Why:** a reviewer who removes every "too fast to read" respondent on every study should not have to do it by hand again. The system learns that habit literally and asks, rather than deciding for them.
- **Table** `cleaning_habits` in `api/db.py`: `check` (pk), `removed` (int), `kept` (int), `studies` (int), `updated_at`.
- **Learning:** when a study's cleaning is locked (find the lock route in `api/routes/cleaning.py`), each flag check among that study's decided respondents adds its removed and kept counts and bumps `studies`. This happens once per lock; a re-lock of the same study must not double-count, so store `study_metadata["cleaning_habits_counted"] = true`.
- **Suggesting:** `GET /cleaning/habits` lists the habits. In the cleaning summary (`GET /studies/{id}/cleaning`), add `suggestions: [{check, label, removed_rate, studies, keys: [...]}]` for checks with at least 10 decisions, a removal rate of at least 0.9, at least 2 studies, and undecided flagged respondents in this study.
- **Web:** `CleaningPanel` shows each suggestion as a card: "On 3 past studies you removed 46 of 48 respondents flagged *too fast to read*. 9 here are flagged the same way." It offers "Review them" (which filters the list) and "Remove all 9". Remove all uses the existing review-first bulk path, recorded with reason "other" and a note naming the habit. Nothing is ever applied automatically.

**Acceptance:** locking two synthetic studies builds the habit; a re-lock doesn't double-count; a third study gets the suggestion with the right keys; a habit under the thresholds suggests nothing; the component test shows the card, and "Remove all" calls the bulk decision with the note.

## Wave 5a: proof, all the way down

Shared rules as before. Each card says whether it may add a dependency.

### P1: Logic proved by a solver  · engine-builder · ready
**Why:** a dry run of 1,000 respondents can miss a path that 1 person in 50,000 takes. A solver answers "can anyone reach Q7?" for every possible respondent, and when the answer is no, it says why.
**New dependency allowed:** `z3-solver` (MIT), pinned to an exact version in requirements.txt.
**Files:** `src/engines/questionnaire/logic_proof.py`, `tests/unit/test_logic_proof.py`, plus the certificate (`src/workflows/qa_certificate.py`) and the X-ray panel copy.
- **Encoding:** for each question in `survey.question_order()`, an `answered_<qid>` Bool, plus:
  - single or numeric: an Int (numeric bounds from `validation` min/max when present; single constrained to its option codes);
  - multi: one Bool per option code, with at least one chosen when answered, and an exclusive option excluding the others;
  - grid rows (`row` set): an Int per (qid, row) over the column codes.
- **Semantics:** the encoding must mirror `survey_definition.evaluate` exactly. A leaf over an unanswered qid is False, so `not(leaf)` over an unanswered qid is True. `in`, `selected`, `eq`, `ne`, `gt` and `lt` behave as `evaluate` does. The question order is the survey's.
  - `answered_Q` ⇔ (no show_if, or show_if holds) ∧ the respondent was not screened out earlier. Screened out earlier means: some earlier question P that was answered and whose terminate_if holds.
  - Derived variables (`derive_if`) and masks are out of scope for v1. A question whose show_if references one is reported as "not provable (uses a derived variable)", never as unreachable.
- **API:** `prove(survey) -> {"unreachable": [{qid, why}], "impossible_screenouts": [{qid}], "always_screened_out": [qid], "not_provable": [{qid, reason}], "examples": {qid: answers}}`.
  - `why` comes from an unsat core, in plain words (e.g. "Q7 needs Q5 = 4, but Q5 only allows 1–3").
  - `examples` holds, for each reachable question, one satisfying respondent as a plain answers dict.
- **Cross-check (required tests):**
  1. On the golden-path template and three hand-built surveys, every question the dry run reached is provably reachable.
  2. Every `examples` answer set, replayed through `evaluate` in question order, really reaches its question (write the replay helper).
  3. A question reachable only by a 1-in-10,000 combination is proved reachable, though the dry run (n=1,000) misses it.
  4. A contradiction (Q7 shown if Q5 = 4, with Q5 codes 1–3) is proved unreachable, with the reason naming both.
- **Certificate:** the `qa.dry_run` step also calls `prove`. The verdict uses the proof: Issued only when nothing is proved unreachable. The PDF says "Proved for every possible respondent" when nothing is not_provable, otherwise "Proved except: …". Unreachable questions list their reasons.
- **X-ray:** `DryRunPanel` shows a line, "Solver: every question is reachable" or "Solver: Q7 can never be shown — <why>". The dry-run route returns `proof` alongside the report.
- **Performance:** a 150-question survey proves in under 5 s (test with a generated survey).

### P2: Tamper-evident study ledger  · platform-builder · ready
**Why:** "trust us" becomes "check it yourself". Every recorded change to a study is chained, so an edit to history shows. A delivered file can be checked against what was issued.
**Files:** `api/db.py` (`audit_log` gains `seq`, `prev_hash`, `hash` via `_ensure_new_columns`; new `ledger_heads` table: `study_id` pk, `seq`, `hash`), `src/platform/ledger.py`, `record_audit` in `api/db.py`, the deliverable writers, `api/routes/platform.py`, `web/src/pages/ActivityPage.tsx` or the project's Activity tab (whichever shows the study's audit log; find it), and tests.
- **Chaining:** `record_audit` with a `study_id` locks that study's `ledger_heads` row (`with_for_update`; SQLite needs no lock) and computes `seq = head.seq + 1` and `hash = sha256(prev_hash + canonical_json(entry))`. The canonical form covers entity, entity_id, action, actor_id, study_id, summary, before, after and created_at, with sorted keys and no whitespace. It then stores the entry and moves the head.
  - Entries without a study_id chain under the key `"_global"`.
  - `record_audit` keeps its never-raise contract.
- `ledger.verify(db, study_id) -> {ok, n, head, first_bad_seq}` recomputes the chain. Entries written before the chain existed (seq null) are reported as `{"unchained": n}`, not as tampering.
- **Deliverables:** every time a pptx, xlsx, data export, report or logic certificate is written, record `entity="deliverable"`, `action="issued"`, `after={"file": name, "sha256": ...}`.
- **Routes:**
  - `GET /studies/{id}/ledger/verify`;
  - `POST /studies/{id}/ledger/check-file` (multipart) returns `{issued: bool, at, name}` for a file whose sha256 was recorded as issued for this study.
- **Web:** on the study's Activity view, a strip reading "Ledger: 214 entries · intact ✓ · fingerprint a1b2c3…", or "Ledger broken at entry 57". Below it, "Check a file you received": upload, then "This file was issued on 25 Oct 2026" or "This file was not issued by this study".
- **Tests:** a chain of 20 entries verifies; editing one stored entry is reported at its seq; a deliverable written by materialize is found by check-file; a different file is not; concurrent writes on Postgres are covered by a `DVV_TEST_PG` test.

### P4: Person-fit scores for authenticity  · engine-builder · ready
**Why:** psychometrics has measured for decades whether one person's answers fit everyone else's pattern. Bots and inattentive respondents misfit in ways speeders and straight-liners don't catch.
**Files:** `src/engines/qc/person_fit.py`, a hook in the authenticity flag pipeline (`src/engines/qc/authenticity.py`, where flags are built), and tests.
- For each rating grid with at least 6 items (reuse `respondents._grid_groups`), compute per respondent:
  - **IRV**, the intra-individual response variability (SD across the grid's items);
  - **normalised Guttman errors G\***, polytomous, with items ordered by mean score.
- Flag `person_fit` (weight 15 in `WEIGHTS`, label "Answers don't fit the pattern") when a respondent is in the top 5% of G\* on at least two grids, or on the only grid when there's one. Its detail is plain words: "On SAT_ATTR and BRAND_IMG, answers contradict the order everyone else gives these items (top 5%)."
- It never flags when fewer than 30 respondents answered the grid.
- **Tests:** synthetic data where 5 respondents answer in reverse of the common item order are flagged; consistent respondents are not; under 30 respondents gives no flag; straight-liners are not double-flagged as misfit (their G\* is 0).

### P5: An error bar on every number  · engine-builder + ui-builder · ready
**Why:** "34%" from 61 people is not "34%" from 600. Every percentage should carry its uncertainty, and a sentence that ranks two overlapping numbers should be refused.
**Files:** `src/engines/tabs/intervals.py`, the provenance step, the citation checker, `StudyThread.tsx`, the cited report chips, and tests.
- `interval(pct, n_eff, level=0.95) -> (lo, hi)` is the Wilson score interval on the **effective** base (so a weighted column's design effect counts). It works from the cell's pct and `effective_base`, which tables already carry, so nothing needs recomputing. Edge cases: pct 0 or 100, and n_eff under 1 (returns None).
- **Study Thread:** the cell step reads "34% (95% interval 28%–40%, effective base 212)".
- **Report chips:** the hover shows the interval.
- **Checker (new rule):** a sentence that ranks two cited cells ("ahead of", "more than", "higher", "lower", "leads", "outperform") whose intervals overlap, and that has no sig letter between them, gets the problem "These two numbers are within each other's margin of error (28–40% vs 25–37%). Say they are similar, or cite a tested difference." A sentence that says "similar", "in line with" or "no significant difference" is exempt.
- **Tests:** Wilson values against known references (e.g. 50% of 100 gives 40.4–59.6); the effective base drives the width; the new rule refuses an overlapping ranking and accepts it when sig is present or the sentence says similar; the findings-generated report from the golden path still passes, so the report writer must already avoid this. If it doesn't, adjust `findings` so a lead with overlapping intervals is worded "is level with" rather than "leads", and test it.

## Wave 5b: (cards to write after 5a)
- **P3** Replay any deliverable byte for byte from the ledger (needs P2).
- **P6** Supplier allocation as a bandit.
- **P7** Semantic diff between tracker waves.
- **P8** Hierarchical Bayes for MaxDiff and conjoint.

## Founders, alongside every wave
- Field one real study with an outside analyst (P0). It is the test for every screen above.

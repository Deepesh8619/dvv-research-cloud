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

### F1: Job queue, worker and workflow runner  · platform-builder · ready
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

### F2: Study graph  · platform-builder · ready
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

### F3: Vector store interface and pgvector  · platform-builder · ready
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

## Wave 2: wiring (blocked on wave 1)

- **F4** Move `_draft_translation` and `coding/run` onto the queue as job kinds
  `translation.draft` and `coding.run`; the status routes read the job. Add the
  `events` table, `emit()`, `GET /api/studies/{id}/events` (SSE, auth, study
  access) and `GET /api/jobs/{id}`. Add a `worker` service to the compose file.
  needs F1.
- **G1** Graph writers: questionnaire approval writes question, option and variable
  nodes plus routes_to, masks, pipes and derives edges. A table cell opened through
  the new `GET /studies/{id}/tables/{t}/cells/{c}/provenance` writes the cell node,
  with computed_from, filtered_by and weighted_by edges. needs F2.
- **X1** Path coverage: the dry run records each simulated respondent's path and
  returns `paths: [{qids, n}]`, `unreachable: [qid]` and `dead_ends`. needs nothing.
- **A1** Paradata features: keystrokes, characters, paste, speech-to-text and time
  columns that `variable_roles` currently discards become per-respondent
  features (`src/engines/qc/paradata.py`). Test on the TRD2 shape (1,168
  characters, 1 keystroke). needs nothing.

## Wave 3: screens (each from the mockups)

- **R0** Lifecycle rail and "Needs you" (screen A). needs F4.
- **X2** Logic X-ray map (dagre) and the certificate PDF (screen B). needs X1.
- **T1** Study Thread drawer on every table cell (screen C). needs G1.
- **A2** Authenticity score as named signals, plus the session timeline in triage
  (screen D). needs A1, F3.
- **C1** Control room over SSE (screen E). needs F4.

## Wave 4: insight and learning

- **R1** Citation checker: numbers in a sentence must match cited cells within
  rounding; comparisons need a significance result. needs G1.
- **R2** Cited report writer (screen F). needs R1.
- **K1** Corrections for cleaning and table specs, as literal rules. needs F2.
- **N1** Norms from past studies' matched questions. needs F3, G1.
- **W1** `field_close` and `qa_certificate` workflows, end to end. needs F4, X2, R2.

## Founders, alongside every wave
- Field one real study with an outside analyst (P0). It is the test for every screen above.

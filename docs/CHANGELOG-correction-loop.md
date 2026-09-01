# Correction Loop — build log

Everything built on branch `feat/correction-loop`, 2026-08-29 → 2026-08-30.
10 commits, 32 files, ~5,200 lines added. 567 tests passing (from a 496 baseline).

**Nothing here is pushed.** The branch is local; `main` is untouched.

This log records *why* each thing exists and what was learned building it,
because the reasoning is the part that gets lost. Where something is unverified
or unfinished, it says so.

---

## The idea

Vaibhav and Vishal know how each client writes questionnaires; the parser does
not. Before this, every correction was spent once: someone hand-fixed a row, the
next document from the same client needed the same fix again.

A correction is now a **rule attached to a format profile**, so one sentence
fixes every question in the document *and* every future document of that client
type. The people who know the domain reach the parser without going through an
engineer.

---

## 1 · Correction loop — `4bae134` (2026-08-29 17:09)

**The seam.** T32 already ran `detect_profile → profile_hint →
build_extraction_prompt`, but `profile_hint` was a closed set of three selectors
(`field_spec`, `prose`, `conservative`) picking a canned block. It carried a
*choice*. It now also carries *content* — founder-taught rules — resolved via a
`rules_for_profile` callable so the engine never imports `api.db` (only detection
knows the slug).

**The guarantee that made it shippable:** a profile with no learned rules
produces a **byte-identical prompt** to before this feature existed. Asserted
directly in `test_no_rules_is_byte_identical_prompt`. Without that, shipping this
would have meant re-validating extraction for every existing client.

**Two lanes, and ambiguity always routes to the safer one:**

| Lane | What | Why safe |
|---|---|---|
| `live` | Extraction rules | Only ever rewrites **draft** questionnaire data. Fielding, quotas and respondent data are unreachable from here. |
| `proposal` | Needs code | Applies nothing. Queues for the owner. |

A correction that should have been instant but waits costs an afternoon. One
that should have waited but was applied costs trust in the whole system. The
asymmetry is not close, so the default is not close.

**Gate:** new `founder` role, enforced server-side on every endpoint. Hiding a
button is not access control. `global` scope still requires OWNER.

**Suggestions** rank flagged (the extractor's own uncertainty) above learned
(precedent on this profile) above common. Clicking fills an editable sentence and
never submits — the correction stays in the author's words. The **scope chip**
updates as they type, so a founder *sees* when a correction will teach the parser
permanently. Without it every correction silently defaults to a one-off.

New: `corrections`, `profile_rules` tables · `api/routes/corrections.py` ·
`web/src/components/CorrectionBox.tsx`

---

## 2 · Open-weights-only routing — `1936a2c` (17:51)

Client questionnaires are NDA-bound, so which vendor sees them is contractual,
not a preference. `LLM_OPEN_WEIGHTS_ONLY` **defaults ON** and drops OpenAI and
Anthropic from every chain *even when their keys are present* — a stray key in a
Render env can no longer route a confidential document off-catalog.

Fails closed deliberately: a default you must remember to set is one that gets
forgotten on the next deploy.

Added a `huggingface` backend (reused `_openai_compatible_generate`, ~20 lines).
Groq's catalog was *already* entirely open-weight, so the migration was mostly
cutting the tail.

> **Unverified:** the extraction default moved to `openai/gpt-oss-120b` on
> published pricing and general capability, **not** on evidence from a DVV
> document. The suite has no accuracy oracle — see §3.

---

## 3 · Accuracy oracle — `6e12c42` (18:44)

The suite proved the *plumbing*; nothing proved extraction was any *good*. A
model swap or a bad rule could degrade real accuracy with every test still green.

`scripts/benchmark_extraction.py`:
- **structural** (default) — no labels needed, runs on any client document today
- **`--ab`** — same document, rules off then on, diffed. The correction loop's
  value as a number instead of a claim.
- **`--gold` / `--make-gold`** — field accuracy against known-correct answers,
  where the gold file is built from questions a human **already approved** in the
  review UI. Labelling becomes a by-product of work the team already does.

**A belief was corrected here.** I had said "74 questions, known-correct." That
was the *segmenter's structural count*, not verified extraction. **No gold file
existed.** Three real client QREs live at `~/Desktop/DVV/Test data/`.

**A production bug surfaced on the first run:** a weaker model can emit `routing`
as a bare string instead of the schema's object, raising `AttributeError` out of
`_question_from_raw` and breaking `extract_question`'s documented contract of
never raising on model output. Fixed at the single shared point; the string is
**kept** as the routing verbatim (it is exactly what a reviewer needs to resolve
routing by hand) rather than discarded.

**First measured baseline** — real GBK doc, `llama3.2:3b`, 8GB M2: 100% question
text, **0% options, 0% terminates, 4/4 flagged, ~40s per question.** Local Ollama
is a fast-fail step in the chain, not a quality tier — now evidence, not
assertion.

---

## 4 · Cerebras, HF provider pinning, backend probe — `2355f93` (19:15)

`scripts/check_llm_backends.py` sends one tiny schema-constrained request per
backend, distinguishing *not configured* / *unreachable* / *reachable but cannot
honour a schema* — the last being the failure that actually bites, and which
otherwise shows up as 75 identical errors and a burnt budget.

`HF_PROVIDER` pins an Inference Provider, as HF's own docs recommend for
structured output.

**Also fixed a latent flake this exposed:** five chain tests passed only because
`CEREBRAS_API_KEY` happened to be absent from the shell. They now clear it
explicitly, and the open-weights guarantee test asserts the **full** chain with
every key set. Verified by re-running with keys deliberately leaked into the
environment.

---

## 5 · `.env` was silently ignored — `c2147c6` (19:29)

Everything read `os.getenv` directly. That works on Render (real env vars) but
meant a local `.env` did **nothing** — you could add `GROQ_API_KEY`, see
`llm_keys: false`, and have nothing anywhere explain why.

`src/env.py`, stdlib only. **Real environment variables always win** (values
applied with `setdefault`), so a stale `.env` on a deployed box can never
override what the platform set — the "works locally, wrong in production"
failure that is miserable to diagnose. It has its own test.

---

## 6 · Cerebras corrections — `152651d` (20:57)

Two things I had asserted were wrong, both found by probing with a real key:

1. **Model id guessed.** `llama-3.3-70b` came from a search result; the account
   serves `gpt-oss-120b` and `gemma-4-31b`, so every call 404'd. The
   `curl /v1/models` command to *ask* is now recorded in `.env.example`.
2. **Free tier claimed on a third-party blog.** A fresh account returns HTTP 402
   `payment_required` for **both** served models. Documented plainly, with the
   date verified, so nobody plans a benchmark budget around a tier that does not
   exist.

---

## 7 · Before/after preview (P4) — `034b29a` (21:12)

A founder can now re-read the **whole document** under a candidate rule and see
what it did before any of it becomes real. Previously a rule was approved blind.

**The design rule everything hangs on: regressions come first and cannot be
collapsed.** A rule that fixes 41 and quietly breaks 6 is not a success, and a UI
that led with the wins would pass exactly that.

`changed` (both sides present and different) is its own **neutral** category and
never scores as a win — otherwise a rule that rewrites 40 question texts into
*different wrong text* reads as a triumph.

`src/engines/questionnaire/diffing.py` is pure functions over dicts — no LLM, no
DB — so the judgement calls are fully tested (11 cases).

Preview runs on a daemon thread and writes **only** to `correction_previews`; the
live definition is untouched until approve. The source `.docx` lives on Render's
ephemeral disk, so a missing file returns an actionable **409** ("re-upload to
preview") rather than a 500.

**Also fixed a real 500,** found by driving the live UI: `_classify` read
`response.data`, but `run_task` returns `AIResponse` whose payload is `.content`,
so **every correction 500'd** — throwing away what a founder had just written.

> **The test had passed** because its double was a hand-rolled class exposing
> `.data`. A double that does not match the real object is worse than no test.
> It now uses the real `AIResponse`, plus a test that a *good* classification is
> honoured, plus one that any unexpected exception still degrades safely.

---

## 8 · Audit, revise, reorder, proposal queue (P5) — `a6fbd71` (2026-08-30 09:31)

Closing gaps between what the spec **claimed** and what the code did.

- **Audit trail.** The spec called this a safety property; it did not exist.
  `audit_log` is append-only and generic, wired into create / apply / toggle /
  approve / revise. `record_audit` **never raises** — losing an audit line is
  bad, failing a founder's approval because an audit insert hiccuped is worse.
- **Revise.** The third review action. Without it a reviewer whose rule broke Q31
  could only rewrite from scratch. The note now rides into the next
  classification, so attempt 2 starts *from* attempt 1. Revising also deletes the
  stale preview — a diff computed under the old rule shown beside a new rule
  would actively mislead.
- **Reorder.** The prompt tells the model later rules override earlier ones; that
  ordering was enforced and unchangeable.
- **Proposal queue.** Proposal-lane corrections landed in the DB and surfaced
  nowhere, making "it queues for the owner" a claim rather than a feature.

**Migration verified properly.** The first check "passed" only because an earlier
`init_db()` had already added the columns — which proved nothing. Rebuilt a table
with the *original* schema (what Render's Postgres has) and ran the real startup
path: all four columns added, pre-existing row intact, `attempt_no` backfilled.

---

## 9 · 429 handling — `0f1a908` (11:31)

A rate limit is not "this backend is broken", it is "wait a moment" — and
providers say how long. The router treated 429 as `BackendUnavailable`, so a
rate-limited call fell straight through to the **next** backend, which is weaker
or (as here) dead.

**Measured on the real GBK document, 12 chunks, Groq `gpt-oss-120b`:**

| | before | after |
|---|---|---|
| Failed extractions | **10 of 12** | 1 of 12 |
| Choice qs with options | — | **100%** |
| With terminate | 0% | **50%** |
| Mean confidence | — | 0.894 |

The surfaced error before the fix was *Cerebras' 402*, because Groq's 429 had
already been swallowed by the fallback — **the actual cause was invisible.**

Root cause: Groq's free tier caps **8,000 tokens/minute** and one extraction
prompt is ~6,100. Nothing was wrong with the model or the prompt.

`http_json` now honours `Retry-After`, then the "try again in 14.69s" hint
providers embed in the message, then exponential backoff. Bounded at 3
attempts / 30s. **Non-429s (401, 402) are not retried** — not transient, and
retrying would hide the cause.

---

## 10 · Founder-editable wording (Tier A) — `0e0b620` (12:25)

Changing "Dashboard" to "Studies" needed an engineer, a deploy, and a round trip
through me. Now it is a text box.

**The safety property is the fallback, not a permission check.** Every
instrumented string renders through `useLabel(key, default)`, and the default at
the call site is what ships, what renders while overrides load, and what renders
if the fetch fails. The app never *depends* on an override existing, so the worst
a bad edit can do is make one word read wrong — never a blank screen — and revert
is one click.

Two more constraints keep it there: only keys in the server's `UI_KEYS` registry
are editable, and **v1 is strings only** (visibility, ordering and layout each
need their own semantics and can genuinely break a screen).

31 editable strings instrumented. Verified live: Vishal renamed
"Dashboard" → "Studies" and "Awaiting sign-off" → "Waiting on client".

---

## 11 · Activity log — this commit

Two deliberately separate records, because mixing them makes both useless:

- **Changes** (`/api/corrections/audit`) — what happened, who did it. Answers
  "who approved the rule that broke extraction, and when" — a question only ever
  asked after something has gone wrong, which is why it must have been recorded
  all along.
- **Looked for** (`/api/ui/activity`) — what founders **searched** and did not
  necessarily find. A search is intent: *"searched 'terminate' four times,
  changed nothing"* says the wording they need does not exist yet, and no record
  of completed changes would ever reveal that. Repeated searches are counted and
  surfaced, because a query someone kept retrying is the loudest signal in there.

Searches are debounced (900ms, ≥3 chars) and fire-and-forget — telemetry must
never delay or break the screen it watches.

---

## Known gaps

| Gap | Status |
|---|---|
| **The rendered diff screen** | Built, tested at the data layer, **never seen**. `llama3.2:3b` classifies non-deterministically to `proposal`, so the live-lane UI path could not be reached locally. Needs a run on Groq. |
| **`gpt-oss-120b` vs DVV documents** | Default changed on published capability, not measured accuracy. Benchmark before trusting it commercially. |
| **Few-shot examples per profile** | Not built. Highest accuracy-per-effort item remaining (~half a day). The prompt tells the model *rules* about GBK format but never *shows* it a correctly-extracted GBK question. |
| **Cost confirm before preview** | Endpoint returns `estimated_model_calls`; the UI shows it mid-run rather than gating on it. |
| **Reorder / proposal-queue UI** | Endpoints exist and are tested. No screens. |
| **Production** | Render Postgres suspended. Local works fully. |
| **Free-tier speed** | ~40 min for a 75-question document at 8k TPM. Fine for benchmarking, too slow to demo live — run previews *before* a meeting. |

## Data-use warning

Free LLM tiers differ on whether they train on submitted data — Mistral's
requires opting in. Client questionnaires are NDA-bound. **Free tiers are for
benchmarking with test documents, not client work.**

## Directive vocabulary from a full inventory of the three client documents

Rather than sampling patterns, every bracketed directive across all three real
client questionnaires was inventoried: **116 distinct shapes**. Acting on the
dominant unhandled families raised deterministic (zero-LLM) extraction from
**37% to 64%** of questions across the three documents.

| Convention | Seen | Now yields |
|---|---|---|
| `TEXTBOX`, `OE: n-CHARACTER MINIMUM`, `_ _ _ _` blanks | 17x | `open_text` |
| `NUMERIC OE`, `RECORD NUMBER n TO n` | 4x | `numeric` (ordered before `OE`) |
| `ROTATE` / `KEEP ORDER` | 3x | randomize on / off |
| `0=Low \| 1 \| ... \| 10=High` scale row | 27x | `grid_single` + scale recorded |
| `IN ROWS`, `RANDOMIZE STATEMENTS` | 8x | `grid_single` |
| `Rank your top three`, `RANK 1,2,3` | 5x | `rank` |
| `[SHOW Q26 LIST]`, `INSERT ... FROM Q4` | 6x | type trusted despite no local options |
| All-caps `CODE ... BASED ON S1` wording | 12x | derived variable, `info` + flag |
| Yes / No option pair | 30x+ | `single` |
| `best describes` / `which one` / `what is your` | 18x | `single` |

Per document: Bubble 26 -> 73 of 90, Vivo 22 -> 60 of 80, Google Ads 19 -> 24 of 75.

**Deliberately not guessed.** The Google Ads document writes `Q type: (blank)`
on 50 questions whose wording is genuinely ambiguous ("Which of the following
are you responsible for" is single or multi depending on client intent). Those
still escalate, on a ~250-token residue prompt asking only for the type. A
wrong deterministic answer is silent and looks authoritative; an escalation is
visible and cheap.

Two rules are shared by both parsers (`is_derived_wording`,
`is_single_select_wording`) so a spec-table document and a prose document
cannot disagree about the same sentence.

## Plain-language question types, and the vocabulary taught to the model

**UI.** The review screen rendered engine identifiers directly: `grid_single`,
`open_text`, `info`. That asks a researcher to learn our vocabulary before they
can review anything. One shared map (`web/src/labels/questionTypes.ts`) now
gives every screen the same plain names and a one-line description of what the
respondent actually does, so a wrong type is visible without decoding it:

| stored | shown | hint |
|---|---|---|
| `single` | One answer | Respondent picks exactly one option. |
| `multi` | Multiple answers | Can tick as many options as apply. |
| `grid_single` | Rating grid | Each row rated on the same scale. |
| `rank` | Ranking | Puts the options in order of preference. |
| `numeric` | Number | Types a number, e.g. an age or a count. |
| `open_text` | Written answer | Types their answer in their own words. |
| `dropdown` | Dropdown list | One answer from a long list. |
| `info` | Not a question | Display text, or computed by the scripter. |

The stored values are unchanged — this is presentation only.

Two related fixes on the same screen. A derived variable now shows a plain
callout ("Not asked to respondents … the fields below are expected to be
empty") instead of an empty question form with an Approve button and no
explanation. And the two routing panels no longer share one empty message: "No
condition — always shown" under TERMINATE read as though someone were being
screened out. Each panel now says what empty means for it.

**Model.** The residue schema could not return `rank` at all — the enum omitted
it, so a ranking question the parser did not catch had no correct answer
available. Added, and both prompts now carry the directive vocabulary from the
inventory: TEXTBOX/OE/NUMERIC OE, scale rows, IN ROWS, RANK, ROTATE vs KEEP
ORDER, ANCHOR/EXCLUSIVE, borrowed option lists, and all-caps CODE variables.
The residue prompt stays at ~263 tokens and is only sent for the ~36% of
questions the parsers escalate.

One test double had to be tightened: it dispatched on the substring `"Q2"`, and
the new guidance cites `[SHOW Q26 LIST]` as an example, which silently swapped
two questions. It now matches on the question wording.

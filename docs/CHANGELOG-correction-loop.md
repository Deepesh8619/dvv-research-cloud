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

## Three production bugs found by running the real upload path

The provenance work forced an end-to-end upload, which had not been done since
the parsers landed. Each of these made extraction fail or waste money, and none
was visible from the test suite.

**1. Every question paid for a full LLM extraction it did not need.**
`extract_question` gated the prose parser on `prose.options or
prose.terminate_if`. An open end, a numeric entry and a display screen have
neither — so exactly the questions the prose parser resolves best fell through
to a full model call anyway. Measured on a 3-question document: 951 seconds and
three model calls, for questions the parser answers in milliseconds. Across the
three real client documents, 33 more questions now avoid a full extraction
(199 → 232 of 245 chunks).

**2. The extraction heartbeat wrote a different clock from the watchdog.**
`touch_questionnaire` used `datetime.utcnow()`; `_is_stale_processing` compares
against `datetime.now()`. On any machine not set to UTC the timezone offset
alone exceeded the 15-minute staleness window, so the *first* heartbeat of a
healthy run marked it dead — reporting "the background worker was likely killed
by a restart" while the worker was still running. Local development on a
non-UTC machine could never complete an extraction.

**3. The reconciliation pre-pass ran silently for 12 minutes.**
`reconcile_segmentation` is one whole-document LLM call that runs *before* any
chunk is extracted. It emitted no progress, so the watchdog saw a row that had
not moved. Measured at 12 minutes on one upload against a 15-minute window —
which is why a large document "never finished". It now heartbeats before and
after.

Together these are why the full 75-question extraction had never completed end
to end. The worker also logs its stages now (thread started, blocks → chunks,
extracting N) — it was previously silent from upload until the first extracted
question, which is what made this undiagnosable from the logs alone.

## Corrections that teach the parsers, not only the model

Until now a correction produced prose that was appended to the extraction
prompt. That made the system more accurate and never cheaper: the same
convention on the next client document still cost a model call, every time,
forever. This closes that.

A correction can now carry a **pattern** the deterministic parsers read
directly. Four kinds, one per decision the parsers actually make:

| kind | example | means |
|---|---|---|
| `type_signal` | `[SHOWCARD]` | the answer type |
| `field_alias` | `Screener population:` | which spec-table field a row fills |
| `option_tag` | `[SO]` | terminate / exclusive / anchor on one option |
| `randomize_signal` | `[SCRAMBLE]` | randomise the options |

Measured on a document using a client convention nothing in the built-in
tables knows: **1 of 3 questions parsed with zero model calls before teaching,
3 of 3 after** — verified end to end through the real upload path, not just in
a unit test.

**Patterns are literal fragments, never regexes.** A founder types what they
see. Accepting a regex would hand a text box the ability to hang extraction on
a catastrophic backtrack, and would fail confusingly on the brackets that
appear in nearly every real directive. Square brackets are stripped when
matching, because the parsers hand the matcher the text *inside* the brackets
for an option tag while a founder writes `[SO]` — "write what you see" has to
work for every kind, not most of them.

Validation runs at the boundary, before storing: an empty needle is a substring
of every string, so it would silently retype every question in the document.
A pattern that cannot be applied is worse than no pattern, because the founder
believes the system learned something and it silently did not.

Storage is three nullable columns on the existing `profile_rules` table rather
than a second table — one queue, one approval flow, one registry. A rule with
no `pattern_kind` is prose for the model exactly as before, and the server
writes the prose half from the pattern so a founder never says the same thing
twice. `active_patterns_for_profile` skips a row that no longer validates
rather than raising: one bad row must not take down a whole extraction.

The no-op guarantee holds at every layer — no patterns means the parsers behave
byte-identically to before the loop existed, asserted in the test suite.

**Teaching is reachable from the review screen.** The picker offers the
fragments *this question* contains that the parsers did not understand,
excluding anything already cited in `extraction_evidence`; picking from that
list instead of retyping is the difference between teaching the real string and
teaching a typo that silently never matches.

### A randomisation bug this uncovered

Wiring the taught `randomize_signal` through showed that randomisation was
being detected and then dropped: both parsers recorded it in
`Question.validation` but never set `Question.randomize_options`, the field
fielding actually reads. **60 questions across the three client documents said
randomise and were being fielded in fixed order** — order bias in collected
data, while the review UI showed the directive as handled. Detecting something
and then not applying it is worse than not detecting it.

Spec-table `Randomize:` cells are free text, so they are read conservatively:
only an affirmative word turns it on, and an unreadable value stays off and
stays visible in `validation`. Randomising when the document did not ask is a
data error, not a nicety.

## Screen-outs that fired on nobody

Found while checking why the review UI showed an empty codes box next to a
verbatim reading `TERMINATE if 1,3,98 selected`.

`Expression` defines `in` as membership in **`values`**. Both deterministic
parsers were putting the codes in **`value`**. Nothing raised — the shapes are
both valid — but everything downstream read the wrong field:

- `evaluate()` does `any(v in chosen for v in (expression.values or []))`,
  which is `any([])` — **False for every respondent**
- the SurveyJS compiler emits `anyof []`, so the fielded survey screened out
  nobody either
- the review UI reads `cond.values` for an `in` op, hence the empty codes box

**16 of the 17 terminate expressions the parsers produce across the three
client documents were inert.** The 17th used `op: "not_in"`, which is not in
`ExprOp` at all — unhandled by `evaluate()`, and the SurveyJS compiler raises
`ValueError` on it. Negation is now `not` over `in`, which is the declared
language and already supported by both.

Two existing tests asserted `expr["value"] == [1, 2]` and `op == "not_in"`.
They were green the whole time. **A test that pins the shape of a value without
exercising the behaviour can hold a bug in place** — both now assert through
`evaluate()`, and a new test checks that every op the parsers emit is one
`ExprOp` declares.

## Two controlled-input round-trips in the review form

Both are the same defect: the input's `value` was derived from the *parsed*
model, so parsing erased what was being typed before the next keystroke.

**Codes box (`is one of`).** `value={(cond.values ?? []).join(', ')}` meant
typing `1,` parsed to `[1]` and rendered back as `1` — the comma vanished as it
was typed, making a multi-code screen-out impossible to enter by hand. The raw
text is now held while the box has focus; the parsed array still updates on
every keystroke, and blur re-renders the normalised codes so what is shown is
what will be saved.

**Option code.** `onChange={{ code: Number(e.target.value) }}` — clearing the
box gives `""`, and `Number("")` is `0`, so backspacing to retype a code
silently wrote **code 0** into the questionnaire. It now holds the raw text,
commits only a real number, and restores the stored code on blur.


## Frontend tests for the two input regressions

`vitest` + Testing Library (`jsdom`), run with `npm test` in `web/`.

Both tests **type one character at a time**. That is the whole point: these
were per-keystroke bugs, and setting a whole value at once (`fireEvent.change`)
reproduces neither — it never asks the component to survive a half-typed state,
which is exactly where both failed.

Each test was checked against the pre-fix code before being kept:

| test | on the fixed code | on the old code |
|---|---|---|
| comma survives while typing `1,3,98` | passes | **fails** |
| trailing separator `1,` is held | passes | **fails** |
| blur normalises `1,,3 ,98` to `1, 3, 98` | passes | **fails** |
| backspacing a code keeps the others | passes | **fails** |
| cleared option code stays empty, not `0` | passes | **fails** |
| blur restores the stored code | passes | **fails** |
| a code typed after clearing is accepted | passes | passes |
| editing one code leaves the others alone | passes | passes |

The last two are behavioural coverage rather than regression guards — they hold
on the buggy version too, and are labelled as such rather than counted as
protection they do not provide. A test that passes on the broken code guards
nothing, which is the same trap the routing tests fell into: they asserted
`value == [1, 2]` and stayed green while every screen-out matched nobody.

`QuestionDetail` is exported from `QuestionnairePage.tsx` for this. Rendering
it with `canCorrect={false}` keeps the founder-only surfaces out of the tree,
so the test needs no providers and touches no network.

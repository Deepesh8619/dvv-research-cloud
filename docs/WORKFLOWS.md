# DVV Research Cloud — features and workflows

Status as of 2026-09-01. Every number here is reproducible from the repository;
anything not yet built is marked as such rather than described in the present
tense.

---

## The system in one paragraph

A researcher uploads a client questionnaire. The system identifies the file
type by rule, reads as many questions as it can *without* an AI model — 64% of
questions across three real client documents — and asks a model only about the
specific fields it could not resolve. Every answer carries a citation to the
fragment that produced it, so review is checking evidence rather than trusting
output. Founders correct what is wrong, and those corrections are stored against
the document's format profile so the next document of that shape reads better.

---

## Workflow 1 — Questionnaire intake and review

```
Upload .docx
   │
   ├─ classify_file()            rule: 5 scripter conventions, 0 model calls
   │
   ├─ detect_profile()           which client format is this?
   │
   ├─ segment_into_questions()   chunk the document
   │
   ├─ per chunk:
   │    ├─ spec_table parser     labelled rows (Q Name:, Response list:, …)
   │    ├─ prose parser          wording + bracketed directives
   │    └─ residue prompt        ONLY the fields still missing (~263 tokens)
   │
   ├─ checkpoint every 5 chunks  partial work survives a crash
   │
   └─ review UI                  citation per field, then approve
```

**What the reviewer sees per question**

- Plain-language type ("Rating grid", not `grid_single`) with a line saying what
  the respondent does
- **How it was read**: from the spec table, from the scripter notes, or by the
  AI model — the AI case is the only one tinted, because it is the one that must
  not be skimmed
- **The exact fragment behind each field**: `Answer type ← [PN: TEXTBOX]`,
  `Screen-out ← Advertising [TERMINATE]`
- Blocking flags that prevent approval, with the reason in plain English
- Drag to reorder, or Alt+↑ / Alt+↓ from the keyboard; an order that would move
  a question ahead of one its routing depends on is rejected with the reason

**Cost.** A 74-question document went from 449,328 tokens (2.2× the daily free
cap) to roughly 108,000 (0.54×) through: skipping verification on
high-confidence parses, the spec-table parser, the prose parser, and cutting the
output reservation from 4096 to 1536 tokens.

---

## Workflow 2 — Correction loop (how the system learns)

A founder writes what is wrong in plain language — *"the terminate condition
wasn't picked up"* — and the request is routed to one of two lanes:

| Lane | Meaning | Effect |
|---|---|---|
| **live** | an extraction rule, draft data only | applied now, stored against the format profile |
| **proposal** | needs code | queued for engineering, never silently applied |

Anything ambiguous goes to **proposal**. Rules are stored per format profile, so
a convention learned on one client's documents does not leak into another's. A
profile with no learned rules produces a byte-identical prompt to before — the
no-op guarantee is asserted in the test suite.

**Known limit.** Corrections currently teach the *model prompt*, not the
deterministic parsers. So a repeated correction makes the system more accurate
but not cheaper. Closing this is the highest-value next build — see
`ARCHITECTURE-agents.md` §5.

---

## Workflow 3 — Coming: data audit against the questionnaire

Not built. Documented here because it is why the extraction accuracy matters.

Each extracted field becomes an assertion the data must satisfy:

| Field | Assertion |
|---|---|
| `type: single` | exactly one response per respondent |
| `options: 1..5` | no value outside the declared codes |
| `terminate_if` | no completed respondent satisfies the screen-out |
| `show_if` | the column is null wherever the condition is false |
| `validation: 18–99` | no value outside the range |

These are comparisons, not judgements — the audit agent needs no model at all.
A `terminate_if` the extractor missed is a discrepancy the audit can never
find, which is the whole argument for citations over guesses.

---

## Feature list

**Questionnaire**
- Format-agnostic extraction: spec-table and prose parsers, alias-driven, no
  single client hard-coded
- 8 directive families parsed deterministically (types, randomisation,
  screen-outs, anchors/exclusives, ranking, grids, borrowed option lists,
  derived variables)
- Per-field provenance and citation
- Checkpointing; partial results survive a killed run
- Duplicate-qid detection, forward-reference validation, derived-variable
  labelling
- Drag/keyboard reordering, inline correction box, proposal queue

**AI routing**
- Open-weights models only by default (`LLM_OPEN_WEIGHTS_ONLY=1`) — client
  questionnaires are NDA-bound
- Fallback chain across four providers with per-model quota cooldowns
- Every backend failure reported, not just the last one
- Researcher-readable error messages

**Platform**
- Roles: owner, analyst, founder, viewer — one definition, server-enforced,
  mirrored in the UI for rendering only
- Audit log of every correction and search
- UI label overrides with a safe fallback (the app never depends on an override
  existing)
- Deployed on Neon Postgres + Render + Vercel

**Test and quality gates**
- 669 tests
- `ruff` clean, `tsc` clean
- Every parser rule has a test naming the real client convention it came from

---

## Running it

```bash
# API
.venv/bin/python -m uvicorn api.main:app --port 8000

# Web
npm run dev --prefix web

# Full gate before any push
PYTHONPATH=. .venv/bin/python -m pytest tests -q && .venv/bin/ruff check src/ api/ tests/ && (cd web && npx tsc --noEmit)
```

## Measuring extraction coverage

The benchmark takes a document. Structural mode needs no labels:

```bash
PYTHONPATH=. .venv/bin/python scripts/benchmark_extraction.py path/to/client.docx
```

To measure what a founder's correction actually bought, run the same document
with learned rules off and on:

```bash
PYTHONPATH=. .venv/bin/python scripts/benchmark_extraction.py path/to/client.docx --ab --profile gbk_spec_table
```

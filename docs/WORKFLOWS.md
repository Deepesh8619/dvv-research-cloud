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
- 691 Python tests, 8 frontend tests (vitest + Testing Library)
- `ruff` clean, `tsc` clean
- Every parser rule has a test naming the real client convention it came from
- Routing tests assert through `evaluate()`, not the shape of the expression:
  a screen-out that does not fire is not a screen-out, however correct it looks

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

---

## Workflow 4 — Data vs questionnaire (the cross-check)

The standard data pipeline validates a file *on its own terms*: speeders,
straightliners, duplicates, gibberish open-ends. None of that says whether the
data matches the instrument the client signed off. That comparison needs both
halves, and it is the check a DP shop is actually paid for.

**It is an explicit action, not something that happens on upload.** A project
often has data before its questionnaire has been reviewed, and running this
against an unreviewed extraction would report the parser's mistakes as the
client's. `Run cross-check` on the project overview (owner / analyst / founder).

```
questionnaire (approved)  ─┐
datamap                    ├─ link_questions_to_datamap()  → which variable holds which question
data.parquet              ─┘
                              ↓
                     check_against_questionnaire()
                              ↓
        undeclared_code · single_multi · out_of_range · terminate_violated
```

| Check | What it means | Severity |
|---|---|---|
| `undeclared_code` | A value in the data that the question never declared | error |
| `single_multi` | More than one response to a single-answer question | error |
| `out_of_range` | A number outside the stated minimum/maximum | error |
| `terminate_violated` | A completed respondent the screen-out should have removed | error |

No model is called anywhere in this. Every finding names the question, the
number of respondents and example row numbers, so a researcher can open the
file and see it. A finding nobody can reproduce by hand is worse than none.

**What was *not* checked is reported too.** `checked` counts what ran and
`skipped` says what could not — a screen-out referencing a question that could
not be linked to a variable is listed as skipped, never silently passed.
"Nothing found" and "nothing looked at" must never read the same.

Common system codes (99, 98, 0, −1 …) are excluded from the undeclared-code
check by default: they appear in nearly every real file without being written
into the response list, and flagging them would bury the real findings.

**Known limit.** `show_if` routing is checked by `linker.cross_validate_routing`
and is not yet folded into this report. Grid questions are linked but their rows
are not individually range-checked.

### MR techniques the parser understands

Read structurally from the directives, not as prose, so the review screen can
show them and the data cross-check can verify them. Every pattern is taken
verbatim from the three real client questionnaires.

| Technique | Written as | Recorded as |
|---|---|---|
| Masking — only selected | `[PN: ONLY DISPLAY OPTIONS SELECTED AT S6]` | `mask selected ← S6` |
| Masking — rows | `[PN: SHOW ONLY ITEMS SELECTED AT Q18 IN ROWS]` | `mask selected ← Q18` |
| Masking — whole list | `[SHOW Q26 LIST]` | `mask full_list ← Q26` |
| Masking — list minus a pick | `[SHOW Q26 LIST EXCEPT FOR Q27 SELECTION]` | `mask list_except ← Q26, minus Q27` |
| Masking — option level | `Unaware (Not selected at Q2a)` | `mask not_selected ← Q2a` |
| Masking — from a list | `[PN: INSERT BRANDS USED … FROM Q13]` | `mask selected ← Q13` |
| Piping — from a question | `[INSERT RESPONSE FROM Q8]` | `pipe ← Q8` |
| Piping — scripter variable | `[INSERT PHARMACY]` | `pipe PHARMACY` (recorded, never guessed) |
| Least fill | `ASSIGN BASED ON LEAST FILL` | `least_fill: true` |

Two distinctions the parser makes deliberately:

**A pipe is not a mask.** `INSERT RESPONSE FROM Q8` puts one answer into the
wording; `INSERT BRANDS … FROM Q13` builds an option list. Treating the first
as a mask would claim a question's options come from somewhere they don't.

**The most specific mask wins.** `SHOW Q26 LIST EXCEPT FOR Q27 SELECTION` also
matches the plain `SHOW Q26 LIST`; reading it as the plain form would show a
respondent the option they just picked.

**`mask_violated`** is then checkable against data: a respondent's answer to a
masked question may only contain codes they chose at the source. A mis-scripted
mask shows the wrong list to real people and leaves no other trace in the
output.

Not yet modelled: MaxDiff design balance (the `_LF` version columns and
best/worst pairs are recognised as MaxDiff machinery by the variable-role
classifier, but the design itself is not validated), and quota cell fill.

### Routing that is preserved but not enforced

When extraction cannot resolve a routing sentence into a question and codes —
"ASK IF AWARE OF BRAND AT Q4", with no qid or code named — it keeps the
sentence verbatim so a reviewer can act on it, and leaves `show_if` null. The
review screen shows the sentence beside the empty condition.

**A sentence cannot be evaluated.** The field engine applies structured
`show_if` only, so such a question is shown to **every respondent**. Creating a
fielding link now returns `unenforced_routing` naming those questions, and logs
a warning. Without it the failure is silent: the questionnaire looks routed,
the data comes back wrong, and nothing in between says so.

Resolve them in review (set the condition) before a real launch, or accept it
knowingly for a soft launch.

## Workflow 5 — Two-way reconciliation with the data map

The data map does not guess. It is the script's own record of what was fielded:
every variable name, the question text, every option label with its punch code,
and the hidden variables the script computed. Extraction reads a Word document
and infers. On a real client study the data map described **113 questions**
while extraction had produced **24**.

So the cross-check runs both directions before it reads a single row:

```
1. read the data map          440 variables, grouped into 113 questions
2. read the questionnaire     24 questions extracted from the document
3. reconcile both ways        13 in both · 105 only in the data map
                              · 11 only in the questionnaire (intros, transitions)
4. rebuild what was missed    105 questions, from the script's own text and punches
5. link                       121 questions covering 281 columns (was 16)
6. audit the conditions       against the punch lists, before any data is read
7. check the data             42 questions code-checked (was 9)
```

**Rebuilt questions are flagged, never silently merged.** They carry
`extraction_method="datamap"` and `RECOVERED_FROM_DATAMAP`, because their
wording comes from the script rather than the client's document and the two can
legitimately differ — hiding that would hide a real disagreement. Routing is
never invented: the data map records what was *collected*, not who was *asked*,
so a rebuilt question has no `show_if` and says so in `missing`.

**Links are carried, not re-derived.** A rebuilt question was made FROM named
columns, so its link is already exact. Sending it back through the fuzzy matcher
would make a multi-select re-guess the very columns it came from.

### Auditing conditions before touching data

A condition is only meaningful if the variable it names exists and the codes it
tests are real punches. When they are not, it is dead — false for every
respondent, for ever.

That failure is invisible where you would look for it: run the data check on a
dead screen-out and it reports **zero violations**, which reads exactly like a
screen-out nobody breached. So conditions are audited first:

| Finding | Meaning |
|---|---|
| `unknown_variable` | the condition names a question with no column at all |
| `unscriptable_code` | the codes tested are not punches on that variable |
| `no_codes` | an `in` test with an empty value list |

A condition where *some* codes exist is a warning (it still fires for some
respondents); one where *none* do is an error.

One subtlety that caused a wave of false positives: for a multi-select, **the
code is in the column name** — `DA_GOALSr98` is punch 98, and its value labels
are only `{0: Unchecked, 1: Checked}`. Reading just the value labels made every
punch beyond 1 look unscripted, producing 40 spurious findings on the real file.

### Derived companions: where an open question's codes actually live

An open-ended question rarely stands alone. The script derives companions from
it, and the questionnaire's conditions are written against **those**, not the
raw value:

```
SS_AD_SPEND              "how much do you spend?"              open number
  HID_SS_AD_SPEND        "Flag if greater than $1,000,000"     1 = Flag, 2 = Not
  noanswerSS_AD_SPEND_r99 "Prefer not to answer"               0 / 1

C_GEO_ZIP                "what ZIP code?"                      open text
  hC_GEO_ZIP_Flagged     "flag if respondent invalid US zip"   1 = Flagged
```

So "the column has no codes" and "the questionnaire lists bands for it" are
both true and not a contradiction. Where a companion exists the report says
`coded_in_companion` (info) and names it, instead of `type_mismatch` (warning).
On the real file that reclassified two of five findings correctly.

Detection is structural, never a list of known names: a derived variable whose
name contains an answerable variable's name, longest match winning, so
`HID_SS_AD_SPEND` attaches to `SS_AD_SPEND` and not to a shorter `SS_AD`. A
study-level variable like `Hid_Segment` names no question and is left
unattached rather than given an invented parent.

A derived variable is also recognised from its **label**, not only its name
prefix: `hC_GEO_ZIP_Flagged` is just "h" plus the question, but its label says
"Dummy variable to flag…". The declaration in the label is the more reliable
signal.

### When the variable name and the question id differ

They often do. The linker falls back to matching on question **text**, which is
right far more often than not — but it is a judgement, and a wrong one silently
checks the wrong column. Every text-matched link is returned as
`needs_confirmation` with both wordings and the score, for a human to settle.
It is never quietly accepted.

### The data map inside the extractor

Recovery used to run at cross-check time, which explained a gap *after* paying
for it. The extractor now consults the data map directly, when one exists —
i.e. when the data was uploaded before the questionnaire.

Three uses, in order of how much they save:

1. **Answer the parser's open questions.** A residue call for a question text,
   type or option list that the data map already records is a call paid for
   twice. Filled fields are cited as "the data map" in the provenance.
2. **Skip what was never asked.** A questionnaire commonly describes its hidden
   variables inline (`HID_COMP_SIZE: Hidden variable for company size`), and the
   reader treats each as a question, then pays a call to invent an answer type
   for something nobody was asked. The data map already knows they are computed.
3. **Add what the reader never found.** Questions the data map names and no
   chunk produced are rebuilt from the script's own text and punches.

Measured on the Google Ads study: **model calls 56 → 28**, nine fabricated
answers avoided, and 105 questions recovered that the reader never found.

Routing is never filled from the data map — it records what was *collected*,
not who was *asked* — so a base or terminate keeps escalating.

**No data map means no change.** The extractor behaves exactly as it did
before, which is asserted by a test.

### Straightlining is only measured on grids someone was asked

A survey export is full of columns that repeat by construction. Blocks of
script-computed flags are identical across items for most respondents because
that is what a computed flag does, and a MaxDiff exercise gets the same
position chosen task after task as ordinary behaviour — which is why scripters
write their own MaxDiff straightliner flag rather than relying on a generic
one.

Counting those as grids reported **89.3% straightlining** on a real
750-respondent study. Nobody acts on a number like that; it just teaches people
to ignore the panel. The single biggest contributor was `HID_AWARE`, flat
across items for 78% of respondents.

Grids are now built only from columns classified `question` or `punch`. Same
file: **89.3% → 16.4%**, across 20 genuine rating grids instead of 32 mixed
ones.

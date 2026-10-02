# How DVV Research Cloud works

Every technique, model and method in the system, from the simplest to the most
involved — what it is, why it exists, and how it works. Written for somebody who
has not seen the code and does not already know the jargon.


## Contents

| | Section | What it covers |
|---|---|---|
| 0 | [The problem](#0-the-problem-this-system-solves) | Why any of this exists |
| 1 | [Deterministic first](#1-the-governing-principle-deterministic-first) | The one rule everything follows from |
| 2 | [Reading the document](#2-reading-the-document) | Intake, segmentation, the two parsers |
| 3 | [Market research techniques](#3-market-research-techniques-the-system-understands) | Bases, masks, pipes, rotation, quotas, MaxDiff |
| 4 | [The AI layer](#4-the-ai-layer) | Models, RAG, the agent loop, grounding |
| 5 | [Serving a survey](#5-serving-a-survey-to-real-people) | Compilation, pinning, enforcement, dry run |
| 6 | [Data engineering](#6-data-engineering) | DuckDB, Parquet, SPSS, process isolation |
| 7 | [Statistics](#7-statistics) | Weighting, Kish, sig letters, effect size, power |
| 8 | [Verbatim coding](#8-verbatim-coding) | Turning open ends into countable themes |
| 9 | [Charts](#9-charts) | Why the model never touches pixels |
| 10 | [Quality control](#10-quality-control) | Speeders, straightliners, data vs questionnaire |
| 11 | [How the system learns](#11-how-the-system-learns) | The correction loop, recovery, re-extraction |
| 12 | [Machine learning and deep learning](#12-machine-learning-and-deep-learning) | Transformers, embeddings, hashing, clustering, local search — and what is *not* trained |
| 13 | [Python techniques](#13-python-techniques) | Dataclasses, generators, processes, regex, validation, testing |
| 14 | [Engineering techniques](#14-engineering-techniques) | Migrations, caching, licensing |
| 15 | [What it deliberately does not do](#15-what-the-system-deliberately-does-not-do) | Decisions, not omissions |
| 16 | [The measurement discipline](#16-the-measurement-discipline) | How claims here were checked |

---

## 0. The problem this system solves

A market research project has three artefacts:

1. **A questionnaire** — a Word document a human wrote, saying what to ask, who
   to ask it of, and what counts as a valid answer.
2. **Respondents** — people who answer it, usually bought from a panel company
   at a few dollars each.
3. **A data file** — one row per respondent, one column per answer, which an
   analyst turns into tables and a client turns into a decision.

Between those three sit a scripter, who turns the document into a working
survey, and a data processor, who turns the responses into tables. Both jobs are
hours of careful, repetitive, error-prone work, and both are usually done by
hand.

**The costly thing is not slowness. It is silence.** A mis-scripted screen-out
does not crash; it quietly lets the wrong people in. A question whose answer
list lost an option does not error; it produces a clean-looking distribution
over the wrong answers. By the time anyone notices, the sample is bought and
the field is closed.

So the governing design rule of this system is:

> **A wrong answer that looks right is worse than no answer at all.**

Almost every decision below follows from that one sentence.

---

## 1. The governing principle: deterministic first

### What it means

A *deterministic* method gives the same answer every time for the same input,
and you can follow its reasoning by hand. A regular expression is
deterministic. A language model is not — it is *probabilistic*, which means it
produces plausible output, and plausible is not the same as correct.

This system reads questionnaires with deterministic parsers first, and only
calls a language model for what those parsers could not resolve.

### Why

Three reasons, in order of importance:

1. **Auditability.** When a client asks "why does the system think Q7 is a
   multi-select?", the answer is "because the document says *Select all that
   apply*, at this line". That is checkable. "Because the model said so" is not.
2. **Silence is dangerous.** A parser that cannot read something *says so* — it
   records what is missing. A language model asked to read the same thing will
   invent an answer, confidently, in the correct format.
3. **Cost.** Roughly 64% of questions are fully resolved with no model call at
   all. On one real document, reading the structured parts first took the model
   call from 451,400 tokens to 14,264.

### How it works in practice

Each parser returns both an answer *and* a list of what it could not determine,
called `missing`. The caller escalates only the `missing` parts.

```
fields = parse_spec_table(chunk)        # try structure first
if fields is None:
    fields = parse_prose_question(chunk) # then wording conventions
if fields.missing:                       # only the residue goes to a model
    ask_the_model(fields.missing)
```

The rule stated in the code itself:

> "Deliberately CONSERVATIVE: anything uncertain is left None and named in
> `missing`, so the caller escalates to the LLM. A wrong deterministic answer is
> worse than an expensive right one — it is silent and looks authoritative."

---

## 2. Reading the document

### 2.1 Intake — getting text out of a file

**Word (`.docx`)** is a ZIP archive of XML. The library `python-docx` reads it.
We walk paragraphs and tables *in document order* — which matters, because a
grid question is a table sitting between two paragraphs, and losing that order
loses which question the table belongs to.

Three things are read that most tools ignore:

- **Bold and heading style** — a heading is often a section boundary.
- **Strikethrough and tracked deletions** — text the document shows as removed.
  On one real client document an entire question was struck out and would
  otherwise have been extracted and fielded to real people.
- **List formatting (`w:numPr`)** — Word stores list numbering in XML, so the
  number never appears in the text. An answer list can arrive as bare lines with
  no visible codes at all. On three real documents, 322, 270 and 29 paragraphs
  carry this marker.

**PDF** is harder, because a PDF has no concept of a paragraph — only glyphs at
coordinates. We use `PyMuPDF` and then reconstruct reading order ourselves:

- **Column clustering** — group text blocks by their x-position to detect a
  two-column layout, so a page is not read straight across the columns.
- **Reading-order sorting** — order blocks by column, then by y-position.

Why: PyMuPDF merges visually adjacent lines into one block. On a US Federal
Reserve questionnaire that collapsed about 100 questions into 4 blocks.

### 2.2 Segmentation — finding where one question ends

This is the single highest-stakes step. Everything downstream operates on
"chunks", and a chunk that is wrong is a question that is wrong.

A **boundary** is a line that starts a new question. The system knows four
conventions, because real documents use all four:

| Convention | Example | Where seen |
|---|---|---|
| Numbered labels | `Q7.` `S2)` `D4A.` | most documents |
| Spec tables | a Word table with a `Q Name:` row | programming houses |
| Section / instruction lines | `SECTION B`, `TERMINATE`, `SKIP TO` | most |
| Base tags, nothing numbered | `[BASE: ALL] Thinking about...` | healthcare / pharma |

The fourth is **gated**: it only applies to a document that numbers *nothing*.
In a numbered document the same `[BASE: ALL]` tag annotates the question above
it, so treating it as a boundary everywhere would split questions in half and
orphan their answer lists.

Why the gate matters: a questionnaire matching none of these becomes **one
headless chunk**, and the reviewer sees a single question whose text is the
entire document. That happened on a real uploaded study.

Two subtleties worth knowing:

- **Trailing directives are carried forward.** `[ASK IF Q3=1]` sitting after
  Q3's answer list belongs to Q4, not Q3. The segmenter pops those off the end
  of a chunk and prepends them to the next.
- **Bracket scanning is depth-aware.** Real directives nest:
  `[ASK IF (K2=1 OR [K14=2]) AND (D3=1)]`. A naive `\[[^\]]*\]` pattern stops at
  the first inner `]` and drops the rest.

### 2.3 Two parser families

**Spec-table parser** (`spec_table.py`) — for documents written as one table per
question, with a label column and a value column:

```
Q Name:      | SS_RESP
Q type:      | Std Single-Response (SR)
Among who:   | All respondents
Response list| 1: Yes  2: No
```

It reads *structure*, not prose. The extension mechanism is a dictionary of
label synonyms: `"q name"`, `"qname"`, `"question id"`, `"variable"` all mean
the same field; `"among who"`, `"base"`, `"universe"`, `"asked of"` all mean the
base. **Supporting a new client's wording is a dictionary entry, never a code
change.** Unrecognised labels are kept rather than dropped, so an unknown
convention is visible instead of silently lost.

**Prose-directive parser** (`prose_directives.py`) — for documents written as
sentences, where the conventions are *scripter shorthand* that is exact where
present:

- **Type instructions**: "select all that apply" → multi-select; "select one" →
  single; "NUMERIC OE" → a number box. Ordered most-specific-first, and numeric
  checked before open-text because `"NUMERIC OE"` and `"OE"` both contain "OE".
- **Punch abbreviations**: `SP` (single punch) / `MP` (multi punch) — checked
  **only inside brackets**, because two-letter tokens appear innocently in brand
  names and ordinary prose.
- **Option tags**: `Healthcare [TERMINATE]`, `None [ANCHOR, EXCLUSIVE]` — every
  trailing bracket group, not just the last one.
- **Inline bases**: `D3. [ASK IF C1=18+] What is your marital status?` On two
  real studies, not reading these lost most of the routing — one had twelve
  questions stating a base and none of them applied.
- **Select limits**: "Select up to 2", "Rank your top three", in digits or
  words.

### 2.4 Survey-level passes

A single question cannot see the document around it. Eleven further passes run
after all questions are read:

| Pass | What it resolves |
|---|---|
| `section_paths` | `IF S0=1 ASK P1-P3` — a routing rule covering a whole block |
| `intro_screens` | A paragraph that introduces the next question, not an answer |
| `split_subquestion_grids` | `Q2a/Q2b` written as one grid, fielded as two |
| `carousel` | The same question repeated per brand |
| `constant_sum` | "Allocate 100 points across these" |
| `swallowed_options` | An answer list absorbed into the question wording |
| `per_item_grids` | "Ask the following for EACH product" |
| `fill_lists` | `[SHOW Q26 LIST]` — an answer list borrowed from elsewhere |
| `loops` | A block repeated per item |
| `trade_off` | Detects MaxDiff / conjoint and refuses to field them |
| `block_notes` | Scripter notes that group or rotate whole blocks |

### 2.5 Document profiles

Before extraction, the system counts structural signals — `Q Name` table rows,
prose labels, bare numbers — and scores which convention best explains the
document. That score selects a prompt hint for the model.

Below a confidence floor of 0.5 it falls back to `UNKNOWN_PROFILE`, which keeps
behaviour **byte-identical** to having no profile at all. The rule in the code:
*"the profile step is never allowed to make an unrecognised document worse."*

---

## 3. Market research techniques the system understands

These are the domain concepts. If you do not work in survey research, these are
the ones worth reading carefully — they are what makes a questionnaire more than
a form.

### 3.1 Base (also: ask condition, `show_if`)

**What:** who sees a question. "Ask only those aware of the brand."

**Why it matters:** if a base is not applied, people are asked questions that do
not apply to them. Their answers are meaningless, and worse, they land in the
same column as the meaningful ones.

**How:** a base is parsed into an `Expression` — a small tree of `eq`, `in`,
`and`, `or`, `not` — which can actually be *evaluated* against a respondent's
answers. That is the difference between recording that a rule exists and being
able to run it.

### 3.2 Screen-out (terminate)

**What:** a condition that ends the interview. "Terminate if they work in market
research."

**Why:** it protects sample quality, and it is the denominator of *incidence* —
the share of people who qualify, which is what the client is buying.

**How:** also an `Expression`, evaluated **server-side** after every page. Never
in the browser, because the browser belongs to the respondent.

### 3.3 Masking

**What:** showing only the options a respondent chose earlier. "Of the brands
you said you were aware of, which have you used?"

**Why:** asking about a brand somebody has never heard of produces noise, and
inflates the apparent usage of brands nobody knows.

**How:** recorded as a *technique* on the question (`source_qid`, mode), and
cross-checked against the data afterwards: did the respondents who saw brand 7
at the masked question actually select brand 7 at the source question?

### 3.4 Piping

**What:** inserting an earlier answer into later text. "You said **Nike** was
your favourite — why?"

**Why:** it makes a survey feel like a conversation and removes ambiguity.

**How:** compiled into the survey platform's own interpolation syntax
(`{Q5}`), with the source question recorded so the cross-check can confirm the
pipe had something to fill it.

### 3.5 Anchoring and exclusivity

**Anchor:** an option that stays in place when the list is randomised — "None of
these" must not appear in the middle.

**Exclusive:** selecting it clears everything else — you cannot be aware of
three brands *and* "none of these".

**How:** exclusivity is enforced on the **server**, not in the browser, because
SurveyJS has no per-choice exclusivity to configure and the client is the
respondent's. A multi-punch carrying both "Ulta" and "None of the above" is
contradictory data that would otherwise reach the tables.

### 3.6 Randomisation and rotation

**Randomise:** shuffle the answer options, so the first option does not win by
position alone (a real and well-documented bias).

**Rotate:** change which *block* comes first. If a study compares Google Ads and
Meta Ads, whichever is asked about first is rated differently.

**How, and this is the interesting part:** rotation is **assigned, not
shuffled**. Each respondent takes the arm that is currently behind — a
"least-fill" rule. A fair coin on 500 respondents lands 54/46 often enough to
bias the very comparison the rotation exists to protect.

And the arm is **recorded** as a variable in the data (`HID_BLOCK_ORDER`). A
rotation the analyst cannot see is nearly as bad as no rotation, because they
cannot control for order.

### 3.7 Quotas

**What:** "we need 400 teens, 400 college students, 400 young professionals."

**Why:** the client is buying a sample shape, not just a sample size.

**How, and what is deliberately *not* done:** quota tables are parsed from the
document and **never applied automatically**. A quota cell needs a condition
against a real question, and "2-9 Employees" is a *definition*, not an
expression. Guessing that mapping would silently field the wrong study. Instead
the system checks that the instrument contains a question capable of sorting
somebody into each quota group — a cell nobody can count cannot be capped.

### 3.8 Hidden / derived variables

**What:** a column the script computes rather than asks. `HID_GOOGLE_USER = 1`
when somebody said they advertise on Google. Banners are cut by these.

**Why they are tricky:** they look exactly like questions in the document, but
nobody is ever asked them.

**How they are detected** — four signals, in falling certainty:

1. A `HID_` prefix on the name.
2. A spec table saying so outright (`Show / HV: Hidden variable`).
3. A programmer note saying so (`[PN: HIDDEN VARIABLE]`).
4. All-caps `CODE`/`RECODE`/`SPLIT` wording, e.g. `C1. CODE AGE – BASED ON S1`.

The fourth leans on one test: **no lowercase**. These documents shout
instructions at the scripter and speak to respondents in sentence case, so
"Code the item you bought" stays a real question.

Signal 3 matters more than it looks: the name tells you nothing. "AGE" is a
question in one study and a hidden recode in the next, and only the note beside
it distinguishes them.

**What is deliberately not done:** the derivation is captured from the
document's own `Definition:` column (`HID_GEO_DIVISION = 1,2`) and **never
executed**. Of 36 such cells in one real document only 10 are a plain
`VAR = codes`; the rest say things like `SS_CHANNELS = 2 and/or 4 ONLY (no
others selected)`. Compiling those half-right would put numbers in a banner
variable that nobody authored.

### 3.9 MaxDiff (best–worst scaling)

**What:** instead of rating twenty features 1–5 (where everybody rates
everything "important"), each respondent sees four at a time and picks the best
and the worst, over about twelve screens.

**Why:** ratings suffer from scale-use bias — some people use the top of the
scale, some the middle, and you cannot compare them. Forced choice removes it.

**How — the design is the measurement:**

- Each respondent sees a different *subset*, drawn from a **balanced incomplete
  block design**.
- "Balanced" means every item appears equally often, and every *pair* of items
  co-occurs about equally often. An item shown more often than another
  accumulates wins for that reason alone; a pair that never shares a screen is a
  comparison the model has to infer entirely.
- A textbook balanced design only exists for certain combinations of (items,
  screens, items-per-screen), and studies choose those numbers for research
  reasons. So the system builds a *near*-balanced design and **reports the
  balance it actually achieved** rather than the balance it was aiming at.

Measured on 20 items / 12 screens of 4 / 10 versions: item frequency exactly 24
each, pair co-occurrence 3–5, **zero** pairs that never meet, and each
respondent sees each item 2 or 3 times.

Balance is needed at two levels that can disagree — across the whole sample, and
within one person's twelve screens. Optimising one breaks the other, which is
why the deal is version-aware and the leftover slots go by least-fill.

---

## 4. The AI layer

### 4.1 Which models, and why these

The system runs **open-weight models only**, and nothing made by OpenAI. The
reason is commercial, not ideological: a client bundle is shipped to the
client's own infrastructure, and a dependency on a single commercial API would
be a dependency they cannot audit, price or replace.

| Role | Model | Where |
|---|---|---|
| Extraction and copilot | Qwen 3 32B | Amazon Bedrock, in the client's region |
| Same, fallback | Qwen 3 | Groq, then Cerebras |
| Local fallback | Llama 3.2 3B | Ollama, on the client's own server |
| Embeddings | `nomic-embed-text` | Ollama, local by design |

The chain is tried in order and the first that answers wins. Embeddings are
local **because the text being embedded is client content** — question wordings
and respondent verbatims — and that should not leave the client's machine.

### 4.2 Structured output

The model is never asked for prose when a structure is wanted. It is given a
**JSON schema** and its reply is parsed against it. A reply that does not fit
the schema is a failure, not a partial success.

Why: "the type is probably single-select" is unusable. `{"type": "single"}` is
either valid or it is not.

### 4.3 Retrieval-augmented generation (RAG)

**What:** before answering a question about a study, find the most relevant
pieces of that study and put them in the prompt.

**Why:** a model has no knowledge of your study. Without retrieval it either
refuses or invents.

**How, specifically:**

- **Embeddings.** Each question and each respondent verbatim is turned into a
  vector — a list of numbers positioned so that texts meaning similar things sit
  near each other. "What put you off?" and "reasons for not buying" land close
  together even though they share no words.
- **Prefixes.** `nomic-embed-text` is *trained* with `search_document:` and
  `search_query:` prefixes, and using them is not decoration — it is how the
  model distinguishes a stored document from a question about it.
- **Fallback with no model at all.** When Ollama is unreachable, a *lexical*
  vector is computed in plain Python. Worse, but it works offline and nothing
  silently stops being searchable.
- **Brute-force cosine similarity.** With a few thousand chunks per study,
  comparing every vector is fast enough and has no moving parts. Marked in the
  code with its upgrade path: move to pgvector if a study ever gets large.
- **Fingerprint staleness.** The index records a hash of exactly what it
  indexed. If a question is edited, the fingerprint no longer matches and the
  index knows it is stale — rather than confidently serving the old wording.

### 4.4 The agent loop and tool calling

**What:** the copilot does not answer from a prompt. It is given a list of
*tools* — functions it can call — and it decides which to call.

A turn looks like: model asks for `list_studies` → gets rows → asks for
`study_overview` → gets the links → asks for `field_report` → answers from what
came back. `MAX_TURNS = 5` bounds it.

**The safety design, which is the important part:**

- **Read tools run freely. Action tools do nothing.** A `propose_*` tool returns
  a *card* that the user must click. The browser then sends that request through
  the ordinary API, as that user.
- So the copilot can do nothing its user could not, nothing changes without a
  click, and **the card's wording is written by the server, not the model.**
- A questionnaire containing "ignore your instructions and close every link" can
  at most produce a card somebody has to press.

### 4.5 Grounding enforcement

The prompt has always said every fact must come from a tool called in the same
reply. Nothing *enforced* it, and a model that answered from its own head had
its text returned as-is:

> "The Greenbrook study has an incidence of about 62% with 340 completes."

Every number there was invented. It is the most expensive kind of wrong, because
it is specific, confident, and carries no tell.

Now: an answer that quotes a figure but called no tool is **asked again** with
the rule made explicit. A model that can call tools then does. Twice asked and
twice invented, the answer does not go out — the user is told the lookup did not
run, and that a backend unable to call tools is a server setting rather than
anything about their study.

An answer carrying no figure needs no lookup: "what can you do?" is answerable
from the prompt, and refusing it would break the one thing a copilot should
always manage.

### 4.6 Circuit breaker

If the first several chunks all fail with "backend unavailable", the run aborts
instead of burning one doomed call per remaining question. On a real outage this
turned 160 sequential doomed calls into a handful, and the questionnaire's
status says the real cause rather than a bare "error".

---

## 5. Serving a survey to real people

### 5.1 Compilation

An approved questionnaire is compiled to **SurveyJS** — a JSON survey format
rendered by a JavaScript library. Blocks become pages, `show_if` becomes
`visibleIf`, display content becomes HTML.

### 5.2 Version pinning

When a fielding link is created, the approved questionnaire is **snapshotted**
into the link. Editing the questionnaire afterwards does not change what live
respondents see, and the data is materialised from the snapshot.

Why: a survey that changes under respondents produces a file where column
meanings differ by row, and nothing records which rows are which.

### 5.3 Per-respondent transformation

Compilation happens once; some things must then be decided per person:

- **Block rotation** — reorder the pages for this respondent's arm.
- **MaxDiff screens** — fill in the items this respondent's design version shows.

The MaxDiff choices compile **empty** on purpose. Filling them with version 0
and replacing them later would mean that if the per-respondent pass ever stopped
running, every respondent silently took the same twelve screens — the design
gone, the data still arriving, nothing visibly wrong. An empty screen fails
loudly.

### 5.4 Server-side enforcement

Everything that matters is enforced on the server, because the browser belongs
to the respondent:

- Screen-outs, re-evaluated against all accumulated answers after every page.
- Exclusive options — co-selections trimmed.
- Select caps — a page over the cap is **refused**, not trimmed, because
  keeping the first two of four picks an answer the respondent never gave.
- MaxDiff answers naming an item that was not on that respondent's screen.

### 5.5 Dry run

Before any real respondent, synthetic ones are walked through the survey using
**the same decision functions the live route uses**. Not a second
implementation — one that reimplemented the rules would be free to drift, and a
clean dry run would then mean nothing.

It answers: which screen-outs never fire, which bases hide a question from
everybody, which masks never narrow anything, which quota fills at respondent 40
of 500.

### 5.6 Preflight

Everything checked once more at the moment a link is created, because from then
on every problem is paid for in respondents. Two severities only:

- **FAIL** — the survey does not do what the document says. The link is refused
  unless somebody overrides it deliberately.
- **WARN** — probably fine and worth a look.

---

## 6. Data engineering

| Choice | What it is | Why |
|---|---|---|
| **DuckDB** | An in-process analytical database | Runs SQL over a file with no server. Column-oriented, so a crosstab over 1,500 rows and 700 columns is instant. |
| **Parquet** | A columnar file format | Reads only the columns a query needs. Keeps types, so a missing value stays missing rather than becoming `NaN`. |
| **Polars / pandas** | DataFrame libraries | Polars for speed on the write path, pandas where a library demands it. |
| **pyreadstat** | Reads and writes SPSS `.sav` | SPSS is still the lingua franca of market research delivery. |
| **openpyxl** | Reads and writes Excel | Client data arrives as `.xlsx`, and deliverables go back as `.xlsx`. |
| **rapidfuzz** | Fast fuzzy string matching | Matching a question's text to a variable's label when the ids do not match. |

Two details that are not obvious:

**Declared missing values.** In SPSS, a code like `99 = "Prefer not to answer"`
must be *declared missing*, or every statistic includes it. An undeclared 99 on a
1–2 scale produced a mean of 25.75. The exports declare them.

**Process pool with `spawn`, not `fork`.** Heavy work runs in separate
processes. `fork` copies a process including its threads and open database
connections, which deadlocks; `spawn` starts clean. Measured finding: adding
more workers does not help, because the bottleneck is elsewhere.

---

## 7. Statistics

### 7.1 Crosstabs

A crosstab is the core deliverable: rows are answers to one question ("stub"),
columns are groups of respondents ("banner"), cells are percentages.

### 7.2 Weighting

**What:** if your sample is 70% male and the population is 50%, each female
respondent is counted as more than one person to correct the imbalance.

**Why:** an unweighted result describes your sample; a weighted one estimates
the population.

### 7.3 Effective base (Kish)

**The problem:** once you weight, you no longer have 1,000 independent
observations. Some respondents count for more, which makes the result less
stable than the raw count suggests.

**The correction:** Kish's effective sample size

```
n_effective = (Σw)² / Σw²
```

If everybody has weight 1, this returns the raw count. The more unequal the
weights, the smaller it gets. All significance testing uses this, not the raw
base — otherwise weighted results look more certain than they are.

### 7.4 Significance letters

**What:** each banner column gets a letter. A cell shows the letters of the
columns it is *significantly higher than*. This is the convention every tab shop
and every analyst already reads.

**How:** a **pooled two-sample proportion z-test** between each pair of columns
in the same banner variable, on effective bases:

```
pooled    = (p₁·e₁ + p₂·e₂) / (e₁ + e₂)
variance  = pooled · (1 − pooled) · (1/e₁ + 1/e₂)
z         = (p₁ − p₂) / √variance
```

The critical value is computed from the normal distribution
(`scipy.stats.norm.ppf`), not hardcoded — 1.96 at 95% confidence, 1.645 at 90%.

**Guard:** a column below a minimum effective base is never tested and never
printed. A significant-looking difference on a base of 12 is noise with a letter
next to it.

### 7.5 Table-level statistics

Letters tell you *which columns differ*. They do not tell you whether the table
as a whole shows an association, how big it is, or whether the sample was large
enough to find one. Each table therefore also carries:

| Statistic | What it answers |
|---|---|
| **Chi-square** | Is there any association between stub and banner at all? |
| **Fisher's exact** | The same question, used instead when a cell count is too small for chi-square to be valid |
| **p-value** | How likely a difference this large is if nothing is really going on |
| **Cramér's V** | *How strong* the association is, 0 to 1 — significance says a difference exists, effect size says whether it matters |
| **Wilson confidence intervals** | A range for each cell percentage. Preferred over the textbook interval because it stays sensible near 0% and 100%, where the usual formula gives impossible bounds |
| **Statistical power** | The chance this sample could have detected a real difference. Low power plus a null result means "we could not tell", not "there is no difference" |
| **Minimum cell n** | The smallest unweighted count in the table, so a reader can see the weakest thing it rests on |

### 7.6 Guardrails: numbers the model may cite but never compute

These are bundled into a frozen `CrosstabGuardrail` attached to each table, and
the reason is specific:

> "These pre-computed values prevent the LLM from fabricating statistical
> claims (e.g. `p < 0.05`). The LLM must cite these values, never compute them."

A language model will happily write "significant at p < 0.05" because that
sentence is extremely common in its training data — not because it ran a test.
Precomputing the real values and requiring citation turns an invented claim into
a checkable one. It is the same principle as the copilot's rule against doing
its own arithmetic, applied one layer down.

---

## 8. Verbatim coding

### What an open end is, and why it is hard

Most survey questions have a fixed answer list. An **open end** does not — the
respondent types whatever they like. "What put you off the brand?" produces a
thousand different sentences saying perhaps fifteen different things.

Turning those sentences into countable categories is **coding**, and it is
traditionally done by a person reading several hundred responses, inventing a
list of themes, and then tagging every response against it. It is slow, it is
the least enjoyable job in the industry, and two coders given the same data
produce slightly different answers.

### How the system does it

**Two stages, deliberately separated.**

1. **Codeframe generation.** A model reads a *sample* of verbatims and proposes
   a set of codes. A codeframe here is structured rather than a flat list:
   - **Hierarchical** — a parent code ("Product Quality") with children
     ("Texture", "Scent", "Longevity"), so a client can read the summary at
     either level.
   - **Typed** — `theme` (what it is about), `sentiment` (positive, negative,
     neutral, as an overlay rather than a separate code), `action` (something
     the client should do about it).
   - **Multi-coding** — one verbatim can carry several codes, because one
     sentence often says two things.

2. **Application.** The codeframe is then applied to *every* verbatim, in
   batches, with a **confidence score per assignment**. A low-confidence
   assignment is a thing a human can be pointed at, rather than something
   buried in a count.

### Why they are separated

Generating and applying in one pass would let the frame drift — the categories
would quietly change shape as the model worked through the file, and the counts
at the end would be over a definition that no longer matches the one at the
start. Fixing the frame first means every verbatim is judged against the same
set, which is exactly what makes the counts comparable.

### The codeframe library

Approved codeframes are stored and reusable. A brand tracker running its fourth
wave should code against the same frame as the first three, or the trend line is
measuring the coder rather than the market.

---

## 9. Charts

The chart engine does not receive numbers. It receives a **variable name**,
computes the crosstab through the same deterministic path the tables use, and
renders that.

Why this indirection matters: it makes a chart that disagrees with the table
impossible. If the model could hand pixels or percentages to a renderer, a chart
and its own table could tell different stories in the same deck, and the chart
would be the one people remember.

Rendered with matplotlib on the `Agg` backend — a non-interactive renderer that
writes images without needing a display, because this runs inside a web request
and must never try to open a window.

---

## 10. Quality control

Every check here is deterministic. A finding a human cannot reproduce by hand is
worse than no finding.

### 10.1 Respondent-level checks

| Check | Rule | Why |
|---|---|---|
| **Speeders** | Interview under ⅓ of the median length | Someone clicking through is not reading |
| **Straightliners** | Zero variance across a grid of ≥4 items | "Agree" to 8 statements is a pattern, to 3 is an opinion |
| **Duplicates** | Same respondent twice | The commonest panel fraud |
| **Junk verbatims** | Keyboard mash, single characters | Open ends are where effort shows |

The threshold choices are stated and conservative. Straightlining needs ≥4
items; speeders need a stable median before a threshold is derived at all.

### 10.2 Data-versus-questionnaire checks

The job a DP shop is paid for: the questionnaire is the contract, the data is
what came back.

| Check | Finds |
|---|---|
| `undeclared_code` | A value in the data outside the question's declared options |
| `single_multi` | Two answers to a single-answer question |
| `out_of_range` | A number outside the stated range |
| `terminate_violated` | A respondent the screen-out matched who kept answering |
| mask / pipe checks | A mask that did not narrow, a pipe with nothing to fill it |
| routing cross-validation | Rows where a question was answered by somebody the base excludes |

**Why `checked` is reported alongside the findings:** before a 2026 fix, every
extracted screen-out evaluated false for every respondent — so this check would
have reported a clean bill of health on any file, for ever. "Nothing found" and
"nothing looked at" must never read the same.

### 10.3 Conditions audited before any data

A routing condition is only meaningful if the variable it names exists and the
codes it tests are real punches. When they are not, the condition is dead — it
evaluates false for everybody, for ever, and the data check then reports **zero
violations**, which reads exactly like a screen-out nobody breached.

### 10.4 Using the data to find parser gaps

The newest and, in some ways, the most useful idea in the system.

The cross-check can only verify the logic that extraction found. On a real
79-question study the parser produced **six** bases — and the data file showed
**eleven distinct answer populations**.

So the data is used to prove the parser wrong:

- Among people who **completed**, a question answered by some and not others was
  not skipped — it was gated.
- Item non-response looks nothing like that: scattered, a few people per
  question, never the same people twice. **A base produces a population, and a
  population is exact.**
- Questions are grouped by *who* answered, not how many — two questions with 403
  answers each are one audience only if they are the *same* 403.

On the Bubble study: B1–B6 share exactly 345 respondents, which is 1551 minus
the 1206 who answered S1. On Google Ads it flagged nine questions whose spec
tables plainly say `Among who: Google Ads Current User (HID_GOOGLE_USER = 1)` —
a base written down in a labelled cell that the parser does not read.

Nothing is inferred about *what* the condition is. The data shows a gate exists
and who came through it; only the document can say why.

---

## 11. How the system learns

### 11.1 The correction loop

When a reviewer corrects a question, two things can be learned:

1. **A prose rule** appended to the extraction prompt. Makes the model more
   accurate, and never cheaper — the same convention on the next document still
   costs a model call, every time, forever.
2. **A pattern the parsers read directly.** The second document using that
   convention is parsed for free.

The measurable claim is **cost per document falling as founders work**, not just
accuracy rising.

Six kinds of pattern, one per decision the parsers actually make: question type,
hidden-variable phrasing, option tags, randomisation wording, field labels, and
range terminates.

**Safety:** patterns use **literal substring matching, never user-supplied
regular expressions**. A regex from a text box is both a denial-of-service risk
and an unreadable rule. Patterns are also scoped per client profile, so one
client's convention never leaks into another's document.

### 11.2 Recovery from the data map

A data map is the script's own record of what was fielded — every variable, its
question text, every option label with its punch code. It does not guess.

A question the data map describes and extraction missed can be **rebuilt**: its
text and options are already known, exactly as fielded. On one real study, 24
questions were read from the document and 105 more recovered this way.

Rebuilt questions are **marked, never silently merged** — they carry
`extraction_method="datamap"` and a flag, because the script's wording and the
client's document can legitimately differ, and hiding that would hide a real
disagreement.

Routing is never invented: a data map records what was *collected*, never who
was *asked*.

### 11.3 Re-extraction

The uploaded document is archived in the database. An extraction is a snapshot
taken by the parser of the day it ran, so every fix since then reaches an
existing study **only on re-extraction**.

This means a study can be broken in a way that is already fixed, and nothing in
the stored definition can tell you that. Re-segmenting the archived source can —
which is what the copilot's `extraction_diagnosis` does.

Re-extraction creates a **new version** rather than overwriting, so it is safe
to run on a study somebody has spent hours reviewing.

---

## 12. Machine learning and deep learning

This section exists because "AI-powered" is usually left vague. Here is exactly
which machine learning is used, which is not, and why.

### 12.0 The honest headline: nothing here is trained

There is no `scikit-learn`, no PyTorch, no TensorFlow, no `.fit()` anywhere in
this codebase. The system **trains no model and fine-tunes no model**. It uses
pre-trained models for *inference* — you hand them text, they hand back text or
numbers — and everything else is deterministic code.

**Why not train?** Three reasons, and they are the same reasons as everywhere
else in this document:

1. **You cannot audit a weight.** When a client asks why Q7 was read as a
   multi-select, "the document says *Select all that apply* at line 412" is an
   answer. "A model trained on 8,000 questionnaires assigned it probability
   0.87" is not, and in research the provenance of a number is the product.
2. **Training needs labelled data that does not exist.** Three real client
   questionnaires is a measurement set, not a training set. A model fitted to
   three documents would learn those three documents.
3. **A client bundle ships to the client's own machine.** A trained artefact is
   a thing to version, retrain, and explain the drift of — permanently, per
   client.

What the system does instead is **encode domain knowledge as rules**, learn
through the correction loop (§11.1), and use general-purpose pre-trained models
for the residue.

### 12.1 Transformer language models

**What a transformer is.** A neural network architecture that reads a sequence
of *tokens* (word fragments) and predicts the next one. Its central mechanism is
**attention**: when processing any token, the model computes how much every
other token in the context should influence it. That is what lets it connect
"it" in one sentence to a noun three sentences earlier — and, here, connect
`[ASK IF S2=1]` to the question it governs.

**Which ones:** Qwen 3 32B (32 billion parameters) as the main model, Llama 3.2
3B locally. "32B" is the parameter count — roughly, the number of learned
numbers inside it. Bigger generally means better reasoning and more cost.

**How they are used here — inference only, three ways:**

1. **Structured extraction.** Given a question's text and a JSON schema, return
   the fields. Not "tell me about this question" — a schema the reply is parsed
   against, where a reply that does not fit is a failure rather than a partial
   success.
2. **Tool calling (also: function calling).** The model is given machine-readable
   descriptions of functions it may call, and it replies with a *structured call*
   — `{"name": "study_overview", "arguments": {"study_id": "..."}}` — rather than
   prose. The system executes it and feeds the result back. This is how the
   copilot reaches real data.
3. **Verbatim coding.** Proposing a codeframe, then classifying open ends
   against it.

**Tokens and why they matter commercially.** Models charge by token, and a token
is about ¾ of a word. The whole deterministic-first design is visible in token
counts: reading the structured parts of one 74-question document first took the
model call from 451,400 tokens to 14,264 — about a 97% reduction.

**Context window.** The maximum tokens a model can consider at once. This is why
extraction works on *chunks*: a 90-question document in one prompt would be both
expensive and worse, because attention is diluted across irrelevant text.

**Temperature and non-determinism.** Models sample from a probability
distribution, so the same prompt can give different answers. This is exactly why
`extraction_method` is recorded per question, and why the regression measurement
excludes LLM-touched rows — they flip run to run on identical code.

### 12.2 Embedding models and vector search

**What an embedding is.** A function that turns a piece of text into a fixed-
length list of numbers — a *vector* — positioned so that texts meaning similar
things sit near each other in that space. "What put you off?" and "reasons for
not buying" land close together despite sharing no words.

This is the key difference from keyword search. Keyword search matches
characters; embedding search matches *meaning*. For verbatims — where every
respondent phrases the same thought differently — that is the whole game.

**The model:** `nomic-embed-text`, an Apache-2.0 open-weight encoder, run
locally through Ollama. Local **by design**: the text being embedded is client
question wordings and respondent verbatims, and that should not leave the
client's machine.

**Task prefixes.** This model is *trained* with `search_document:` and
`search_query:` prefixes. Using them is not decoration — it is how the model
distinguishes "this is a thing to be found" from "this is somebody looking".
Omitting them measurably degrades retrieval, because the model was taught to
expect them.

**Cosine similarity.** To compare two vectors, take the cosine of the angle
between them: 1.0 is identical direction, 0.0 unrelated. Direction rather than
distance, because a longer text produces a longer vector and length is not
meaning.

Vectors are **L2-normalised** on storage — divided by their own length — which
makes the cosine a plain dot product, so comparison is a multiply-and-add.

**Brute-force search.** Every stored vector is compared against the query. With
a few thousand chunks per study this is milliseconds and has no moving parts —
no index to build, no index to go stale. Marked in the code with its upgrade
path: move to pgvector if a study ever gets large enough to need one.

### 12.3 Feature hashing — the no-model fallback

When Ollama is unreachable, embeddings are still produced, with **no model at
all**. The technique is the **hashing trick** (feature hashing), and it is worth
understanding because it is a genuinely clever classical method:

1. Take every word in the text, and every character *trigram* (three-letter
   run) inside those words.
2. Hash each one with blake2b into a fixed number of buckets.
3. Count what lands in each bucket — words weighted 1.0, trigrams 0.5.
4. Take the square root of each count, then L2-normalise.

**Why hashing instead of a vocabulary?** A normal bag-of-words model needs a
dictionary mapping every known word to a position, which must be built, stored
and kept in sync. Hashing needs none: the hash *is* the position. Collisions
happen and are tolerable, because a collision adds a little noise rather than
losing a word.

**Why character trigrams?** They survive typos and inflection. "moisturiser",
"moisturizer" and "moisturising" share most of their trigrams while being three
different words to an exact matcher. This is what makes the fallback tolerant of
how respondents actually type.

**Why the square root?** Sublinear scaling — the same damping idea behind TF-IDF.
A word appearing ten times is more relevant than one appearing once, but not ten
times more relevant, and without damping one repeated word dominates the vector.

It is worse than a real embedding model. It also works offline, costs nothing,
and means nothing silently stops being searchable.

### 12.4 Fuzzy string matching

When a question's id does not appear in the data file, the linker falls back to
matching on *text*: the question's wording against the variable's label.

**Levenshtein distance** — the minimum number of single-character insertions,
deletions or substitutions to turn one string into another. "colour" to "color"
is 1.

**`token_sort_ratio`** (from `rapidfuzz`) — the specific scorer used. It splits
both strings into words, sorts them alphabetically, rejoins and *then* compares.
This makes it insensitive to word order, so "age of respondent" and "respondent
age" score as near-identical. Scored 0–100, with an acceptance threshold.

**Why `rapidfuzz` and not `fuzzywuzzy`:** it is a C++ implementation with no
compiled Python dependency chain, which matters for a bundle that must install
cleanly on a client's machine.

**And the honest part:** every fuzzy link is surfaced for a human to confirm
rather than quietly accepted. A wrong link silently checks the wrong column.

### 12.5 Clustering — reading a PDF's columns

PDF text has coordinates, not structure. To detect a two-column page, the left
x-positions of text blocks are clustered.

The algorithm is **sequential leader clustering** (single-pass greedy
agglomerative): walk the sorted x-positions; put each into the first existing
cluster whose mean it is within a threshold of, otherwise start a new one. The
threshold is 15% of page width, so incidental indentation does not read as a
second column.

**Why not k-means?** k-means needs you to say *k* — how many clusters — in
advance, and the number of columns is exactly what is unknown. A threshold-based
method discovers the count from the data.

### 12.6 Combinatorial optimisation — the MaxDiff design

Building a balanced incomplete block design is a search problem: of all the ways
to deal 20 items into 12 screens of 4, find one where pair co-occurrence is as
even as possible.

**The approach is local search (hill climbing):**

1. **Construct** a starting design that is already frequency-balanced, by
   dealing items round-robin so no item is used twice before all are used once.
2. **Repair** any screen that got the same item twice, by trading within the
   same version so no count changes.
3. **Improve** by repeatedly proposing a random swap of two items between two
   screens, keeping it if it lowers the cost, undoing it if not.

**The cost function** is the sum of squared deviations of pair co-occurrence
counts from perfectly even. Squared, so one badly-served pair matters more than
several slightly-off ones.

**Delta evaluation** is what makes it fast enough. Rescoring the whole design per
candidate swap would be thousands of operations; instead only the pairs touching
the two moved items change, so each swap is scored by computing the *difference*
— about six pair updates. That is the difference between a few hundred attempts
and enough of them to matter.

**Determinism** comes from a seeded random number generator. Same parameters and
seed, same design, every time — because a design that differed between the
preflight check and the live link would make the check meaningless.

### 12.7 Greedy balancing — least-fill assignment

Used for rotation arms and MaxDiff versions: each new respondent takes whichever
arm currently has fewest people.

This is a **greedy online algorithm** — it decides with no knowledge of the
future and never revisits. For balancing it is also optimal: the maximum
difference between any two arms never exceeds one. A random draw, by contrast,
is balanced only *in expectation*, and a fair coin on 500 respondents lands
54/46 often enough to bias the comparison the rotation exists to protect.

### 12.8 Set identity by checksum

The base-gap detector needs to know whether two questions were answered by the
*same* people, not merely the same number of people.

Holding the respondent sets and intersecting them would be correct and slow.
Instead each question's answering set is reduced to a pair — `(count, sum of row
numbers)` — and sets with the same pair are treated as equal.

This is a **fingerprint**: a cheap value that is equal when the inputs are equal.
Different sets can collide in principle; in practice two different sets of 403
respondents having the same row-number sum is vanishingly unlikely, and the
consequence of a collision is two audiences reported as one — a merged finding,
not a wrong one.

---

## 13. Python techniques

The language features and library patterns the codebase leans on, and why each
was chosen.

### Data modelling

**`@dataclass`** (48 modules). A decorator that generates `__init__`,
`__repr__` and `__eq__` from type-annotated fields. Used instead of plain
dictionaries so that a typo is an `AttributeError` at the line that made it,
rather than a `None` discovered three layers later.

**`@dataclass(frozen=True)`** (12 places). Immutable — assigning to a field
raises. Used for things that must not change after construction: a generated
MaxDiff design, a statistical guardrail. If a value is a *fact* rather than a
*state*, freezing it means no later code can quietly edit it.

**`StrEnum`** (12 places). An enum whose members *are* strings, so
`QuestionType.MULTI == "multi"` is true and it serialises to JSON with no
conversion. You get a closed set of valid values and a readable wire format at
once — a typo'd `"mutli"` fails at the enum, not in the database.

**`frozenset`** for constant sets. Immutable and hashable, so a module-level set
of valid values cannot be mutated by accident from somewhere else.

### Typing

**`from __future__ import annotations`** — in nearly every module. Makes Python
treat annotations as strings rather than evaluating them at import. Two
benefits: forward references work (a class can reference itself), and circular
imports between modules that only need each other's *types* stop being a
problem.

**`X | None`** rather than `Optional[X]` — the modern syntax, and it reads as
what it is: this is an X or it is nothing.

### Performance and laziness

**Generators (`yield`)** (10 places). A function that produces values one at a
time instead of building a list. Used for walking document blocks and for
recursively finding radio groups inside nested panels: memory stays flat
regardless of document size, and a caller that stops early does not pay for the
rest.

**`functools.lru_cache`**. Memoisation — remembers results by argument so a
repeated call is free. Used on MaxDiff design generation, where building is
deterministic but takes half a second, and every respondent entering the survey
would otherwise rebuild the same design.

**Walrus operator (`:=`)** (11 places). Assign inside an expression:

```python
if (match := pattern.search(text)):
    use(match.group(1))
```

Avoids either calling `search` twice or declaring a variable one line early.
Used in comprehension filters where the matched value is both the test and the
result.

### Concurrency and isolation

**`ProcessPoolExecutor` with the `spawn` start method.** Heavy work runs in
separate *processes*, not threads, because Python's Global Interpreter Lock means
threads do not run CPU-bound work in parallel.

`spawn` rather than `fork` specifically: `fork` duplicates the process including
its threads and open database connections, and a forked child holding a copy of a
lock that was held at fork time deadlocks. `spawn` starts a clean interpreter.

**`threading.Thread(daemon=True)`** for background extraction. Here the work is
I/O-bound — waiting on a model API — so the GIL is not the constraint; the thread
is released while waiting. `daemon=True` means it does not keep the process alive
at shutdown. The thread uses its **own database session**, because the request's
session is closed by the time it runs.

### Regular expressions

Used far more than in typical application code, because scripter conventions
*are* patterns. The specific features that earn their place:

**`re.VERBOSE`** — allows whitespace and comments inside a pattern, so a complex
one can be read:

```python
_SUFFIX_RE = re.compile(r"""
    (?:                    # a sub-variable suffix is EITHER
        [RC_]\d+           #   separator then digits  (Q5r1, Q5_1)
      | (?<=\d)[A-Z]       #   OR one letter right after a digit (Q5a)
    )$
""", re.VERBOSE)
```

**Named groups** `(?P<stem>...)` — `match.group("stem")` instead of
`match.group(1)`, so inserting a group does not silently renumber the rest.

**Lookahead `(?=...)` and lookbehind `(?<!...)`** — assert context without
consuming it. `(?<!ZIP )\b(RE)?CODE\b` matches "CODE" except after "ZIP",
which keeps "ZIP CODE" out of the derived-variable detector.

**Non-greedy `+?`** — match as little as possible. In `^(?P<stem>.+?)[rc_](\d+)$`
it makes the stem stop at the *last* separator, so `Q2ar1` splits into `Q2a` and
`1`, not `Q` and `ar1`.

### Validation at the boundary

**Pydantic** models every request and response. Validation happens once, at the
HTTP edge, so code inside the system can assume its inputs are the shape they
claim. A malformed request is rejected with a readable error rather than causing
a `KeyError` somewhere deep.

**FastAPI dependency injection** (`Depends`). A route declares what it needs — a
database session, the current user, a required role — and the framework supplies
it. Authorisation is then a *type signature*:

```python
user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST))
```

A route that forgets to check permissions is visibly different from one that
does, which is much harder to get wrong than remembering an `if` at the top.

### Persistence

**SQLAlchemy ORM** for tables, with one deliberate exception: the
`SurveyDefinition` is stored as a JSON **string in a TEXT column**, not as
SQLAlchemy's JSON type. The dataclass already owns its own serialisation
contract, and TEXT behaves identically on SQLite (tests) and Postgres
(production), while the JSON type double-parses and differs subtly between them.

**Defensive `ALTER TABLE`** at startup instead of a migration tool. A client
bundle must survive an upgrade with nobody present who knows what a migration
is.

### Testing

**pytest fixtures** — reusable setup declared as a function and requested by
name as a parameter. A test that needs a database says so in its signature.

**`monkeypatch`** — temporarily replaces an attribute for one test and restores
it afterwards. Used to point the code at a temp directory or to stand in for a
model API. The restoration matters: one test in this session wrote real files
into the repo and every later test then saw them.

**`unittest.mock.patch`** as a context manager — same idea, scoped to a block,
used where a fake needs to be built inline.

**Testing at the boundary, not the implementation.** Routing tests assert
through `evaluate()` — does this condition actually fire for this respondent? —
rather than checking the shape of the expression tree. A screen-out that does not
fire is not a screen-out, however correct it looks.

---

## 14. Engineering techniques

| Technique | What and why |
|---|---|
| **Defensive column-add** | On startup, `ALTER TABLE ... ADD COLUMN` for any column the code expects and the database lacks. No migration tool, because a client bundle must survive an upgrade with no DBA. |
| **Content-hashed assets, no-cache shell** | The JS bundle's filename contains a hash of its contents, so it can be cached forever. `index.html` names that file and is served `no-cache` — without which a deploy silently does not arrive. |
| **Ed25519 licence** | `<payload>.<signature>`, verified with a public key shipped in the image. The private key never leaves DVV, so a copy of the image cannot mint its own licence. A lapse has a grace window, and respondent links keep working even after it — the client's respondents are not hostages to an invoice. |
| **Idempotent flags** | `add_flag` adds once and mirrors onto the question, so a repeated pass cannot produce duplicates. |
| **Qid deduplication** | A qid is an *address* — review, approval, routing and the data column all use it. Two questions sharing one makes the second unreachable. Later duplicates are suffixed and keep their original visibly. |
| **Checkpointing** | Extraction persists partial results as it goes, so a long run that dies has not lost everything. |

---

## 15. What the system deliberately does not do

This list is as important as the rest, and every item is a decision rather than
an omission.

- **Compute derived variables.** The rule is in the document in prose; compiling
  it half-right would put numbers in a banner variable nobody authored.
- **Apply quotas automatically.** A quota definition is not an expression, and
  guessing the mapping would field the wrong study.
- **Field MaxDiff or conjoint before the pieces exist.** Detected and refused,
  because the output would look like data and be unanalysable.
- **Invent an ID, a count or a reason.** Every copilot fact comes from a tool
  call, enforced.
- **Act without a click.** Every change is a card the user confirms.
- **Flag a required question with no "don't know".** Tried, measured, removed —
  it fired on 25, 21 and 18 questions across three documents, and the ones it
  named were exhaustive by construction. A check naming 40% of a questionnaire
  is one a reviewer learns to scroll past.

That last one is the pattern worth internalising: **a check that cries wolf
costs more than it finds.** Several ideas in this system were built, measured
against real documents, and deleted.

---

## 16. The measurement discipline

Three real client questionnaires and their three real data files are the
measurement set for every parser change. A unit test proves a rule; these prove
the rule survives contact with documents nobody wrote for us.

Rules that are followed:

1. **Baseline on `main` first.** Before claiming "no regression", run the same
   script on `main` and on the branch and compare the *lists*, not the counts.
2. **Exclude LLM-touched rows.** They flip run to run on identical code, because
   a backend cooldown makes the model reachable in one run and not the next.
   Reading one of those as a regression sends you hunting a change you did not
   make — which happened.
3. **Guard the premise.** A test that passes for the wrong reason is worse than
   no test. Several tests here assert first that the input really has the shape
   the test is about.

---

*Every claim in this document is from the code. Where a number appears — 64%,
451,400 tokens, 345 respondents, 1551 rows — it was measured on a real client
document or data file, not estimated.*

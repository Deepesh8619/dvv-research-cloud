# Reading a questionnaire, and understanding one

Parsing answers "what does this document say?". A questionnaire is only usable
when something also answers "does what it says make sense, and what is it
FOR?". The two are different jobs and they fail differently: a parse error
gives you an obviously broken question, while a comprehension failure gives
you a survey that runs perfectly and measures the wrong thing.

This is what the system understands today, why each piece is there, and what
it still does not do.

---

## 1. Is this a question at all?

A document is mostly not questions. Of 79 rows in the Google Ads instrument,
**19 are not asked of anybody**: intro screens, transitions, explanatory text,
and hidden variables the programmer computes.

| The document says | What it is | Read as |
|---|---|---|
| `Q type: Text Only` | a transition screen | `info` |
| `Q type: Display text`, `No response` | display | `info` |
| `Show / HV: Hidden variable` | computed, never asked | `info` + `DERIVED_VARIABLE` |
| qid begins `HID_` | computed | `info` + `DERIVED_VARIABLE` |
| `Q type: Multi-line (large) Text OE` | a verbatim | `open_text` |

Each of these was once read as a question with no answers. The cost is not
cosmetic: a transition screen typed as an open end shows the respondent a
paragraph **and a box to type into**, and the export carries a column for an
answer nobody was asked for.

The rule throughout: **a stated fact beats a heuristic.** `Show / HV: Hidden
variable` is the document telling us outright, and it is worth more than any
amount of wording analysis. The matching pattern is deliberately narrow
because the same cell's other value is `Show respondent directly` — and a
loose match there would hide a real question from every respondent, which is
far worse than the bug it fixes.

## 2. What is this question connected to?

A question is rarely self-contained. Four relations are read from what the
document states, never inferred from data:

- **Base** — `[ASK IF Q13=1-5]`, section paths. Who sees it.
- **Mask** — `[INSERT SELECTIONS FROM DA_GOALS]`, `ONLY DISPLAY BRANDS AWARE
  FROM Q2a`. Where its answers come from.
- **Pipe** — `[INSERT RESPONSE FROM Q8]`. What is substituted into its wording.
- **List source** — `[INSERT CURRENTLY USED BRANDS FROM DA_PLATFORM_USAGE
  9-15]`. Where the rest of its list comes from.

The distinction that keeps costing us is **mask versus pipe**. A mask supplies
a LIST; a pipe substitutes ONE earlier answer. Reading a pipe as a mask claims
this question's answers come from Q8 when they do not. `[INSERT RESPONSE FROM
Q8]` is excluded by name for exactly this reason.

These relations are materialised as a graph (`buckets.links`), so both
directions are answerable: what decides this question, and what depends on it.
That is what makes "can I edit Q4's list?" a question with an answer.

## 3. What is the study FOR?

The structure is usually written in a scripter note, in prose, where a human
would look for it:

> Please group the Google Ads questions and Meta Ads questions into two
> separate blocks. **Block order: Rotate the presentation of the two blocks
> evenly (50% see Google Ads first, 50% see Meta Ads first).** Internal order:
> Do NOT randomize the questions within the blocks.

Every clause changes what a respondent meets.

The **rotation** is the clause that decides whether the study is sound. Half
the sample must meet Google first and half Meta first, because whichever brand
is asked about first is rated differently. Field it in fixed order and every
respondent sees Google first — so the Google-versus-Meta difference the study
*exists to measure* carries an order effect nobody can separate out
afterwards. **The data looks complete and the finding is unsound.**

This platform has no block-order rotation. So the blocks are built, the
internal order is pinned, and the rotation is recorded and **refused at
preflight** rather than quietly dropped.

### Structure as a cross-check

Because the note names its questions, it also checks the parse. On the real
document the note says `GO_AI_GOOGLE`; the document's own `Q Name` is
`GA_AI_GOOGLE`. One character, and the error is in the *instruction*, not the
question — so the block is short by one and nothing says why.

The near miss is **suggested, never applied**. A qid decides a data column,
and quietly moving a question into a block because the name was nearly right
is how a study ends up with an arm the analyst cannot reproduce from the
document.

## 4. What must be refused?

Some instruments state designs this platform cannot field. Detecting them and
saying so is worth more than a best effort:

- **MaxDiff / conjoint** — experimental designs, not questions. Each
  respondent should see a different subset from a balanced design, and the
  result is a utility model rather than counts. Fielded as one static screen
  the export *looks like data and cannot be modelled* — which is worse than an
  empty question, because nobody finds out until the model refuses to run.
- **Block rotation**, as above.
- **A grid with rows and no columns** — reported as incomplete rather than
  silently complete.

The pattern is the same each time: **name the technique, refuse it, say why.**
Refusing costs a conversation. Fielding a broken one costs the sample.

---

## Where the reviewer meets all this

- **Preflight** runs every check the moment before a link exists, because
  review happens over days and a link pins the instrument the second it is
  created. FAIL refuses the link; WARN is worth a look.
- **The source pane** shows the client's document beside our reading of it,
  with colour, highlights and strikethrough kept — the presentation the parser
  drops on purpose but which carries information (a highlight after an option
  label is how these documents point at a hidden variable).
- **Every finding cites its evidence**, so approving is not a rubber stamp.

## What this does NOT do yet

Stated plainly, because a roadmap written as if it were finished is its own
kind of defect:

- **The link UI does not yet use the structure.** Blocks are built and
  compile to pages, but the respondent-facing survey does not present a block
  as a labelled section, and nothing decides per-study whether a section needs
  a banner, a progress marker or none.
- **No semantic check that a question makes sense in context** — that a pipe
  has something to fill it *at the point it fires*, or that a masked list can
  be non-empty for a real respondent. The dry run catches some of this by
  simulation; it is not the same as understanding.
- **Q21d-style chunk boundaries.** A question whose chunk swallows the
  placeholder and quota tables around it is still typed from what it swallowed.
- **Re-read cannot reach spec-table fixes.** `reread_question` replays only
  the prose parser, so a fix to the table path reaches an existing study only
  on re-upload — and worse, a re-read can currently overwrite a correctly
  labelled hidden variable with a prose reading of it.

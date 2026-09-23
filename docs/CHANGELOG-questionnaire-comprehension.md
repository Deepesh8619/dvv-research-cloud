# Questionnaire comprehension — build log

Ten PRs, 2026-09-22 → 2026-09-23, `49b098f` → `657de2e`.
Backend tests 1261 → 1320. Web tests 85 → 91.

All merged to `main` and deployed except the last three (#61, #62, #63), which
were merged after the last `./dvv upgrade`.

This log records *why* each thing exists and what was learned, because the
reasoning is the part that gets lost. Where something is unverified or
unfinished, it says so.

---

## The idea

Parsing answers "what does this document say?". A questionnaire is only usable
when something also answers **"does what it says make sense, and what is it
for?"**. The two fail differently: a parse error gives you an obviously broken
question; a comprehension failure gives you a survey that runs perfectly and
measures the wrong thing.

Every defect below was found the same way — by running the parser against three
real client questionnaires (`~/Desktop/DVV/Test data/`) with the LLM
deliberately unreachable, so only the deterministic path was measured.

---

## 1 · A question asked "for each" is a grid — #54

"How satisfied are you with each of the following pharmacies?" followed by a
satisfaction scale extracted as a single question whose *answers were the
scale*. The respondent picked one satisfaction level, once, with no pharmacy
attached — and the export had one column where the tab plan needed nine.

**Lesson: guard on shape, not on the type label.** Q4a arrived as the same
defect wearing a different type (`grid_single` with one option and its "Yes"
swallowed into the question text), and both existing repairs skipped it *on
type* — when the type was itself part of the corruption.

Measured: questions offering nothing to pick, Vivo 8 → 1.

## 2 · MaxDiff and conjoint are refused — #55

These are experimental designs, not questions. MaxDiff shows each respondent a
different subset from a balanced design; conjoint builds concepts from
attribute levels across repeated tasks. This platform has neither a design
generator nor a utility model, so the block extracted as one static screen and
the export **looked like data and could not be modelled** — worse than an empty
question, because nobody finds out until the analyst's model refuses to run.

**Lesson: where a rule may read matters as much as what it reads.** The first
version flagged an ordinary "what are the top barriers?" multi-select, because
its spec note said "motivators maxdiff exercise" — the author reminding himself
about a section four questions later. Only positions that speak *for* a
question now decide it: the declared `Q type:` cell, its own name, its heading,
its wording and labels, and bracketed directives. A notes field does not.

Measured: 250 questions across three documents, 6 flagged, all of them the real
MAXDIFF module.

## 3 · Read the client's document beside our reading — #56, #61

The parser keeps a questionnaire's *meaning* and drops its presentation — and
the presentation carries information. A highlight after an option label is how
these documents say "a hidden variable sits behind this one". On the Vivo
document the flattening discards **350 highlights, 1027 coloured runs and 87
struck-out lines**.

`src/intake/document_html.py` renders the *same walk* the loader makes, keeping
colour, highlight, strikethrough and tracked changes. No LibreOffice, no PDF
step, no new dependency.

**The anchor trap:** the loader *skips* a wholly struck-out paragraph, and skips
it **before** incrementing its block counter — while this view must still show
it. Such a paragraph renders unanchored and does not advance the count. Off by
one and every later anchor sends the reviewer to the wrong line, which is worse
than no viewer because it looks like the parser misread the document. Verified
aligned on all three documents: 633, 438 and 130 blocks.

**Untrusted input:** the document comes from a client and the pane sets inner
HTML, so markup is built server-side from escaped text and a colour is emitted
only if it is six hex digits. Two tests attack it directly.

#61 then made each question's own source tile draw *its slice of the document*
rather than the parser's flattened lines — a reviewer comparing a table in Word
against a list of lines re-associates every row by eye.

## 4 · One unclosed bracket stopped a question being asked — #57

```
Q21d.[ASK Q21e IF USED VIVO AT Q13 (Q13.7=1-5) And where do you feel
VIVOHealth Pharmacy falls short, if at all? [OPEN END]
```

The `[ASK` bracket is never closed, so `\[[^\]]*\]` ran to the `]` of
`[OPEN END]` and stripped the whole sentence as one scripter tag. The question
was stored as **display text** — in the review queue, never asked, in a
document a client had signed off.

Fix: a tag body cannot contain another `[`. The unparseable directive stays
visible in the wording, which is this module's own rule already.

## 5 · The spec table, read as written — #58, #59, #60

Four defects in one table, found by opening the new source pane on `DA_SHARE`.

- **`Multi-line (large) Text OE` → multi-select.** A phrase describing the size
  of the *text box* read as the *response type*. Six verbatims became choice
  questions with no options.
- **The variable list thrown away, and reported complete.** `row_items and
  options` was false because the response list was a directive rather than
  codes, so three stated rows were discarded — with `missing=[]`, so the
  question declared itself finished and nothing ever asked.
- **A directive inside the answer's own cell** (`Google Ads⏎[IF GOOGLE ADS
  CURRENT USER]`) — joined into the label, so the bracket reached the
  respondent and the base was lost.
- **A directive with no code beside it** was skipped before the directive check.

Plus: `list_from` was parsed and **consumed by nothing**. Recording it as a
*mask* — which is what it is — makes `fill_lists` borrow the items, the
compiler narrow them at run time, and the UI show the link, all from machinery
that already existed.

And `Show / HV: Hidden variable`, the table stating outright that a row is
computed. All three existing signals missed it. **The risk here runs the other
way**: that cell's other value is `Show respondent directly`, and a loose match
would hide a real question from every respondent — so the pattern is narrow and
that value is pinned by test.

Measured: Google Ads questions with nothing to pick **10 → 0**.

## 6 · Structure, not only questions — #62

`Q type: Text Only` is a transition screen. Matching the bare word `text` typed
it as an open end, so a respondent reading *"In this next section…"* was handed
a **text box**. 19 of 79 Google Ads rows now correctly read as not asked.

A scripter note is structure:

> Block order: Rotate the presentation of the two blocks evenly (50% see Google
> Ads first, 50% see Meta Ads first).

The blocks are built (7 and 7, all 79 questions placed, none lost or
duplicated). **The rotation is refused, not dropped** — whichever brand is
asked first is rated differently, so fixed order means the Google-versus-Meta
difference the study *exists to measure* carries an order effect nobody can
separate out afterwards. The data would look complete and the finding would be
unsound. Preflight fails the link over it.

**The note also checks the parse.** It names `GO_AI_GOOGLE`; the document's own
`Q Name` is `GA_AI_GOOGLE`. The typo is in the *instruction*. Reported as "did
the note mean…?" and **not applied** — a qid decides a data column.

## 7 · The parts the script names, in the link — #63

Blocks carried real labels and the respondent UI used only the *count*, for
progress segments. The compiler now ships `sections` and the respondent page
names the current one and banners it **once on arriving**.

This is the seam the compiler already used for `questionSymbols`,
`gridMaskSources` and `pipeVariables`: presentation derived from the
definition, shipped with the model, drawn by the page. Deterministic, no model
call.

**A test caught the hazard.** The existing fixture labels its blocks
"Screener" and "Main" — the first version showed those to respondents. A block
label is normally a *scripter's* header; only blocks the note named as arms of
the comparison are in respondent-facing words.

---

## What is NOT done

- **The link UI does not present a block as a section beyond the banner**, and
  nothing decides per-study whether a section needs a progress treatment.
- **Nothing rendered has been seen in a browser.** #61 and #63 are verified by
  tests and build only.
- **`Q21d`** — its chunk swallows the campaign placeholder table and a PN
  position-exposure quota table, so the table-shape pass types it as a grid.
  Chunk boundary.
- **Re-read cannot reach the spec-table path, and can damage a question.**
  `reread_question` replays only the prose parser, so #58–#60 reach an existing
  study **only on re-upload**. Worse, `_reread_is_regression` catches only a
  drop to *zero*: measured on the real document, a re-read turns
  `HIDDEN_TOPBENEFIT` back into a 9-option question and silently drops an
  option from `DA_TOP_GOAL`. **Do not run "Re-read all" on a spec-table study
  until this is fixed.**

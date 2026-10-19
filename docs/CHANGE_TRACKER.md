# Change tracker — questionnaire reading, link and checks

Every change to how a questionnaire is read and turned into a survey link, with
the evidence it was measured on, plus the decisions and issues still open.
Newest first. Measurements are offline (AI off) on the local corpus; client
documents and the independent readings ("gold") live outside the repo in
`~/dev/DVV/eval` and are never committed.

**How to measure a change**

```bash
# from a worktree of the branch, web/node_modules symlinked in
python scripts/eval_compare.py --batch ~/dev/DVV/eval/gold --out <dir>
```

It compares each question three ways: the questionnaire as an independent
reader states it, what the parser extracted, and what the compiled link does
(run in SurveyJS headless). Always run it on `main` too and diff the per-item
lists, not just the percentages: a removed flag can turn a known miss into a
"silent" one without anything getting worse (see 2026-10-15).

---

## 2026-10-19 — a named brand list coded the way the script codes it (branch fix/study-list-codes)

"FOR Q6-Q10 REFER TO CORE COMPETITOR SET OF 10 BRANDS BELOW" names an unnumbered
list once. Its questions borrowed the awareness list's codes (Q2a: CeraVe 5,
Neutrogena 14); the client's own script — and its export — coded the list by
its order, 1..10 (CeraVe 2, Neutrogena 3). A tracker's next wave fielded here
would have coded every brand differently from the last.

| Change | Evidence |
|---|---|
| Questions a named list covers are coded by its order | Bubble Q6_1..Q6_6 and Q8-Q10 now carry exactly the script's codes, per-category subsets included (lip products 1,3,7,9,10,98; sunscreens without Rhode 7) |
| Masks match by brand name where the two lists code a brand differently: each choice tests the source's code (CeraVe 2 shows when `{Q2a} contains 5`); the dry run does the same | compiler + dry-run tests |
| "98Another brand not listed (Specify)" — no space after 98 — kept code 98; it had collided with Medicube 12 and vanished from four categories | Bubble Q6_1..Q6_4 |

The Q6 question itself stays one multi-select per category: the export shows
the script fielded it as a loop (`Q6_Lr<category>r<brand>`), not as a grid.

Measured (AI off): Bubble parser 47.8% → 51.1%, link 44.6% → 47.8% (Q8, Q9,
Q10 now match — the known gap below is closed); nothing else changed.

## 2026-10-18 — every variable and what fills it (branch feat/variables-view)

Owners and analysts get a **Variables** tab: every column the data file will
have — hidden variables, the link's assigned arms and paradata included — and
what fills each one: *asked*, *computed* (with the rule or base in words: "1
when D2 = 1 … 4 when D2 ≥ 4"), *assigned by the link*, *recorded*, or **not
filled** (a hidden variable with no approved rule — a banner arriving empty),
with every code to check punched data against. Built by the same code that
writes the file, so it cannot drift. Client reviewers do not see it (the
endpoint now refuses the reviewer role).

## 2026-10-18 — coded hidden variables computed for every respondent (branch feat/derived-rules)

A coded hidden variable (Bubble D2a "CODE SIZE OF HOUSEHOLD" 1/2/3/4 or more,
Google Ads HID_COMP_SIZE) is a banner every table is cut by, and its column
was delivered **empty**: the document's wording was kept but never run, since a
rule compiled wrong puts numbers nobody authored into a banner.

Now the second look **proposes** a rule and a person approves it once:

| Read from | Example |
|---|---|
| the Definition column | HID_COMP_SIZE: Micro `SS_EMP = 2`, Small `SS_EMP = 3–4` |
| grid-row definitions | HID_AWARE: `DA_USAGE 1, 2, 3, or 4 = 3-5` — codes over different rows can all hold, so it is multi-coded |
| a condition in the answer label | Vivo S11 "All other (S11=3)" |
| number bands over the question it is coded from | D2a over D2 (`D2 ≥ 4`); Vivo S3a age bands over S3 |
| a model writing each code's condition in the document's grammar (Bedrock) | the rest; parsed, and every name must exist |

A value set from outside the answers (ZIP lookup, sample source, split,
assignment) is said to be so. Once approved (`Option.derive_if`), the value is
computed in the link (SurveyJS `calculatedValues`, for routing), on the server
(screen-outs, quotas, dry run — never trusted from the client), and in the data
file (one column; one 0/1 column per code when multi-coded); preflight stops
calling it uncomputed. A rule waits until every question it tests is answered.

Measured on the six documents (AI off): 28 coded hidden variables — 11 rules
ready to approve, 8 explained as set from outside, 9 need the model or a person.
No change to the gold scores.

Also fixed: saving any hidden variable (or a number question with opt-out codes)
in the editor **erased its codes** — the form sent an empty answer list for
types it shows no list for.

## 2026-10-18 — tick-box grids: several answers per row (branch feat/grid-multi)

Vivo Q21 ("What concerns, if any, do you have about the following types of
pharmacies? Select all that apply") was read as one answer per row: a
respondent could tick only one pharmacy type per concern and the rest were
never collected. A new type, `grid_multi`, runs end to end:

| Where | What |
|---|---|
| Reading | a grid whose own wording says "select all that apply" (and not "one per row") |
| Link | a matrix flagged `multiAnswer`; the respondent page ticks several per row |
| Conditions | `Q21.3 = 2` on a row means 2 was ticked |
| Data | one 0/1 column per cell, `Q21r3c2` (null where the row was not shown) — the form the data check already links |
| Field report, uploaded exports, dry run, live answers, editor | count and show it per cell |
| Model | `grid_multi` is now a real type, not a stand-in |

Measured (AI off, 6 documents): Vivo Q21 no longer a type difference (its row
codes differed before and still do); everything else unchanged. Bubble Q6 stays
split per product category, each with its own brand list.

## 2026-10-18 — bipolar grids, and kinds of question we cannot run yet kept on record (branch feat/bipolar-grid)

| Change | Evidence |
|---|---|
| A tripped grid whose document states its rows as a table (`Q41_1 \| left \| right \| theme [DO NOT SHOW]`) is read from that table. Two statement cells and pair wording make it **bipolar**: rows "left \| right", hidden theme column left out | Bubble Q41 (6 pairs), Q44 (5 pairs) now match the independent reading; Bubble parser 45.7% → 47.8%, link 42.4% → 44.6% |
| "Half see left and right flipped": each respondent is dealt an arm least-fill; the flipped arm sees the statements swapped **and the scale reversed**, so a code keeps its meaning and the data needs no recoding. The arm is delivered as `HID_BIPOLAR_FLIP` | integration test: 4 respondents alternate, data carries 1,1,2,2 |
| Respondent page: the two statements face each other above a one-line scale (the left end stays under the left statement; a reversed scale stays a number pad) | checked at phone width |
| The model may now say a question is a `bipolar_grid`, `grid_multi`, `rank_grid`, `slider` or `other` instead of being forced into our list. Kinds the link runs are applied; the others are set up as the nearest kind with the real one in `validation.intended_type` and a *Kind of question not supported yet* flag | agent tests |

No regressions on the six documents (Google Ads B link 69.0% → 70.4%).

## 2026-10-18 — tripwires: a confident misreading gets a second look (branch feat/tripwires)

The model only saw a question the rules could not read or one that carried a
flag. Bubble Q41 was read as a grid whose rows were "0=Agree fully with" and
"Theme" — the six statement pairs gone — with no flag, so nothing ever looked
again. Three checks now compare each reading with its own source text:

| Tripwire | Catches |
|---|---|
| Rows that are a scale label or a column heading | Bubble Q41, Q44; Vivo Q13 (its wrapped scale lines read as three rows) |
| The document lists `Q41_1 …` rows and the reading has a different number | Bubble Q41 (6 vs 2), Q44 (5 vs 4); a table read as a list |
| The document's scale runs further than the reading's columns | — (none in the six documents) |

A tripped question enters the second look. Scale lines read as rows are removed
by rule when real rows remain (Vivo Q13 now matches the independent reading's
rows). Otherwise it goes to the model with the document's table in view, and
the model's fix is kept only when the tripwire clears. Measured (AI off, 6
documents): Bubble silently-wrong 39.1% → 37.0% (Q41, Q44 now flagged); no
other document changed; no false alarms. Q41/Q44 are resolved properly by the
bipolar grid type (next).

## 2026-10-18 — pipeline quality control judges the completes (branch fix/qc-on-completes)

The Overview's *Quality control* box ran on every row of a fielded export. On
the real Google Ads file it reported 62 speeders (the screen-outs — a terminate
is short because the screener ended it) and 618 "empty" columns (routing and
hidden script variables). When the export has a status column, QC now runs on
the completes only and drops the emptiness lines: 62 completes checked, 0
speeders, 10 straight-liners, 40 gibberish open ends. Files without a status
column are checked as before. Existing projects pick it up on Re-process.

## 2026-10-18 — an uploaded export read as fieldwork (branch feat/data-field-report)

An uploaded export was shown as a spreadsheet (a column-by-column "data
profile"). It is fieldwork, so it now gets the field report a link gets — read
from the file: completes against the sample target, incidence, median
interview against the length the document promised, field dates, how every
interview ended, **which rule screened people out** and **which quota cells the
script counted** (both from Decipher's `markers`, the script's own record),
where partials stopped, devices, soft-launch quality on the completes, and each
question's answers. The Overview shows these cards in place of the data
profile; the field monitor shows them under *Fielded data*.

Real Google Ads export: 62 completes of 750, incidence 30.7%, median 17m 19s
against 12 promised; screened out at SS_RESP 90, SS_ADVERT_DECMAKER_QUAL 25,
SS_REVENUE 18; quotas by sample source, company size and retail.

Also fixed: the copied-open-end check read system text (user agent, the
script's variable lists) as answers and flagged every complete; it now reads
only question and "other, specify" text, and ignores a text most respondents
share.

## 2026-10-18 — fielded data on the field monitor (branch feat/fielded-data-in-monitor)

The field monitor only knew links run here, so a study fielded on another
platform looked unfielded even with its export uploaded. It now has a *Fielded
data* panel: the file, respondents, variables, and how each interview ended
(read from the export's status column), with Upload / Replace and a button to
check the data against the questionnaire. Real Google Ads export: 254
respondents — 62 qualified, 140 terminated, 45 over quota, 7 partial.

## 2026-10-17 — fielded data into an existing project (branch feat/attach-data)

| Change | Evidence |
|---|---|
| The *Questionnaire checks* tab uploads (or replaces) the fielded data file of an existing project, runs the usual processing, and says when the check is ready. Before, a project started from its questionnaire had nowhere to put the client's export. | Real Google Ads export attached to a questionnaire project: 254 respondents, 115 questions linked, 8 findings |
| The data check read respondent rows from the upload folder; processing writes them to the run folder. Every project whose data came from an upload skipped all row-level checks with "the server restarted". | Integration test fails before, passes after |
| The project API never returned the data file's name, so the Data file row and its Download button were empty. | Integration test |

## 2026-10-17 — second look: every flagged question re-checked, solved or explained (branch feat/second-look)

A reviewer met DA_VID_EFFIC as three flags ("Empty options", "Verification
discrepancy", "QA found a problem") for one cause, none saying how to fix it.
After everything is read, each flagged question is now re-checked with the
whole questionnaire in view and ends in one plain resolution: **fixed
automatically** (Undo), **fix ready** (Apply), **note**, or **needs you** with the
recommended next step. Flags stay, folded under "Technical details".

| Solved by rule | Example |
|---|---|
| Stale flags cleared (a grid "with no rows" that a later pass filled) | Bubble Q2c, Q8–Q10; Vivo Q5–Q7 |
| Rows inserted from another question, scale from a spec-table response list, "Among who" base (`and/or` now read) | Google Ads DA_VID_EFFIC (fixed; a ready fix where the document names itself) |
| Base written as a group a hidden variable defines | GA_AI_GOOGLE → HID_AWARE = 1; loop passes → current/lapsed users |
| Name used twice (renamed) → note | SS_ADVERT_DECMAKER_2, S6_2 |
| "Indicate all" on a one-answer question → ready fix | SS_CHANNELS |
| Typed number read as a list → ready fix | SS_EMP (0–9,999,999) |
| Screen-out answer in a select-all list → ready fix: exclusive | Vivo S6, Q3 |
| Two answers with one code → ready fix: next free code | C_BUS_TOOLS |
| Programmer note read as an answer → removed and obeyed | Vivo Q21 "[PN: RANDOMIZE]" |
| Missing name → likely real one in the advice | DA_USAGE → DA_PLATFORM_USAGE, C_ZIP → C_GEO_ZIP |

What rules cannot settle goes to a model (task `questionnaire.resolve`) with the
questions it names, its neighbours, a cleanly read question of the same kind, and
the study overview. Its answer may only use the document's wording and is kept
only when QA finds less wrong. A person's save re-checks that question.

Also fixed: a fix or agent suggestion applied in the editor lost a grid's rows,
scale and masks on save (the form never sent them).

Measured (AI off, 6 documents): flagged 106 → 80, every remaining one with a
next step. Against the independent readings, no question got worse; four Google
Ads bases and DA_VID_EFFIC improved. **Known gap (closed 2026-10-19):** Bubble Q8–Q10 rows carried
Q2a's codes where the script numbers its core brands 1–10.

## 2026-10-16 — fielded data checked against the questionnaire (branch feat/data-audit)

The questionnaire plus a fielded Excel/SPSS export: who answered when the base
says they should not, screen-outs among completes, exclusive answers ticked
with others, answer caps, totals, masks, codes outside the list.

| Change | Evidence |
|---|---|
| Paradata (timers, device, geo, `dSample`, `vUserAgent`, `hCF*`) is not a question | Google Ads: 216 → 50 columns rebuilt as questions |
| Screen-out check reads `status` (terminated / overquota / complete) | false violations among terminates gone |
| Questionnaire codes translated to the script's by answer label (reversed or renumbered scales) | TRD1 Q10 no longer "53% outside base" |
| Grid-row conditions tested per row; respondents outside a base reported even when most agree | Google Ads TRUST/SENTIMENT evaluable; TRD2 Q5 (1 respondent) found |
| Exclusive answers, "select up to N", stated totals checked per record | Bubble Q2a: 4 respondents ticked "None" with brands |
| Split questions use their own columns before the source grid; loop passes link to `<qid>_Lr<n>r<code>`; grids link cell by cell (`Q14r1c2`) | 36 false code findings removed (Vivo Q14/Q21, TRD1 Q20) |
| A question no longer takes the next lettered question's column (`Q14` → `Q14a`) | linker test |
| Masks compare renumbered lists by label; masked grids are checked by row | Bubble Q6: 933 false mask violations avoided |
| `[PN: ASK Q19 OF ALL]` exempts Q19 from `ASK Q12-Q19 IF …` | Bubble Q19: 1,030 false "outside base" removed |
| A base naming a question that exists nowhere is reported "not checked" | Google Ads GA_META_ADV_SPEC (document says `DA_META_ADV`) |

Findings per study (start of the work → now): Bubble 72 → 19, Vivo 43 → 6,
Google Ads 22 → 13, TRD1 12 → 1, TRD2 0 → 0, plus base checks. Against an
independent audit (gold logic applied to raw columns, completes only) 6 of 9
findings match with the same respondents; the other 3 are a reference error
(two Google Ads questions with one name), a document variable that does not
exist, and an ambiguous mask (Vivo Q18a shows the "Other" row selected at Q18).

What remains is mostly real document-vs-script differences (`option_not_scripted`,
`punch_not_in_questionnaire`). Known gaps: bipolar slider grids stored three
times (`Q41Q41_1`, `Q41_LQ41_1`, `Q41_RQ41_1`) are rebuilt as questions; per-
category brand subsets in a carousel (Bubble Q6_5/Q6_6) are not read.

## 2026-10-15 — answer conditions, bases written as notes (branch feat/answer-conditions)

| Change | Evidence |
|---|---|
| Editor: every answer row has "only show this answer if…" (badge when the document stated one) | `OptionCondition` tests; API already kept `Option.show_if` |
| Conditions read underscore names (`HID_GOOGLE_USER = 2 or 3`) and grid rows (`Q38.11=6-10`) | `test_routing_prose` |
| `ASK Q12-Q19 IF EVER PURCHASED BUBBLE AT Q2b` — the one answer named in words; `ASK … OF SPLIT A (S0a=1)`; `SKIP C8 IF …` | Bubble: Q12–Q19, Q29a/b, C8 gated |
| `ASK IF MULTIPLE AT S6 SELECTED` (any two answers); `= 3-5 for at least one option` (any grid row); `only show options …` filters each row | Vivo S7; Google Ads TRUST/SENTIMENT |
| A note naming `Q29a` found its question only if upper-cased | Bubble Q29a/b |
| Link: a list filtered from an earlier answer is skipped when nothing carries over | compiler test |
| An answer's own base (`Google Ads [IF GOOGLE ADS CURRENT USER]`) was read and dropped; now resolved from the hidden variable naming that group, else flagged | Google Ads DA_SHARE, C_ACCOUNT_PLATFORM |
| Grid-row `[ANCHOR]` / `[EXCLUSIVE]` tags kept; `anchor 3` in spec-table randomise details | Bubble Q2a/b/c, Google Ads C_OFFER |

Independent comparison, main `1569729` → this branch: parser matches 138 → 142,
link 193 → 195, defects caught 60 → 60 (of 372 questions / 169 defects).

## 2026-10-15 — merged as PR #109 (`1569729`)

Spec-table bases behind `Other >>>`; Word auto-numbering for question names and
answer codes; unnumbered-document segmentation (`[BASE` without colon, ALL-CAPS
headings, closing "Thank you", next list number); section-heading bases
`(S0=2)`; brand-loop bases from hidden variables; per-sentence numeric
screen-outs; ranking, number ranges, per-row % tables, inline `[RANDOMIZE]`,
`Randomize variable list`; grid rows shuffled in the link; consistency checks
and a reason on every flag; decoding report + review panel; teach loop closes
on an unseen-style questionnaire; `scripts/eval_compare.py`.

Measured main `b312f0d` → `1569729`: parser matches 101 → 141, link 145 → 194,
wrong-and-unflagged 176 → 139, defects caught 47 → 60.

---

## Decisions waiting for the product owner

| Decision | Options | Why it matters |
|---|---|---|
| Are questions **required** by default? | required unless the document says optional / optional unless stated | 49 differences against the independent readings, which assume required |
| Keep **opt-outs last** ("None", "Other", "Don't know") when the document is silent? | keep (today) / only when stated | ~45 "anchored" differences: the document is silent, the parser anchors by default |
| Number boxes with a per-box condition (`DA_SHARE` Google box only for Google users) | change the box type (data shape changes) / keep flagged | SurveyJS number boxes cannot hide one box |

## Pending issues (known, not yet fixed)

| Issue | Where seen | Needs |
|---|---|---|
| Per-category display rules in a carousel table (`[PN: DISPLAY CATEGORY IF] Q3.1=1-4 …`); the document's 4th cell reads `OR 6 OR 7=1-4` (data shows Q3 rows 4–7) | Bubble Q6 | code (carousel reader) |
| Bipolar slider grid stored three times (`Q41Q41_n`, `Q41_LQ41_n`, `Q41_RQ41_n`) | Bubble Q41/Q44 (data audit) | code (linker) |
| Order rules: `KEEP ORDER, RANDOMIZE IF 1 SHOWS FIRST`, `FIRST AND SECOND POSITION ONLY` | Vivo | code + a model for conditional order |
| Grid written as two labelled sub-tables (`24 A. Spravato` / `24 B. TMS`) | TRD1 Q23 | code |
| Sliding-scale grid whose rows come from another question; rows/columns written the wrong way round | Google Ads DA_VID_EFFIC, DA_GOAL_PLATFORM | AI (residue) |
| MaxDiff design references (`MAXDIFF_COMPELLING`, missing item 13) | Google Ads | MaxDiff support |
| A closing answer that is not an opt-out by wording ("All of my patients … received it") not anchored under a keep-last rule | TRD2 Q11 | judgment / model |
| "Other, specify" answers without a text box (58 differences) | all | being handled in the eval session |
| Document mistakes the rules cannot judge (a filter on "Not aware", a copied formula naming the wrong brand, reversed scale order) | Google Ads, TRD2 | logic reviewer (AI) |

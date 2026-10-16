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

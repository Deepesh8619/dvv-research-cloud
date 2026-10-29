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

## 2026-10-29 — information screens read like an analyst reads them (branch feat/reader-information-screens)

Mid-document information ("Moving on…", "Thinking now about one brand, Bubble…",
"Please now hand your device over to your teen…", the welcome) rode at the end of the
PREVIOUS question's text: it never became a screen, and it muddied that question's
reading. A paragraph that talks to the respondent, asks nothing, and comes straight
before a question is now its own information screen. A section's condition passes
through such a screen (and applies to it) instead of stopping at its INFO id.

Measured (7 gold documents, AI off, item lists diffed): 12 newly right, 0 newly wrong.
Missing gold questions: bubble 12 -> 5, vivo 7 -> 2.

## 2026-10-29 — questionnaire reading accuracy: contents pages, required, honest scoring (branch fix/skip-table-of-contents)

**What changed**
- A Word table of contents is no longer read as questions (TOC styles, `_Toc` links,
  PAGEREF/TOC fields, the "Contents" title). On a live GBK file, "KEY METRICS ...... 22"
  had become question SCREENER8 "KEY METRICS22".
- `settle_required`: after every pass, an asked choice/number question is required unless
  its own text says optional. A failed AI reading built a stand-in with required=False and
  the repair kept it — so when the model was throttled, those questions were skippable.
- `eval_compare` scored two things a respondent cannot meet: on grids the parser's one
  shuffle flag means ROWS (now compared with the gold's rows), and "anchored" is compared
  only when the options are shuffled.

**Measured** (7 gold documents, 369 questions, AI off, item lists diffed)

| | exact matches |
|---|---|
| old code, old scoring | 170 |
| old code, corrected scoring | 202 |
| this branch | 216 (+14 newly right, 0 newly wrong) |

Per document (exact %): bubble 51→59, google_ads_a 46→64, google_ads_b 48→63,
trd1 70→70, trd2 43→68, vivo 30→40.

**Open decision:** open ends are optional by default (28 of the remaining "required"
differences); the gold readings follow the document, which rarely says so.

## 2026-10-25 — MaxDiff utilities and shares of preference (branch feat/maxdiff-utilities)

Advanced methods → MaxDiff gave only counting scores. When the file records the
items each screen showed (Decipher `MD_Items_<n>`), it now also fits an
aggregate best-worst logit (Newton steps on the concave likelihood, small ridge,
numpy only): each item's utility, centred on the average item, and its share of
preference, adding to 100%. The list is ordered by utility. On 300 simulated
respondents it puts five items back in their true order with the true spread
(test); 1,000 respondents × 12 screens × 20 items fit in 0.13 s. One model for
the whole sample: Hierarchical-Bayes per-respondent utilities are still not
attempted.

---

## 2026-10-25 — founders can reword every (i) tip (branch feat/editable-help)

The 39 info tips moved from `help.ts` into `web/src/help/help.json`, which the
API reads too: each tip's title, what, why and how is now a key in Admin →
Wording (`help.<id>.<part>`, grouped as "Info tip — <title>"), with the shipped
text as its default. InfoTip reads overrides through `useLabel`, so the shipped
wording renders until a founder changes it, and if overrides fail to load.
Tests: registry defaults match the JSON for every tip; a tip shows a founder's
override.

---

## 2026-10-25 — the server drops answers a mask hid (branch feat/mask-enforcement)

A masked list ("Which have you used?" showing only brands heard of) was narrowed
in the browser only; a page sent by any other client could store a brand the
respondent was never shown. The answers route now applies the same rule the
compiler writes into `choicesVisibleIf` — one shared helper, `_mask_terms`, so
the two cannot drift — and drops the hidden codes (logged). Dropped rather than
refused, so a disagreement between this check and SurveyJS can never leave a
real respondent stuck. Write-ins, "None" and options the source never offered
stay allowed, as in the browser. Not checked server-side yet: grid rows and
grid sources. Test: `test_an_option_the_mask_hid_is_dropped_on_the_server`
(fails with the check removed).
## 2026-10-25 — a model drafts the blank translations (branch feat/translation-drafts)

Translations → "Draft blanks with a model" fills every string still blank in
that language, in the background. Only a private model is used: Bedrock, or a
local model when no keys are set; with only free-tier keys it refuses
(`router.run_private`) — client text never goes to Groq/HF/Cerebras. A person's
translation is never overwritten, and a draft that loses a piping token is
refused and left blank. Drafted rows carry "model draft — check" in the sheet's
new `check` column and stay counted as drafts until the translator changes the
text or clears the mark. Tests: `test_translation` (draft, marks, private
route), `test_fielding_routes` (route end to end, failure message), panel test.
Not yet run against Bedrock itself.

---

## 2026-10-25 — one broken link no longer takes down the supplier report (branch fix/supplier-report-resilience)

The supplier scorecard built every link's report in one go, so a single link
whose questionnaire could not be read (or whose data could not be built) made
the whole Sample suppliers panel fail with a server error. Now each link is
on its own: an unreadable link gets a row saying so ("its interviews are
safe"), and a link whose quality checks fail still shows completes, accepted
and billable, marked "quality not checked". Test: a link with a corrupt
questionnaire beside a healthy one — both rows come back.

## 2026-10-25 — quota bands on number questions (branch feat/numeric-quota-bands)

The quota plan took single-choice answers only, so the most common quota of
all — age bands — could not be written. A cell can now be a band of a number
question (`min`/`max`, inclusive, either end open: "18–34", "55+"), stored as
"not below 18 and not above 34" in the same condition language every other
quota uses. Refused with the reason when it has no ends or starts above where
it ends. The builder's Quotas card shows "from / to" for a number question.
Checked: unit tests (17/18/34/35 against 18–34), the journey test opens a link
with Women and 18–34 cells, web test.

## 2026-10-25 — an (i) wherever a feature needs explaining (branch feat/info-tips)

User asked for an info icon wherever someone might not know what a feature
is: what it is, what it does, why, and how. `components/ui/InfoTip.tsx` is a
small (i) that opens on hover, keyboard focus or tap (phones), closes on
Escape, a click elsewhere or scroll, and is drawn above the page so scrolling
lists and clipped cards cannot cut it off. Every text is in `help/help.ts`
(What it is / Why / How), 39 entries, tested to have all three.

Placed on: the Data bar; Questionnaire checks (consent and notice, quote and
each of its inputs, translations, follow-up probes, data vs questionnaire);
Reports (key findings, waves and countries, advanced methods and each method);
Cleaning (the review, what each check means, lock; adverse events); Open ends;
Field monitor (fielded data, dry run, soft launch, vendor redirects, test link,
quotas, sample suppliers, accepted, reconciliation, incidence, build data file);
the builder (templates, blocks, expert notes, quotas, both condition editors).

Also: "Materialize" is now "Build data file"; Key drivers offers rating scales
only (numbered points or scale wording) — gender, age bands and income were
being offered as attributes; and field labels in the quote stay real labels
(the (i) sits beside them, not inside, so clicking a label still focuses its
input).

## 2026-10-24 — quotas written with the questionnaire (branch feat/quota-plan)

Quotas were set per link, by hand, after the link existed. Now the builder
has a Quotas card: completes needed, and rows like "Women · S_GENDER · Woman ·
50%" (answers picked by label, the limit shown as you type). Saved with the
questionnaire (`metadata.quota_plan`, `GET|PUT /questionnaire/quotas`); every
real link opened afterwards gets one cell per row, limited to share ×
completes rounded up (test links get none). Refused with the reason: no
completes, a question that is not single-choice, answers it does not have, or
shares for one question over 100%. Single-choice questions only for now
(numeric bands such as age come next if needed).

Checked: unit tests; the journey test now writes a plan and finds the cell on
the link it opens; in the browser, Women 50% of 400 saved as a limit of 200.

## 2026-10-24 — awareness block asks only about brands the person knows (branch feat/awareness-masks)

Consideration, purchase and preference in the Awareness block now list only
the brands ticked at aided awareness (plus "None of these"), using the
existing option masks; the mask follows a renumbered source (`AWR_AIDED_2`).
Checked in the compiler output and in SurveyJS itself (aware of 1 and 3 →
offered 1, 3 and None).

## 2026-10-24 — the survey link loads only the survey (branch perf/split-bundle)

The whole app was one 2.5 MB script: a respondent opening a survey link on a
phone downloaded the analyst app with it. Pages now load when opened
(`React.lazy` per route, the app frame included), and the respondent page
imports the one SurveyJS theme it uses instead of all of them.

Measured on the production build (compressed): a respondent downloads
**469 kB, down from 588 kB**; what remains is mostly SurveyJS itself
(survey-core is 1.3 MB minified). The login page is ~90 kB instead of 588 kB.
Checked by serving the production build: the survey link fetches only the
entry, the respondent page and the survey runtime. Also: the welcome screen
counted the information screen as a question ("7 questions" against a
progress bar of 6); it now counts what the progress bar counts.

## 2026-10-24 — CI, an end-to-end journey test, and multi-selects in the tables (branch chore/ci)

Nothing ran the tests except a person, and nothing exercised the features
together. Now:

- **CI** (`.github/workflows/ci.yml`) on every PR and push to main: the
  backend suite with every AI key empty, ruff, vitest and the production web
  build. Green on a clean runner.
- **The journey test** (`tests/integration/test_golden_path.py`): a brand
  tracker from the template → expert checks → approve → link with a supplier
  → 40 respondents through the respondent API (5 under 18 and 1 refusing
  consent are screened out; 34 complete) → data → tables, findings, cleaning,
  Open ends, methods (TURF), the PowerPoint, the reconciliation file and the
  billable amount.

Its first run found a real defect: in DVV's own fielded data the multi-select
option columns are typed MULTI, and the automatic table plan took SINGLE only
— so a fielded brand tracker had **no tables for aided awareness,
consideration or purchase**, the core of the study (Decipher exports were
unaffected: they type the same columns SINGLE 0/1). The plan now includes
them, and findings name an option by its own label ("Nescafé"), not the
question-and-option title.

## 2026-10-24 — live expert checks while building (branch feat/expert-checks)

The builder now reads the questionnaire the way a senior researcher would, as
it is built (`questionnaire/expert_checks.py`, `GET /questionnaire/expert-checks`):
leading wording, two questions in one ("the price and quality"), a
multi-select with no "None of these"/"Don't know", a scale that leans one way,
9+ unordered answers in a fixed order, grids over 12 rows, more than 4
required open questions, and the interview length past 20 minutes. Two weights
— fix and consider — and never blocking. A chip in the builder header ("≈ 3–6
min · 1 note") opens the notes; each note jumps to its question; it refreshes
as questions change.

Calibrated: all five templates come out with no notes; the real Google Ads
questionnaire gets 10 — about 22 minutes, a 15-row grid, a 12-answer list in
a fixed order, and multi-selects without "None" (weighed "consider": some,
like customer ages, always have an answer). MaxDiff design tables are not
called long grids.

## 2026-10-24 — conditions in plain words (branch feat/plain-language-conditions)

The routing editor asked for a question id, an operator ("equals", "has
selected") and codes typed by hand ("1, 2"). Given the questions (builder and
questionnaire review), each condition now reads as a sentence: the question
picked by its wording, a verb that fits its type (a single choice "is / is not
/ is any of", a multi-select "includes", a number "is / is more than / is less
than"), and the answer picked from its labels — "is any of" as chips. The
condition is read back above ("What is your age? is less than 18"); "all of
these / any of these" replaces "match ALL (and)". Labels: "Ask this question
only if", "End the interview (screen out) if". The stored routing is unchanged,
and any screen not passing the questions keeps the previous editor.

## 2026-10-24 — build from scratch: study templates and ready-made blocks (branch feat/builder-library)

User asked for a card-based way to build a good questionnaire with ease. The
builder started blank with only a title box. Now (`questionnaire/library.py`):

- **Templates** — Brand tracker, Concept test, Usage and attitudes, Customer
  satisfaction, Doctor (HCP) study — chosen as cards when creating the
  survey; each asks only for what it cannot know (category, brands,
  attributes, the concept…) and arrives as standard sections.
- **Blocks** — Consent and notice, Screener, Doctor screener, Awareness
  funnel, NPS, Satisfaction, Concept test, MaxDiff, Van Westendorp pricing,
  Open question, Demographics — added from "Add a block" after the selected
  question.
- Written the standard way: balanced 5-point scales labelled at both ends
  ("Neither satisfied nor dissatisfied"), "None of these" exclusive and
  anchored, brand lists randomised, "Prefer not to say" exclusive; routing in
  the block (screener terminates under the minimum age, consideration asked
  only of people aware of a brand, NPS "why" probed when thin); MaxDiff design
  sized so each item is shown about three times; question ids renumbered
  (`NPS_2`) when taken, with the block's routing following the new ids.
- `GET /questionnaire/library`, `POST /questionnaire/modules`,
  `POST /questionnaire/template` (empty questionnaire only, 409 otherwise).

Every template and block validates, compiles to a link, and passes the MaxDiff
design check (17 tests). In the browser: a new project from the Concept test
template arrived as 16 questions; an NPS block added after S_GENDER landed
there with its brand in the wording.

## 2026-10-24 — one data upload per project (branch feat/one-data-upload)

User: "no need for 3 sections while uploading". The same data file could be
uploaded in three places — New Project, Questionnaire checks (data vs
questionnaire) and Field monitor (fielded data) — although the last two
already wrote to the same slot. Now a Data bar under the project header, on
every tab, is the one place: file name, respondents, variables, Download,
Upload/Replace. When it changes, the project reloads (`dvv:data-changed`).
The data check shows the file it reads ("None yet — upload it in the Data
bar"), and the Field monitor keeps its fieldwork cards with a link to the
project page. New Project's "Start with data" stays: that creates a project
from data rather than adding data to one.

## 2026-10-24 — Open ends in each project; Wording and Activity under Admin (branch feat/open-ends-and-nav)

User asked what Coding, Activity and Wording do and whether they belong. Found:
Coding listed script columns (uuid, date, markers, user agent) as "open
questions", each "Not coded"; and coding had never worked on an uploaded
Decipher export at all — the coder needed a `respondent_id` column the export
does not have, so every question failed before a model was asked. Studies
fielded through DVV links were never coded.

- `variable_roles.open_end_columns`: a column respondents wrote in — role and
  label say so AND most answers have two or more words (so not ZIP codes,
  timestamps, hidden stores; MAXDIFF_OE kept). Google Ads: 6 real open
  questions instead of 70+ text columns; TRD1 2, TRD2 3.
- `coding.runner.code_open_ends`: adds a respondent id when missing; used by
  the upload pipeline and by on-demand coding. Failures read in words ("no
  model was reachable; try again in a few minutes"), details in the log.
- `POST /coding/run` (background; 409 while running) and `GET /coding/status`.
- A project's **Open ends** tab replaces the Coding page: each open question
  with its answer count and coded state, "Code N open questions", progress,
  and the review queue for answers the model was unsure of.
- Sidebar: Dashboard, Projects, New Project, Admin. Admin has tabs Team
  (owner), Wording and Activity (owner, founder); the old addresses redirect.

## 2026-10-23 — cleaning review as triage, with the answers behind each flag (branch feat/cleaning-triage)

User feedback: the cleaning screen was hard to use — the reason defaulted to
"other" (which forces a note), flags repeated line by line ("gibberish: Q5",
"gibberish: Q7a" …), three badges per respondent, and the reviewer never saw
what the respondent actually wrote. Now:

- A summary strip (to review / removed / kept, progress, Lock beside it).
- Filters by band and by check, with counts; reviewed respondents hidden
  unless asked.
- A ranked list on the left, flags grouped ("Gibberish ×2, Straight-lined").
- An evidence panel: each flag with its question and the respondent's answer
  in words (new `POST /cleaning/respondents/{key}/evidence`, gated like a
  download), the straight-lined grid ("all 6 rows answered “Extremely
  trustworthy”"), and interview time against the median. Decipher's scripted
  labels (`${"Agree3" if …}`) are shown as their text.
- The reason pre-filled from the strongest flag; a note required only for
  "other". Keys: ↑ ↓ move, K keep, R remove; after a decision the next
  unreviewed respondent opens.
- "Remove all N" for the ones still marked review first (recorded as "other"
  with that note). Still no bulk keep.
- Rules, imputation and versions folded into one closed section.

Checked in the browser on the local Google Ads study: R removed the first
respondent and opened the next; its evidence ("Google Ads" for all 9 goals)
is the kind a reviewer should see before deciding.

## 2026-10-23 — advanced methods: MaxDiff, TURF, Van Westendorp, key drivers (branch feat/advanced-methods)

Expansion item 8 of 8. Reports → Advanced methods, on the completes, computed
from the data file with numpy (no model). Only the methods the file supports
are offered.

- **MaxDiff** counting scores, (best − worst) / times shown, reading Decipher's
  layout (`<q>_<n>best`, items shown in `MD_Items_<n>`) and DVV's own
  (`<q>_s<n>_best`). Hierarchical-Bayes utilities are not attempted.
- **TURF**: best set of each size by reach (every combination up to 50,000,
  greedy beyond), with frequency.
- **Van Westendorp**: acceptable range (PMC–PME), optimal and indifference
  points; out-of-order answers left out and counted; 30 respondents minimum.
- **Key drivers**: Johnson's relative weights (share of R²), honest under
  correlated ratings; needs max(30, 5 × attributes) complete answers.

Measured on Google Ads: MaxDiff 65 respondents × 12 screens, each item shown
~218 times (a balanced design: 65 × 12 × 4 / 14 ≈ 223); ai_audience_grow
+0.28 first, cash_back −0.19 last. TURF on sales channels: own website 88%,
+ marketplaces 94%, + own stores 97%. Synthetic checks: drivers recover an
80/20 split; Van Westendorp crossings land at the true price.

## 2026-10-21 — one follow-up when an open answer is thin (branch feat/followup-probes)

Expansion item 7 of 8. Questionnaire checks → Follow-up probes: tick the "why"
questions. A respondent whose answer is a stock reply ("ok", "nothing comes
to mind") or under six words gets one follow-up page right after it, worded
for that answer by an open model — neutral, never suggesting an answer
(leading wording is refused) — or a fixed probe when no model answers. Optional
to answer. Asked once per respondent and question and recorded, so the public
link cannot run the model at will. Data gains `<qid>_PROBEQ` (what was asked)
and `<qid>_PROBE` (the reply). The thin rule is one rule in Python and
TypeScript, tested on the same answers.

Measured: on Google Ads 23% of open answers are thin ("Lower the rates",
"Nothing comes to mind"). Job-title questions are short by design — hence
switched on per question, never by default. Checked as a respondent in the
browser: "Lower the rates" → follow-up page, probe recorded on the session;
the welcome screen no longer counts the follow-up as a question.

Not measured: the model-written wording (no model may read client answers
here; Bedrock login expired). The fixed probe is what ran.

## 2026-10-21 — waves and countries: drift, moves, one stacked file (branch feat/tracker-stacking)

Expansion item 6 of 8, second half. Reports → Waves and countries: pick the
other files of a tracker; their data maps are checked against this one —
variables not in every file, codes added or missing (fine to stack), and the
ones that break: a code whose label changed, a list **renumbered** ("Sago" was
3, now 4), a **different question under the same name**, a changed type. With
one other wave picked, the significant moves against it are listed (the
existing wave comparison, which had no screen). **Download stacked SPSS file**
writes every respondent with a SOURCE variable and the codebook, and is
refused while any question breaks. The temporary file is deleted once sent.

Fixed on the way: wave tracking compared codes by number only, so a code
relabelled between waves was reported as a movement; it is now skipped with
the reason.

Measured on the two physician studies (different questionnaires reusing
question ids): 161 of 383 variables shared; 14 questions break — Q6 and Q13
are different questions under the same names, Q15 changed type, and the
sample-source list `vlist` is renumbered (Sago 3 → 4), which would have put
every panel's respondents under the wrong panel in a pooled table.

## 2026-10-21 — translations through the translator's sheet (branch feat/translation-sheets)

Expansion item 6 of 8, first half. A survey can now be fielded in other
languages from one master. Questionnaire checks → Translations: download a
sheet per language (one row per thing a respondent reads — question, answer,
grid row, grid column; UTF-8 with BOM so Excel opens Hindi or Tamil as text),
the translator fills the translation column, upload it back. The upload is
checked: blanks listed, and every piping or scripting token (`[INSERT
PROVIDER]`, `{Q1}`, `[textbox]`) must be kept exactly. Stored on the
questionnaire, so a link opened afterwards carries it; `?lang=hi` on the link
serves the Hindi questionnaire (untranslated strings fall back to the source)
and `lang` lands in the respondent's entry parameters, i.e. in the data.

Measured on Google Ads: 570 strings over 68 questions; 22 carry a token, and a
translator who drops them gets all 22 named on upload.

Not built: a model's first draft of the translation — no model can be run on a
client questionnaire here (free tiers excluded, Bedrock login expired); the
sheet is the industry's own workflow either way.

## 2026-10-21 — sample suppliers and the panel reconciliation file (branch feat/supplier-reconciliation)

Expansion item 5 of 8. Panel redirects, vendor name, CPI, target, the vendor
scorecard and quota balancing already existed, but only as API: nothing in the
app showed them or let anyone set a supplier's price. The Field monitor now has
a Sample suppliers panel — per live link: completes against target, incidence,
share removed in cleaning, **accepted** completes (the ones nobody removed:
what the panel is paid for) and the billable amount — with Edit (name, cost
per complete, target) and a **Reconciliation** download: every respondent's
panel id (pid, else rid/psid), outcome (complete / terminate / overquota /
security / incomplete), accepted or rejected, and the reason a person gave.
Nothing is rejected automatically.

Not built: creating projects and buying sample through a marketplace's own API
(Cint, PureSpectrum, Dynata) — that needs partner accounts and credentials;
every one of them already integrates through the redirect links DVV writes.

Checked in the browser on a scratch database: Cint 12 completes × 3.5 = 42;
PureSpectrum 8 completes, 2 removed as speeders → 6 accepted × 2.8 = 16.8; the
downloaded file lists the two rejections with "speeder: under 4 minutes".

## 2026-10-21 — findings with cited numbers, in the Reports tab and the deck (branch feat/cited-findings)

Expansion item 4 of 8. The deck asked a model for every headline and printed
it unchecked ("No headline / Analysis pending" when no model answered).
Findings are now computed from the tables (`analysis/findings.py`): the
largest significant banner difference, the most chosen options of a
multi-select (its option tables regrouped under the question's wording), the
leading answer; screener questions are the sample's profile, ranked last;
constant tables (100% one answer) say nothing. Every number carries its table,
column and unweighted base; bases under 30 are marked indicative. A model's
sentence is kept only if every number in it is a cell, base, mean or gap of
its table (numbers inside labels like "Under 35" are not claims).

The PowerPoint opens with a Key findings slide, each line cited
(`[T11: Checked n=52, Unchecked n=39]`), and every table slide names its source
and base. Fielded studies now get the PowerPoint too (they had only Excel),
named in the questionnaire's words, with no model call.

Measured: Google Ads (30 auto tables, Total only) → 5 findings + 4 sample
lines, the "USA 100%" country constant dropped; TRD2 with a banner → the one
significant difference (REMS 36% vs 15%) ranked first. Every computed
finding verifies against its own tables.

## 2026-10-21 — pharma compliance: adverse events and the consent notice (branch feat/pharma-compliance)

Expansion item 3 of 8.

**Adverse events.** The Cleaning tab reads every open answer of every
interview (screen-outs and partials included) for a side effect, a medicine
not working, overdose, pregnancy or misuse. *Possible* means someone had it
("my patient developed a rash", "it did not work for him"), to report within a
working day; a *mention* is an event nobody said they had, or denied ("no side
effects"). Nothing is reported or dismissed automatically.

| Study | Interviews | Raw keyword hits | Possible | Mentions |
|---|---|---|---|---|
| TRD1 (physicians) | 131 | 5 | 1 ("if Spravato did not work for them" — hypothetical, a person rules) | 1 |
| TRD2 (physicians) | 91 | 5 | 0 | 4 |
| Google Ads | 254 | 1 | 0 | 0 |

Every raw hit was an opinion ("efficacy and safety", "expand on
hallucinations"), which is why a word list alone is not the rule.

**The notice.** Questionnaire checks lists what respondents are told before
agreeing against DPDP s.5 / Rules r.3 (consent question, purpose, data,
withdrawal, grievance contact, Data Protection Board) and, for a health
study, the adverse-event statement. Neither physician questionnaire had any of
them. One click puts a notice and a consent question first; a "no" ends the
interview. The consent ledger now also finds consent questions by their own
wording (nothing ever set `validation.consent`, so it was always empty for a
questionnaire read from a document).

## 2026-10-21 — a quote read from the questionnaire (branch feat/quote-from-document)

Expansion item 2 of 8. The Questionnaire checks tab opens with a quote: the
interview length as a range (±30%, its measured error), the five longest
questions, the screeners that decide who qualifies, and — from the user's own
completes, incidence, rate per minute, minimum cost per complete and project
fee — starts needed, cost per complete, sample cost and total. When the
document's own length is below the low end, it says the study is likely
under-priced. Nothing guesses a panel's prices.

| Study | Estimate | Document says | Actual median |
|---|---|---|---|
| Google Ads | 22.0 min | 12 | 17.3 |
| TRD1 | 13.0 min | 15 | 19.9 |
| TRD2 | 12.4 min | 15 | 12.4 |

Mean error 21% against the documents' 26%. The timing constants are plain
reading and answering times, not fitted to these three studies.

## 2026-10-21 — one authenticity score per respondent (branch feat/authenticity-score)

Expansion item 1 of 8. Decipher writes, per open end, keystrokes, characters,
the pasted text, keyboard suggestions, dictation and a paste flag; it was all
discarded as "system columns". The Cleaning tab now ranks respondents by one
score (0-100, *review first* ≥ 60, *worth a look* ≥ 30), every reason shown,
removal still a person's decision.

| Evidence | Rule (set on real exports) |
|---|---|
| Pasted answer | a paste (copied text or the export's flag) only where keystrokes are under half the characters — "1,168 characters with 1 keystroke" (TRD2) counts; "65 characters with 111 keystrokes" does not |
| Few keystrokes | long answer, keystrokes under 30% of characters, and no autocomplete, suggestion or dictation to explain it (a phone's "In clu de data…" is not a fault) |
| AI-written text | phrases a language model leaves ("as an AI language model") |
| Too fast to read | finished faster than the words they were shown could be skimmed at 600 a minute (questions and answers seen, not hidden or script columns) |
| Existing checks | speeder, straight-liner, duplicate, gibberish, copied open end, failed trap |

Fixed on the way: cleaning flagged every row of a fielded export, screen-outs
included (now completes only); "gibberish" fired on `uuid`, `session`,
`fp_html5`, `VendorID` (now answer columns only); typing-trace, `session`,
`qtime` and `uniqueid` columns are script columns, not questions.

Measured on completes: Google Ads 62 → 11 flagged (was 37), 1 worth a look;
TRD2 91 → 28 flagged, 7 worth a look, including the 1,168-character paste;
TRD1 17 → 2. Weights are a first setting, to retune on a fielded study's
reviewer decisions.

## 2026-10-20 — model readings stay marked until a person approves them (branch fix/model-readings-flagged)

The Bedrock run (2026-10-19) showed the cost of trusting the model: where it
read a question the rules could not, the question's flags went away, and a
partly-wrong reading became a silent one (Vivo silently wrong 33.8% → 44.9%).
A model's reading passes the same checks as a rule's, so the checks cannot be
what releases it.

| Change | Evidence |
|---|---|
| Every question the model reads carries **"Read by a model — check it"** (`MODEL_READ`), with a plain reason and a next step | unit test |
| It clears only when a person approves that question; "approve clean questions" skips it, so it always gets an individual look | integration test |
| A second-look fix written by the model is **offered** ("fix ready — written by a model, check it against the document"), never applied on its own | second-look and tripwire tests |

Not yet measured on Bedrock: the AWS sign-in expired before the re-run. Expected
effect: Vivo's silently-wrong share back towards the AI-off 33.8% while keeping
the model's accuracy gain (parser 34.6%).

## 2026-10-19 — the model path measured on Bedrock, and what that run found (branch fix/derived-model-rules)

First real run of the model fallbacks (Qwen3-32B on Bedrock, Mumbai) on the six
documents. `scripts/eval_compare.py --with-bedrock` lets the extraction call
Bedrock only — every free tier stays off, as before.

| Found | Fixed |
|---|---|
| Asked for hidden-variable rules, the model **invented values**: ZIP codes for Bubble C7a "CODE STATE & REGION", and they passed (only question names were checked) | a rule's values must be codes of the question it tests, or numbers the document writes |
| Correct model answers refused by our reader: "NOT A AND B" over grid rows, a blank code meaning "everyone else" | compound conditions split only where a new condition starts; exactly one blank code = none of the others |
| "SS_CHANNELS = 2 and/or 4 ONLY (no others)" read as "2 or 4" | `ONLY` and "any options outside 2 and 4 selected at X" read exactly, over X's own codes — now by rule, no model needed |
| Audience codes over Q2c and Q2a counted as overlapping memberships (a recent buyer also "aware, never purchased") | multi-coded only when every code tests something of its own; otherwise a priority list |
| Questions the model read came back **not required** with nothing in the document saying so (9 on Vivo) | optional only where the document says optional / not required / if applicable / may skip |

Hidden variables over the six documents: 13 rules from the document, 4 from the
model (ready for a person to approve, marked "written by a model"), 8 set from
outside the answers, 3 with no rule (two name a question that does not exist).

Gold scores, AI off → Bedrock on: Vivo parser 29.9% → 34.6%, link 27.3% →
32.1%; TRD2 42.9% → 44.8%; TRD1 70.0% → 71.0%; Google Ads B link 69.0% → 70.4%;
Bubble and Google Ads A unchanged. **Open:** where the model reads a question
the rules had flagged, the flag goes and a partly-wrong reading can be left
unflagged — silently wrong rises (Vivo 33.8% → 44.9%, Bubble 33.7% → 38.0%).
Model runs also vary run to run (TRD1 silent 22.6% one run, 0.0% the next).
Vivo Q17b got worse with the model (single read as numeric).

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

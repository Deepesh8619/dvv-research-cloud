# From good to industry-changing — product strategy

Written 2026-10-16 after merging the fielded-data audit (#111). It answers:
*"We say we already do this, but the system is not good enough. It has to be
great — a product that changes market research. Rethink it."*

The architecture note we were given (knowledge base + statistical engines +
workflow rules + agents, with the LLM as coordinator) is the right shape, and
DVV is already built that way. Agreeing with the shape is not the same as being
great. This document says, honestly, where DVV stands, what the industry's real
problem is in 2026, and what would make DVV the product agencies cannot work
without.

---

## 1. The honest position

**What is strong.** A deterministic reader that turns a client's Word
questionnaire into a working survey, checks the questionnaire's own logic, and
now checks fielded data against that logic respondent by respondent. Every
decision is explainable and repeatable. Tabs, weighting, significance, coding,
cleaning and MaxDiff design exist and are tested (1,724 tests).

**What is not.**

| Gap | Why it matters |
|---|---|
| **No study has been fielded end to end.** (ROADMAP Tier 1, open since September) | Every claim is measured on extraction. Agencies buy delivered studies, not parsers. |
| **Word → survey is no longer unique.** Forsta's Decipher sells "AI Survey Scripting" from Word documents. | Our edge cannot be "we read the document". It has to be "we prove it is right". |
| **The middle of the job only.** Nothing before the questionnaire (brief → method → sample) and little after the tables (insight, report). | Displayr (Research Agent, report chat) and quantilope (15 automated methods, AI co-pilot) already own those ends. |
| **Advanced methods are refused.** MaxDiff is designed but not analysed; no conjoint, TURF, pricing (Van Westendorp, Gabor-Granger), key drivers, segmentation. | These are the high-margin studies. quantilope's whole business is automating them. |
| **Respondent quality rules are ours, not evidence.** And we throw away the evidence we have (§3). | Data quality is the industry's #1 problem (below). |
| **Knowledge lives in prose.** `METHODS.md` is 1,300 lines a person reads; the system cannot consult it. | The note's "knowledge base, SOP library, formula library" is exactly this gap. |

## 2. What the industry's problem actually is in 2026

1. **Trust, not speed, is the scarce thing.** About 40% of researchers name data
   quality as their biggest obstacle, and that concern rose sharply year on
   year (GRIT 2025). Brand-side researchers report the lowest satisfaction with
   generative AI. One panel executive estimates 30–40% of collected survey data
   is now questionable, up from 10–15%.
2. **AI is now the fraud.** LLM-driven bots answer surveys convincingly (NORC,
   2026 literature review); 34% of survey takers admit using an LLM for open
   ends; autonomous agents are harder to catch than assisted humans (2026
   preprint). Detection of LLM-written open text reaches 97–99% precision in a
   2026 study.
3. **Synthetic respondents are being sold harder than the evidence supports.**
   Qualtrics launched synthetic panels across three suites (X4, March 2026).
   Independent reviews find only about a quarter of published silicon-vs-human
   comparisons agree, with systematic positive skew and collapsed variance. ESOMAR
   and the Insights Association require validation and disclosure. The accepted
   good use is *pretesting instruments and screening ideas*, not replacing
   respondents.
4. **Governance decides who wins.** Confidence in AI risk management correlates
   with beating business goals (GRIT 2026). ESOMAR publishes "20 Questions" that
   buyers now put to every AI vendor.
5. **Programming errors are silent.** A broken skip does not raise an error; it
   sends people to the wrong place, and one published experiment shows the same
   rating moving from 2.98 to 4.15 stars depending on whether skip logic excluded
   non-users. Late edits that are not retested on every path are the usual cause.

**The opening.** The industry is splitting into *fast and unverifiable* (AI
surveys, synthetic panels) and *slow and trusted* (agencies re-checking
everything by hand). Nobody owns **fast and provably right**. DVV's
deterministic core is the only foundation in this market that can make that
claim honestly, because every decision it makes can be shown.

## 3. The thesis: *verified research*

> Every question's logic proven before field. Every respondent verified human
> and consistent. Every number traceable to rows and a rule. Every sentence of
> insight cited to a number. At the speed of automation.

That sentence is the product. Each part is measurable, and each part is
something competitors either do not do or cannot prove.

A finding from this session shows how close it is. Three of five real client
exports already record, for every open end, keystrokes, character count,
paste, speech-to-text and time per question. DVV classifies all of it as
"system columns" and discards it. In those exports, one completed respondent in
a physician study submitted a 1,168-character answer with one keystroke; another
typed 12 keys for a 61-character answer, twice. Nothing in the product sees
this today.

## 4. Seven pillars

Ordered by leverage for a two-to-three-person team. Each says what it is, what
the note asked for that it covers, and the measure that says it is done.

### P0 — Prove it (blocks everything)
Field one real study end to end: document → link → real respondents → data →
tabs → an analyst outside the team signs it off. *Done when* a signed-off
deliverable exists. Until then, every pillar below is a claim.

### P1 — Respondent authenticity (the trust layer for people)
One score per respondent, with every reason shown, built from evidence the
data already holds:
- **Typing evidence:** keystrokes vs characters, paste, dictation, keyboard
  suggestions, per open end.
- **Time evidence:** per-question time against the median for that question
  (reading speed for its word count), not one overall speeder cut.
- **Logic evidence:** the per-respondent questionnaire audit merged in #111 —
  answers that contradict routing, exclusives, masks.
- **Content evidence:** LLM-written open text, near-duplicate open ends across
  respondents, gibberish, answers unrelated to the question.
- **Consistency evidence:** contradictory answers across questions, straight-
  lining weighted by grid length, impossible combinations.

Thresholds come from research task R9 (evidence, with false-positive rates), not
from us. Removal is always a human decision on a ranked, explained list.
*Done when* a fielded study's removals are each explained, and a seeded set of
known bad respondents is caught at the stated precision.

### P2 — Questionnaire proof (the trust layer for the instrument)
Turn the dry run from simulation into proof: enumerate every reachable path;
show any question no one can reach, any masked list that can be empty for a
real respondent, any pipe with nothing to fill it, any screen-out that can never
fire, any quota cell that cannot fill (ROADMAP 5.1). Re-run automatically on
every edit, so a late change cannot ship untested — the industry's most common
programming failure.
*Done when* a list of seeded logic defects is caught 100%, and every edit shows
which paths it changed.

### P3 — Knowledge as code (the note's three layers, done for our size)
- **Method cards** — one versioned file per method (purpose, when to use, inputs,
  minimum sample, outputs, common mistakes, sources). Read by the planner, the
  copilot and the preflight ("a MaxDiff with 22 items needs ≥ N per version").
- **Formula library** — every statistic as a tested function with its
  reference (sample size, margin of error, NPS, Kish effective base, column
  proportions, TURF reach). The model may cite these, never compute them.
- **SOPs as executable checklists** — data validation, programming QA, tab QA,
  each step a check the system runs and reports, not a page someone reads.

Files in the repository and Postgres — not Neo4j, Qdrant or a new service. Ten
to fifty methods do not need a graph database.
*Done when* the copilot answers method questions only from cards, citing the
card, and every number in a deliverable names the function that made it.

### P4 — Methods engine (the high-margin studies)
In order of client demand: MaxDiff analysis (counts, then hierarchical Bayes
with PyMC), TURF, Van Westendorp and Gabor-Granger pricing, key-driver analysis
(relative weights / Shapley), segmentation (k-means / latent class), then
choice-based conjoint. Each one: design → screens → fielding → estimation →
client output, sharing the `SurveyDefinition` spine. Research task R6 feeds it.
*Done when* each method's output matches a reference implementation on a
published dataset.

### P5 — Study designer (the front end)
Brief → objectives → recommended method (from the cards, with reasons) → sample
size from the formula library → draft questionnaire from a question library
of proven wordings → straight into P2's proof. A designed study arrives already
provable, because it was built from the same model it is checked against.
*Done when* a brief produces a questionnaire that passes P2 with no defects and
a researcher accepts it with edits only.

### P6 — Cited insight (the back end)
The model writes the "so what" only from numbers the engines computed; every
sentence links to its table cells, base and significance; claims on bases below
a threshold are refused. Output to deck, document and dashboard.
*Done when* every sentence in a generated report resolves to a cell, and a
reviewer finds zero unsupported claims on a seeded set.

### P7 — Synthetic pretest (the honest use of synthetic respondents)
Persona-driven model "respondents" take the survey before launch, to find
confusing wording, double-barrelled questions, fatigue points and grids that
invite straight-lining. **Never reported as data**, labelled as a pretest, in
line with ESOMAR guidance. This turns the industry's most over-sold feature into
the one use the evidence supports.
*Done when* it finds seeded wording defects and the report states plainly that
no synthetic answer is ever a result.

### Compounding (runs under every pillar)
- **Resolution memory** (ROADMAP 5.5): every human decision becomes a
  suggestion for the next document — within one client's boundary.
- **Norms**: anonymised benchmarks per question type and category across a
  client's own studies — what large agencies sell as normative databases.
- **Published governance**: our answers to ESOMAR's 20 Questions, with the
  audit trail that backs each one.

## 5. What we will not build

| Idea | Why not |
|---|---|
| Fine-tuning a model on research knowledge | Knowledge belongs in cards and code, where it can be versioned and cited. |
| Neo4j / Qdrant / Kubernetes / LangGraph now | Each is a service to run for a problem we do not have yet. Add one when a measurement shows the need. |
| A web "intelligence agent" (companies, pricing, news) | Desk research is a different product; it pulls the team away from fielding and data. |
| Synthetic respondents as results | The evidence does not support it, and it would spend the trust that is the product. |
| More agents | One planner calling engines is easier to verify than five agents handing off. |

## 6. How we will know it is great

| Measure | Today | Target |
|---|---|---|
| Studies fielded end to end | 0 | 1, then one per month |
| Document → field-ready, proven link | not measured | under 1 hour including QA |
| Logic defects reaching field | not measured | 0 on the seeded set |
| Bad respondents caught (seeded) | not measured | stated precision, from R9 |
| Report sentences without a cited number | not measured | 0 |
| Advanced methods supported end to end | 0 (MaxDiff design only) | MaxDiff, TURF, pricing, drivers |

## 7. Sequence

1. **P0** — field one study (needs the team: respondents, sample, a reviewer).
2. **P1** — authenticity score, starting with typing and time evidence already
   in the exports. Buildable now, measurable on real data now.
3. **P2** — path proof, extending the dry run.
4. **P3** — method cards and formula library (small, unlocks P4–P6).
5. **P4** — MaxDiff analysis first, when R6 reports.
6. **P6**, **P5**, **P7**.

Research tasks R1–R14 (`RESEARCH_TASKS.md`) stay; R9 feeds P1, R6 feeds P4, R10
feeds P5's defaults, R12 feeds the governance answers.

## Sources

- GRIT 2025 summaries: [aytm](https://aytm.com/post/the-2025-grit-report-why-the-future-of-insights-is-human-led-and-ai-powered), [Greenbook Business Outlook](https://www.greenbook.org/insights/artificial-intelligence-and-machine-learning/the-sea-change-insights-from-the-2025-grit-business-outlook), [Rival](https://www.rivaltech.com/blog/2025-grit-insights-practice-report); [GRIT 2026](https://www.greenbook.org/grit/insights-practice-edition)
- Fraud and LLM respondents: [NORC literature review (2026)](https://www.norc.org/content/dam/norc-org/pdf2026/cpss-research-brief-fraud-lit-review.pdf), [Claassen et al., SSCR 2026](https://journals.sagepub.com/doi/10.1177/08944393251408022), [agentic AI vs quality control (2026)](https://arxiv.org/pdf/2608.28597), [LLM pollution of behavioural research](https://arxiv.org/pdf/2508.01390), [Tremendous](https://www.tremendous.com/blog/detect-ai-survey-research-responses/)
- Synthetic respondents: [PyMC Labs](https://www.pymc-labs.com/blog-posts/synthetic-consumers-a-practical-guide), [Radical Innovators](https://radical-innovators.com/en/insights/synthetic-personas-market-research-2026/), [Skimle](https://skimle.com/blog/synthetic-respondents-in-research-promise-pitfalls-and-when-to-use-in-2026)
- Competitors: [Qualtrics X4 2026](https://www.qualtrics.com/articles/news/new-market-research-capabilities-x4-2026/), [SiliconANGLE on Qualtrics synthetic](https://siliconangle.com/2026/03/18/qualtrics-adds-ai-powered-synthetic-data-research-tools-speed-customer-insights/), [Displayr AI report tool](https://www.research-live.com/article/news/displayr-launches-ai-report-tool/id/5141403), [quantilope quinn](https://quantilope.com/resources/quantilope-launches-quinn-ai-co-pilot), [quantilope methods](https://quantilope.com/ci-advanced), [Forsta/Decipher](https://www.pressganey.com/forsta-legacy/?p=34169), [category overview](https://www.getwhy.io/articles/best-ai-market-research-tools)
- AI conversational interviewing: [NORC, Survey Research Methods 2026](https://ojs.ub.uni-konstanz.de/srm/article/view/8624)
- Programming errors: [Data Diggers](https://www.datadiggers-mr.com/blog/poor-survey-programming-the-hidden-cost-of-rushed-research), [QuestionPro skip logic](https://www.questionpro.com/blog/skip-logic-in-surveys/)
- Governance: [ESOMAR 20 Questions for AI-based services](https://esomar.org/newsroom/esomar-launches-20-questions-to-help-buyers-of-ai-based-services-for-market-research-and-insights)

Most competitor and synthetic-respondent figures come from vendor or trade
sources; treat them as directional until the research tasks verify them.

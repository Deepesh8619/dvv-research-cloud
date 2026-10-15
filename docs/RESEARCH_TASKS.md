# Research tasks — making DVV Research Cloud a phenomenal product

For Vishal and Vaibhav. Each task is **research**: find out, compare, measure,
recommend. Deepesh applies the result in the product. Every task says what
question to answer, why it matters (with what our own measurements show), what
to look at, what to hand back, and how we will know it is done.

**Rules for all tasks**

- **No client questionnaires or client data leave our machines.** Not into
  ChatGPT, Claude.ai, Gemini, Groq, or any free tool. Use public questionnaires,
  synthetic ones, or ones you write yourself.
- Cite every claim (link, page, date). "Industry standard" needs a source.
- Hand back a short written report (`docs/research/<task-id>.md`) plus any data
  or sample files. Lead with the recommendation, then the evidence.
- Where a task asks for test documents, also write the **correct reading**
  (what each question asks, who sees it, how it ends) so we can score the
  product against it automatically.

**Where we stand (2026-10-15, AI off, 6 real questionnaires, 372 questions read
independently):** the parser matches an independent reading on 142 questions,
the survey link on 195; 139 are still wrong without a flag; 60 of 169 document
mistakes are caught. The gaps below are what stands between that and a product
a research agency trusts without re-checking every question.

Suggested split: **Vishal — R1–R7** (reading, logic, AI); **Vaibhav — R8–R14**
(respondents, data, standards, market). Swap freely.

---

## Reading questionnaires

### R1. A catalogue of how agencies write questionnaires
**Question:** In how many different ways do research agencies write the same
things — who is asked (bases), screen-outs, grids, loops, piping, answer
conditions, randomisation, hidden variables?
**Why:** every big miss we fixed this month was a convention we had not seen
(`Other >>>`, Word auto-numbering, `[BASE …` without a colon, section headings
carrying a condition). A new client brings new ones.
**Look at:** public questionnaires with documentation (Federal Reserve SHED, CDC
BRFSS, Pew methodology appendices, ESOMAR / Insights Association sample
instruments, university survey archives, published academic questionnaires,
Decipher/Qualtrics example surveys).
**Hand back:** 20+ questionnaires from at least 8 different authors; a table
of conventions (how each writes a base, a screen-out, a grid, a loop, a pipe,
an answer condition) with examples; 10 of them with a correct reading in our
format (`~/dev/DVV/eval/GOLD_SCHEMA.md`).
**Done when:** we can run `scripts/eval_compare.py` on the 10 and get a score.

### R2. Getting everything out of Word and PDF files
**Question:** What does a .docx / .pdf hold that our reader misses?
**Why:** question numbers and answer codes drawn by Word were invisible until
this month; tracked changes, comments, highlights, text boxes and PDF tables are
still partly unread.
**Look at:** python-docx limits, docx2python, Mammoth, pandoc, LibreOffice
headless; for PDF: pdfplumber, Camelot, PyMuPDF, Docling, Unstructured,
AWS Textract (data-retention terms first).
**Hand back:** a comparison on 10 test documents (R1) of what each tool
recovers (numbering, tables, tracked changes, comments, colour/highlight,
text boxes, footnotes), speed and licence; a recommendation.
**Done when:** a list of concrete gaps in our loader, each with the tool or
technique that closes it.

### R3. Measuring "correct" — the reading standard
**Question:** How do we know a reading is right, when two careful readers
differ?
**Why:** our independent readings sometimes assume things the document does
not say (a code 99 that is never printed, "keep Other last" where the document
is silent). We need a protocol, not opinions.
**Look at:** inter-rater agreement (Cohen's / Fleiss' kappa, Krippendorff's
alpha), annotation guidelines practice (NLP/document-AI benchmarks), how
survey QA teams double-check programming.
**Hand back:** a reading protocol (who reads, how disagreements are settled,
what counts as "the document says" vs "a reader assumed"), and an agreement
measurement on 3 questionnaires read by both of you.
**Done when:** the protocol is written into `~/dev/DVV/eval/PROTOCOL.md` and
the agreement numbers are known.

### R4. Which open model should read what the rules cannot
**Question:** For the parts no rule can read (a question written in prose, a
grid described in words, a contradiction), which open-weight model on AWS
Bedrock gives the best structured answer per rupee and per second?
**Why:** production uses Bedrock with open models only (no OpenAI/Anthropic
models in the product). Some questions are only readable with AI today.
**Look at:** the open-weight models Bedrock offers in ap-south-1 (Qwen, Llama,
Mistral, DeepSeek families), structured / JSON output reliability, context
length, price per 1M tokens, latency; Bedrock data-use and retention terms.
**Hand back:** a benchmark on synthetic and public questionnaires (R1) — never
client ones: accuracy per field, JSON validity rate, cost and time per
questionnaire; a recommendation per task (extract, understand brief, logic
review, question agent).
**Done when:** a table we can turn into the model setting per task.

## Survey logic and the respondent link

### R5. How survey platforms express logic — and what we cannot yet
**Question:** What logic do Decipher (Forsta), Qualtrics, Confirmit, Alchemer
and SurveyJS support, and how does each map onto ours?
**Why:** we added counted conditions ("two or more selected"), grid-row
conditions and answer conditions this month; others are still missing
(conditional order — "keep order unless 1 shows first", position rules,
quota-driven routing, loops over a selection with per-iteration logic).
**Look at:** each platform's documentation for display/skip logic, masking,
piping, loops/looping-by-answer, randomisation blocks, quotas, terminate
behaviour.
**Hand back:** a mapping table (feature → each platform → our model → gap), and
for every gap: how a questionnaire writes it, how the platform stores it, and
a proposed representation for us.
**Done when:** each gap has a proposal Deepesh can implement or reject.

### R6. MaxDiff and conjoint done properly
**Question:** What does it take to field and analyse MaxDiff (and later
choice-based conjoint) at agency quality?
**Why:** MaxDiff appears in client work; we flag it as not supported for
analysis.
**Look at:** balanced incomplete block designs, number of screens/items per
screen, design efficiency, analysis (counts, multinomial logit, hierarchical
Bayes), open-source tools (R `support.BWS`, `ChoiceModelR`, Python
`pylogit`/`xlogit`, Stan), how Sawtooth reports results.
**Hand back:** a design recipe (inputs → screens), an analysis recipe with an
open-source implementation, a worked example on synthetic data, and what the
client deliverable should show.
**Done when:** Deepesh can build design + analysis from the recipe.

### R7. Conditions on number boxes and other link limits
**Question:** How do we hide one box in a "must add to 100" question for some
respondents, and what other per-item conditions SurveyJS cannot express?
**Why:** a box shown only to Google Ads users (a real client question) cannot
be hidden today; we flag it instead.
**Look at:** SurveyJS matrixdropdown / multipletext / custom widgets, expression
validators and totals, how the change would alter the stored answer shape.
**Hand back:** options with the data-shape impact of each (what changes in the
saved responses and exports), and a recommended one.
**Done when:** a decision Deepesh can take with the cost known.

## Respondents, data quality, standards

### R8. The respondent experience on a phone
**Question:** Which question layouts produce the best data on mobile —
carousel vs grid, sliders vs buttons, long lists, open-ends — and what makes
respondents drop out?
**Why:** the link is the product the client sees; most panel respondents are
on phones.
**Look at:** published survey-methodology research (Journal of Survey
Statistics and Methodology, Public Opinion Quarterly, AAPOR, ESOMAR, Pew
methods), WCAG 2.2 accessibility for forms.
**Hand back:** recommendations per question type with the evidence behind
each, and an accessibility checklist for the link.
**Done when:** a short list of layout rules Deepesh can apply to the link.

### R9. Catching bad respondents
**Question:** What signals reliably identify speeders, straight-liners, bots,
duplicate people and AI-written open-ends — and at what thresholds?
**Why:** cleaning is built (speeder, straight-line, gibberish, duplicate,
fraud checks) but thresholds are ours, not evidence-based.
**Look at:** academic and industry studies on survey fraud and data quality,
panel providers' quality standards, open-end AI-detection research and its
false-positive rates.
**Hand back:** each signal with a recommended threshold, its known
false-positive rate, and sources; which to flag vs remove automatically.
**Done when:** thresholds Deepesh can put into the cleaning rules.

### R10. Product defaults the industry expects
**Question:** When a questionnaire is silent, what should happen? Required or
optional? Opt-outs ("None", "Other", "Don't know") kept last? Exclusive by
default? Randomise or not?
**Why:** these are the biggest disagreements between our output and an
independent reading (49 on "required", ~45 on "kept last").
**Look at:** ISO 20252, ESOMAR / MRS / Insights Association codes and
guidelines, platform defaults (Decipher, Qualtrics), what agency scripting
guides say.
**Hand back:** a recommended default for each, with sources and the
exceptions.
**Done when:** Deepesh can set the defaults and cite why.

### R11. Weighting, significance and tables a client trusts
**Question:** Do our crosstabs, significance tests and weighting match what
SPSS, Q, WinCross, MarketSight produce?
**Why:** a number that differs from the client's own tool ends trust instantly.
**Look at:** significance tests used in MR tables (column proportions,
effective base under weighting), raking/RIM weighting, how each tool reports.
**Hand back:** the same synthetic dataset tabulated in at least two reference
tools vs ours, with every difference explained.
**Done when:** zero unexplained differences.

### R12. Compliance for the clients we serve
**Question:** What do India's DPDP Act, GDPR, and healthcare-professional
research rules (EphMRA / BHBIA / Insights Association) require of us?
**Why:** healthcare and international studies are already in the pipeline;
consent, erasure and retention are built but not checked against the law.
**Look at:** the regulations themselves, the codes, AWS Bedrock/S3 data
residency and processing terms.
**Hand back:** a checklist of obligations, what we already do, what is
missing, and a draft data-processing note for clients.
**Done when:** each missing item is a concrete task.

### R13. Competitors, prices and what agencies pay for
**Question:** What do agencies pay today for survey programming, hosting and
tabulation, and where do Decipher, Qualtrics, Alchemer, SurveyMonkey
Enterprise, Conjointly and Indian programming vendors fall short?
**Why:** positioning and pricing.
**Look at:** public pricing, agency forums, case studies, conversations with
2–3 agency programmers (no client data).
**Hand back:** a comparison table (features, turnaround, price), the top 5
pains agencies describe, and a pricing recommendation.
**Done when:** a one-page positioning and price proposal.

### R14. How users teach a system, and trust it
**Question:** How do the best document-AI and data tools let a non-programmer
correct them once and have it stick — and show what they did not understand?
**Why:** our "teach this" and "how this questionnaire was read" panels are the
answer to "every client writes differently"; they must be obvious to a
research executive, not just to us.
**Look at:** human-in-the-loop document extraction products, correction UX,
explainability patterns; 3–5 short usability sessions with research staff on
our review page (synthetic questionnaire).
**Hand back:** findings, the 5 most confusing things on our review page, and
concrete changes.
**Done when:** changes Deepesh can apply to the review page.

# Fielding the first real study

Roadmap Tier 1.1. The goal is one deliverable that a person who is not on the
team signs off. Everything else (pricing, case studies, the next feature) waits
on what this teaches. A rehearsal with simulated respondents (2026-10-13,
121 respondents, AI off) already proved the wiring; it said nothing about how
real people answer.

## Before field (you)

1. **Merge and deploy.** Merge `feat/mr-ops-bundle`. Then on the server
   `cd /opt/dvv/deploy && ./dvv upgrade`. It is deployed only when
   `/api/health`'s `commit` matches the merge.
2. **Look at the UI.** Open the Cleaning tab and the survey page on a phone and
   a laptop. Nobody has seen them yet.
3. **Pick a small study.** 50-150 completes, an internal or friendly client,
   a questionnaire you already parse well (Bubble, Vivo, Google Ads). Avoid
   image stimulus, multi-language and multi-select grids; they cannot be fielded.
4. **Name the analyst** outside the team who will accept or reject the result,
   and agree in writing what "accepted" means.
5. **Privacy.** If the document is under NDA, run extraction with AI keys EMPTY
   (CLAUDE.md section 0). Confirm `llm_key_presence()` is all False first.

## Field

1. Upload, review every question yourself (do not bulk-approve; with AI off the
   flags clear without anyone reading), approve.
2. Preflight: fix or accept each blocker by name.
3. Create one link per vendor (vendor name, CPI, target completes). Run a test
   link yourself end to end, on a phone.
4. **Soft launch** a small number and read the field report before opening up.
5. Watch: incidence, median length, the question where people drop off,
   over-quota. If a question has a high drop-off, stop and read it as a
   respondent.

## After field

1. Materialize (the combined route if more than one vendor).
2. Cleaning tab: review every flag. For each, write down whether the flag was
   RIGHT (a person you would remove) or WRONG. This tally is the most valuable
   output of the study: it tells you which checks cry wolf on real people.
3. Lock. Check the bases in the tabs match completes minus removed.
4. Download the `.sav`, the xlsx deck and the cleaning log. Give them to the
   analyst with the questionnaire.
5. Replacement sample: note `replacement_needed`; reopening quota cells is a
   person's call.

## Record (put in the study notes)

| Measure | Value |
|---|---|
| Starts / completes / incidence | |
| Median interview length | |
| Flags raised / correct / wrong, per check | |
| Respondents removed | |
| Questions the parser read wrong, found in review | |
| Things the respondent page got wrong on a phone | |
| Analyst verdict and their change requests | |

## Stop and fix before continuing if

- a respondent sees a question they should not, or a screen-out does not fire;
- completes land in the wrong quota cell;
- a flag points at more good respondents than bad ones (the check is deleted
  or retuned, per the "cries wolf" rule in CLAUDE.md);
- the `.sav` does not open cleanly in SPSS or the labels are wrong.

## Known gaps to keep in mind

- Purge leaves questionnaire copies, corrections and UI overrides.
- Imputed values are in the analysis data only; the delivery file keeps raw
  values and the cleaning log is the record.
- A lock cannot detect a client tab plan made outside the API.
- Short grids can raise a chance straightliner flag.

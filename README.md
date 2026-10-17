# DVV Research Cloud

Turn a client's questionnaire — a Word document written for a human scripter —
into a fielded survey, collected data, and a tabbed deck, without a person
re-typing any of it.

**What it is good at:** reading a real questionnaire correctly, saying what it
could not read, and refusing to field something that would produce unusable
data. **What it is not:** a finished Decipher replacement. See
[Scope](#scope-what-it-does-and-does-not-do).

---

## How it works

[**docs/METHODS.md**](docs/METHODS.md) explains every technique, model and
method in the system — from how a Word document is read to how a significance
letter is computed — assuming no prior knowledge of market research or of this
codebase. Start there to understand *why* the system is built the way it is.

## The path a study takes

```
  .docx / .pdf  ->  extraction  ->  review  ->  preflight  ->  link
                                                                 |
   deck (.xlsx)  <-  crosstabs  <-  coding  <-  data (.sav)  <-  respondents
```

**1 · Intake and extraction.** A Word or PDF questionnaire is read into a
canonical `SurveyDefinition`: questions, answer lists, grids, routing, quotas,
loops. Deterministic first — around two thirds of questions need no model call
at all — with a local open-source model only for the residue. Tracked changes
and strikethrough are honoured: a question struck out of the document is not
fielded.

It reads both conventions real agencies use: **prose** (`Q7. How satisfied…
[ASK IF Q3=1]`) and **spec tables** (`Q Name:` / `Q type:` / `Response list:`).

**2 · Comprehension.** It distinguishes what is asked from what is not (intro
screens, transitions, hidden variables the programmer computes), reads the
relations between questions (bases, masks, pipes, list sources) into a graph,
and reads the study's own structure out of scripter notes. See
[`docs/questionnaire-comprehension.md`](docs/questionnaire-comprehension.md).

**3 · Review.** Every question shows what the parser read, what it could not,
and the evidence for each decision — beside the client's own document,
rendered with its colour, highlights and strikethrough intact. Founders can
teach the parser a client's conventions, and the lesson applies to every
future document of that type (the correction loop).

After everything is read, a **second look** goes back to every flagged question
with the whole questionnaire in view: stale flags are cleared, what the document
settles is fixed by rule, and the rest goes to a model shown the questions it
names and its neighbours (its answer may only use the document's wording). Each
question then carries one plain resolution — *fixed automatically* (undo), *fix
ready* (apply), *note*, or *needs you* with the recommended next step — instead
of a row of flag codes.

**4 · Preflight and fielding.** Before a link exists the whole instrument is
re-read as a respondent and the data file will meet it: questions with no
answers, conditions nobody can satisfy, screen-outs that never fire, masks
that cannot narrow. A 300-respondent dry run runs the routing for real. Then a
public link, quotas, soft launch, paradata, and a live field report.

**5 · Data and delivery.** Responses become a labelled `.sav` with its codebook
— value labels, measure levels and declared missing codes — plus `.xlsx`,
weighted crosstabs with significance testing, verbatim coding, and an Excel
deck.

**Fielded data checked against the questionnaire.** Given the questionnaire and
an exported data file (Excel or SPSS, from this system or another platform), the
*Questionnaire checks* tab finds, respondent by respondent: answers outside the
base, completes that match a screen-out, exclusive answers ticked with others,
more answers than allowed, totals that do not add up, answers outside a mask,
and codes the list never offered. Document and script codes are joined by
answer label, so a reversed or renumbered scale is not reported as an error.

Throughout: **it refuses rather than guesses.** A MaxDiff it cannot analyse, a
grid with no columns — each is named and blocked at preflight rather than
fielded as something that looks like data.

## Scope: what it does and does not do

**Does**

- Word/PDF questionnaires, prose and spec-table conventions
- Single, multi, single-answer grids, numeric, open text, dropdown, ranking,
  info screens; constant sum; loops and carousels; "Other, specify" text boxes
- Routing, screen-outs, section paths, masking, piping, list sources, answer
  conditions, randomisation with anchors, block rotation, quotas
- MaxDiff design and screens
- Preflight, dry run, test links, soft launch, vendor redirects
- `.sav` (in and out), `.xlsx`, weighted crosstabs, sig testing, tracking,
  verbatim coding
- Fielded data checked against the questionnaire, per respondent
- Self-hosted per client — one Compose stack, the client's data never leaves it

**Does not, yet** — see [`docs/ROADMAP.md`](docs/ROADMAP.md)

- Multi-select grids (`GRID_MULTI`)
- Image or video stimulus, so ad/pack/concept testing cannot run
- MaxDiff analysis (utilities) and conjoint
- Multi-language
- Panel APIs (Cint/Lucid/Dynata) beyond redirect templating

**Not yet proven:** no study has been fielded end to end for a client. Every
quality claim below is measured on extraction, not on collected data.

## Measured

Six real client questionnaires (372 questions), AI off, against independent
readings of the same documents — scored by `scripts/eval_compare.py`; every
change and its measurement is in [`docs/CHANGE_TRACKER.md`](docs/CHANGE_TRACKER.md).

| Questionnaire | Parser matches the reading | Survey link matches |
|---|---|---|
| Bubble Consumer Research | 45.7% | 42.4% |
| Google Ads SMB (two versions) | 45.9% / 47.9% | 68.9% / 70.4% |
| TRD prescriber (study 1) | 70.0% | 70.0% |
| TRD referrer (study 2) | 42.9% | 53.6% |
| Vivo AU Wave 2 | 29.9% | 27.3% |

Most remaining differences are a few causes (open product decisions such as
required-by-default, randomisation and anchoring) — listed in the tracker.

1801 backend tests, 123 web tests.

## Quickstart

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest                       # 1801 tests
cd web && npm install && npm run dev
```

Deployment: [`DEPLOY.md`](DEPLOY.md) · demo: [`MVP_DEMO.md`](MVP_DEMO.md)

## Layout

```
src/
├── core/        # the canonical model — SurveyDefinition, Question, DataMap
├── intake/      # readers: .docx, .pdf, tabular, tracked changes
├── adapters/    # external formats in and out (SPSS)
├── engines/
│   ├── questionnaire/   # extraction, comprehension, second look, correction loop (46 modules)
│   ├── fielding/        # preflight, dry run, quotas, paradata, field report
│   ├── tabs/            # crosstabs, weighting, significance, tracking
│   ├── coding/          # verbatim coding
│   ├── qc/              # data vs questionnaire, condition audit, cleaning, fraud
│   └── rag/             # per-study embeddings for the copilot
├── renderers/   # SurveyJS compiler, Excel deck
└── ai/          # model router — open-source models only
api/             # FastAPI
web/             # React review + respondent app
```

**Iron rule:** adapters map in → engines compute over → renderers map out. No
engine reads a source file format directly.

## Documentation

| File | What it is |
|---|---|
| [`docs/METHODS.md`](docs/METHODS.md) | Every technique, model and method, from first principles |
| [`docs/PRODUCT_STRATEGY.md`](docs/PRODUCT_STRATEGY.md) | Where the product is going: "verified research", seven pillars |
| [`docs/CHANGE_TRACKER.md`](docs/CHANGE_TRACKER.md) | Every change with its measurement; open decisions and known issues |
| [`docs/RESEARCH_TASKS.md`](docs/RESEARCH_TASKS.md) | Research tasks for the team, each with what to hand back |
| [`docs/artifacts/`](docs/artifacts/README.md) | Archive of every published write-up (dossier, audits, walkthrough, briefing) |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | What is left, ordered, split by what compounds and what does not |
| [`docs/questionnaire-comprehension.md`](docs/questionnaire-comprehension.md) | How a questionnaire is understood, and what is still not understood |
| [`docs/CHANGELOG-questionnaire-comprehension.md`](docs/CHANGELOG-questionnaire-comprehension.md) | Build log — why each parser decision exists |
| [`docs/CHANGELOG-correction-loop.md`](docs/CHANGELOG-correction-loop.md) | Build log — founders teaching the parser |
| [`docs/LIFECYCLE_AND_ARCHITECTURE.md`](docs/LIFECYCLE_AND_ARCHITECTURE.md) | Study lifecycle and architecture |
| [`docs/WORKFLOWS.md`](docs/WORKFLOWS.md) | How the pieces are used together |
| [`docs/FIRST_STUDY.md`](docs/FIRST_STUDY.md) | Runbook for fielding the first real study end to end |
| [`docs/ARCHITECTURE-agents.md`](docs/ARCHITECTURE-agents.md) | Chat specialists and the model router |
| [`DEPLOY.md`](DEPLOY.md) | Per-client Compose bundle, licensing |
| `docs/obsidian-vault/` | **Stale (July 2026)** — the tab-engine phase. Kept for history. |

## Conventions

Conventional commits · feature branches, never direct to `main` · `pytest` and
`ruff` clean before PR · secrets only in `.env`, never committed ·
open-source models only.

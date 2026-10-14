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

**4 · Preflight and fielding.** Before a link exists the whole instrument is
re-read as a respondent and the data file will meet it: questions with no
answers, conditions nobody can satisfy, screen-outs that never fire, masks
that cannot narrow. A 300-respondent dry run runs the routing for real. Then a
public link, quotas, soft launch, paradata, and a live field report.

**5 · Data and delivery.** Responses become a labelled `.sav` with its codebook
— value labels, measure levels and declared missing codes — plus `.xlsx`,
weighted crosstabs with significance testing, verbatim coding, and an Excel
deck.

Throughout: **it refuses rather than guesses.** A MaxDiff block, a block
rotation it cannot honour, a grid with no columns — each is named and blocked
at preflight rather than fielded as something that looks like data.

## Scope: what it does and does not do

**Does**

- Word/PDF questionnaires, prose and spec-table conventions
- Single, multi, single-answer grids, numeric, open text, dropdown, ranking,
  info screens; constant sum; loops and carousels
- Routing, screen-outs, section paths, masking, piping, list sources,
  randomisation with anchors, quotas
- Preflight, dry run, test links, soft launch, vendor redirects
- `.sav` (in and out), `.xlsx`, weighted crosstabs, sig testing, tracking,
  verbatim coding
- Self-hosted per client — one Compose stack, the client's data never leaves it

**Does not, yet** — see [`docs/ROADMAP.md`](docs/ROADMAP.md)

- Multi-select grids (`GRID_MULTI`)
- Image or video stimulus, so ad/pack/concept testing cannot run
- MaxDiff and conjoint (detected and refused, not fielded)
- Block rotation (detected and refused)
- Multi-language
- Panel APIs (Cint/Lucid/Dynata) beyond redirect templating

**Not yet proven:** no study has been fielded end to end for a client. Every
quality claim below is measured on extraction, not on collected data.

## Measured

Three real client questionnaires, deterministic path only (no model calls):

| Questionnaire | Questions | Reaching a respondent with nothing to pick |
|---|---|---|
| Bubble Consumer Research | 101 | 0 |
| Vivo AU Wave 2 | 70 | 1 |
| Google Ads SMB | 79 | 0 |

1628 backend tests, 112 web tests.

## Quickstart

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest                       # 1628 tests
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
│   ├── questionnaire/   # extraction, comprehension, correction loop (34 modules)
│   ├── fielding/        # preflight, dry run, quotas, paradata, field report
│   ├── tabs/            # crosstabs, weighting, significance, tracking
│   ├── coding/          # verbatim coding
│   ├── qc/              # discrepancy, condition audit, variable roles
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

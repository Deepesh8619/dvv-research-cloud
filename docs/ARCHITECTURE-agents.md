# Agents: what exists, what is missing, and what to build

Written 2026-09-01, in answer to: *"Make a multiagent system which triggers when
needed and if agents need help then llm is called. If not built, research how we
can make one for our project — how many and of what type — so whenever a doc or
excel or any data is uploaded it identifies which agent to trigger and which
operation needs to be performed."*

Short answer: **two thirds of it already exists, under different names, and the
missing third is the orchestrator.** This document says exactly which parts are
real today, which are not, and what to build next.

---

## 1. The rule that governs every agent

> A model is called only when a deterministic reader cannot answer, and it is
> asked the smallest question that resolves the gap.

This is not an aspiration; it is measurable and currently measured:

| Stage | Deterministic | Model |
|---|---|---|
| File identification | `.sav .parquet .xlsx .csv .docx` by rule | only an unrecognised text file |
| Question extraction | 64% of questions across three real client QREs | the remaining 36%, ~263 tokens each |
| Question type | 8 directive families + wording rules | genuinely ambiguous single-vs-multi only |

Every number above is reproducible from the repository. When a stage cannot
show its numbers, it is not finished.

---

## 2. What already exists

### 2.1 Intake classifier — `src/intake/classify.py`

The "which agent do I trigger" router, already built. Returns a
`Classification(category, confidence, reason, method)` over seven categories:
`data_file`, `datamap`, `questionnaire`, `verbatims`, `tab_spec`, `codeframe`,
`unknown`.

Cheap signals first, model last:

- `.sav/.zsav/.parquet` → `data_file` by extension, confidence 0.99
- `.xlsx/.xls` → `data_file`, 0.85 (binary; a text preview would be useless)
- `.csv` with a wide header → `data_file`, 0.75
- `.docx/.doc` → text extracted, then scored against five scripter conventions
  (question labels, `[PN:` notes, `TERMINATE`, spec-table row labels,
  randomisation directives). Two independent hits → `questionnaire`, 0.95.
- Anything else → the model, with a readable preview

`method` records which path answered, so the ratio of rule to model is a
reportable number rather than a belief.

### 2.2 Chat specialists — `src/ai/specialists.py`

Four roles already exist for the analysis chat, separated by **tool ownership,
not by prompt pleading**: Steward (data health), Analyst (crosstabs,
significance, SQL), Showcase (charts, decks), Concierge (formula authoring).
Showcase has no crosstab tool, so it cannot quietly compute a percentage
instead of handing off.

This is prompt-and-tool-schema routing on a stateless backend, not a long-lived
agent runtime. That distinction matters and is the right call: idle specialists
cost nothing because their schemas are never assembled into the request.

### 2.3 Engines — `src/engines/`

The work each agent would perform is already implemented and tested:
`questionnaire` (extraction, correction loop), `coding` (verbatims, codeframe
reuse), `tabs` (crosstab, weighting, significance, tracking), `analysis`
(pivot, charts, quality, codegen), `qc` (respondent checks), `fielding`.

**These are libraries, not agents.** They do not decide when to run.

---

### 2.4 Orchestrator — `src/orchestration/`

An end-to-end pipeline runner **already exists** for the data path, with human
approval gates and three playbooks (`fast_intake`, `standard_intake`,
`strict_intake` — differing only in which gates auto-approve):

```
classify_file()  ->  load data  ->  QC  ->  [gate]  ->  verbatim coding
   ->  crosstabs  ->  Excel/PPT decks  ->  [delivery gate]
```

It maps a classification to a `FileType`, records a `FileManifest`, and carries
a `StudyRun` through `StudyStage` with an `approval_chain`. This is the
"identify which agent to trigger and which operation to perform" machinery,
built and integration-tested.

---

## 3. What is actually missing

Not the orchestrator — two specific things inside it.

### 3.1 The questionnaire is not wired into the pipeline

`orchestrator.py` maps `questionnaire -> FileType.WORD_DOC`, and then step 2 is
`LOAD DATA`. There is no branch that routes a questionnaire to the extraction
pipeline that `api/routes/questionnaire.py` drives. So the two halves of the
system — questionnaire understanding and data processing — run from different
entry points and never meet.

The fix is a branch at step 1: a `questionnaire` classification runs
extract → review → approve and stores the approved `SurveyDefinition` against
the study, instead of falling through to the data loader.

### 3.2 The Auditor does not exist

Nothing compares a data file against the questionnaire it was collected under.
This is the piece the Excel/SPSS phase is really asking for, and §4 sets out
exactly what it would check.

### 3.3 The agents (six, and why not more)

Each agent owns one engine, states its preconditions, and declares what it does
when it gets stuck. Six is the count because it is the number of distinct
*failure conversations* a researcher can have — not the number of file types.

| Agent | Trigger | Does | Escalates to a model when |
|---|---|---|---|
| **Librarian** | any upload | classify, dedupe, attach to study | file type unrecognised after rules |
| **Reader** | `questionnaire` | extract questions, directives, routing | a field the parsers cannot resolve (~36%, per-field residue prompt) |
| **Cartographer** | `datamap` | map variables → questions | a variable name has no obvious question |
| **Auditor** | `data_file` | schema/range/base checks *against the questionnaire* | never — discrepancies are arithmetic, not opinion |
| **Coder** | `verbatims` | codeframe reuse, auto-code | a verbatim matches no existing code |
| **Tabulator** | `tab_spec` | build and run the table plan | a banner definition is ambiguous |

Note **Auditor never calls a model.** "Q7 allows codes 1–5 but the data
contains 6" is a comparison, not a judgement. This is the agent the Excel/SPSS
phase depends on, and it is the cheapest one in the system.

### 3.3 Escalation contract

Every agent, without exception:

1. Does what it can deterministically.
2. Names what it could not do, per field, in a `missing` list.
3. Asks a model **only** about the named gaps, with the resolved context
   supplied and marked "known and correct — do not second-guess".
4. Records the answer's provenance (`extraction_method`, `extraction_evidence`)
   so a human reviews a citation rather than a verdict.
5. Offers what it learned to the correction loop as a candidate rule.

Steps 1–4 are implemented today for Reader. Step 5 is not implemented for any
agent — see §5.

---

## 4. Why the questionnaire work is the foundation

The eventual goal is: *given a data file and the questionnaire it was collected
against, find every row the questionnaire says should not exist.* Concretely,
each extracted field becomes a data assertion:

| Questionnaire field | Assertion on the data |
|---|---|
| `type: single` | exactly one non-null response column |
| `options: 1..5` | no value outside 1–5 (plus declared specials) |
| `terminate_if Q1 in [4,5]` | no completed respondent satisfies it |
| `show_if Q3 = 1` | Q's column is null wherever Q3 ≠ 1 |
| `randomize: true` | option order is not constant across respondents |
| `validation: 18–99` | no value outside the range |

Every one of those is a deterministic check. **The accuracy of the audit is
capped by the accuracy of the extraction** — a missed `terminate_if` is a
discrepancy the Auditor can never find. That is why the extraction work comes
first, and why 64% deterministic with cited evidence matters more than a higher
number reached by guessing.

---

## 5. Agents that learn — built

A correction now writes a **pattern** the deterministic parsers read, not only
prose for the model:

```
LearnedPattern(kind="type_signal", pattern="[SHOWCARD]", value="single")
```

`src/engines/questionnaire/learned.py` holds the four kinds and their
validation; both parsers consult taught patterns before their built-in tables,
because a founder describing their client's convention knows something the
built-ins cannot. Patterns travel the same `*_for_profile` provider seam as
`learned_rules`, so the engines still never import `api.db`.

Measured on a document using a convention nothing built-in knows: **1 of 3
questions parsed with zero model calls before teaching, 3 of 3 after**, verified
through the real upload path. Details and the safety properties are in
`CHANGELOG-correction-loop.md`.

Remaining, in order:

1. Report the rule-vs-model ratio per document, so falling cost is visible
   rather than asserted
2. The questionnaire branch inside the orchestrator (§3.1)
3. The Auditor (§3.2, §4) — the piece the Excel/SPSS phase needs
4. Cartographer, Coder, Tabulator as the data phase lands

---

## 6. Honest status

| Piece | Status |
|---|---|
| Intake classification | **built**, rule-first, tested |
| Chat specialist routing | **built** |
| Engines for every phase | **built**, tested |
| Pipeline orchestrator + approval gates + playbooks | **built**, integration-tested |
| Reader agent behaviour (extract → residue → provenance) | **built**, 64% deterministic |
| Questionnaire branch inside the orchestrator | **not built** — §3.1 |
| Auditor (data vs questionnaire) | **not built** — §3.2, §4 |
| Agents that teach the parsers | **built** — see §5 |

An earlier draft of this document said the orchestrator did not exist. It does
— `src/orchestration/{orchestrator,playbooks,approvals,manifest}.py` — and the
real gap is narrower and more useful to know: the questionnaire never enters
it.

Nothing above is described as finished unless it is tested and measured.

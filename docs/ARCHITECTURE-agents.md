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

## 3. What is missing: the orchestrator

Today, `classify_file` returns a category and a human picks what happens next.
Nothing maps *category → operations → engine → escalation*. That mapping is the
whole of the missing piece, and it is what the Excel/SPSS phase needs.

Proposed as one module, `src/orchestrator/`, with three parts:

### 3.1 The plan

```
FileArrived(path, study_id)
  → classify_file()                     # exists
  → PLAN[category]                      # new: a table, not a model
  → [Operation, Operation, ...]         # ordered, each with a precondition
```

`PLAN` is a literal dict from category to an ordered list of operations. It is
deliberately a **table, not a model call**: choosing what to do with a file
whose type is already known is a lookup, and paying a model to perform a lookup
is the exact waste this system is designed to avoid.

### 3.2 The agents (six, and why not more)

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

## 5. The gap that matters most: agents that learn

The correction loop stores founder corrections as `learned_rules` and injects
them into the **LLM prompt** (`schema.build_learned_rules_block`). That teaches
the model. It does not teach the parsers.

So today, when a founder corrects the same convention on a second client
document, the deterministic path still misses it and still pays for a model
call. The system gets more accurate; it does not get cheaper.

The fix is to let a correction write a **pattern**, not just prose:

```
LearnedPattern(profile, kind="type_signal", pattern="[SHOWCARD]", value="single")
```

The parsers already take `learned_rules` through a `rules_for_profile` callable,
so the seam exists — the engines never import `api.db`. Widening that callable
to also carry patterns is a contained change, and it is what turns "the founders
train it" from a slogan into a measurable falling cost per document.

**This is the highest-value next build.** Recommended order:

1. `LearnedPattern` storage + the correction UI that offers "always treat X as Y"
2. Parsers consult learned patterns before their built-in table
3. Report rule-vs-model ratio per document, so the learning is visible
4. Orchestrator + `PLAN` table (§3.1), with Librarian and Auditor first
5. Cartographer, Coder, Tabulator as the data phase lands

---

## 6. Honest status

| Piece | Status |
|---|---|
| Intake classification | **built**, rule-first, tested |
| Chat specialist routing | **built** |
| Engines for every phase | **built**, tested |
| Reader agent behaviour (extract → residue → provenance) | **built**, 64% deterministic |
| Orchestrator / `PLAN` table | **not built** — §3.1 |
| Auditor (data vs questionnaire) | **not built** — §4 |
| Agents that teach the parsers | **not built** — §5, highest value |

Nothing above is described as finished unless it is tested and measured.

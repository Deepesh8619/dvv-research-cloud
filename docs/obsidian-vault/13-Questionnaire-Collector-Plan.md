# Questionnaire Ingestion + Survey Programming ("Collector") — Full Plan

**Researched/planned by:** Fable 5, 2026-07-07 · **Status:** Plan approved for build, tasks queued for Opus 4.8

---

## Key decisions made in this plan (worth knowing, even though not re-litigating)

1. **`SurveyDefinition` is a NEW model, separate from `DataMap`** — DataMap describes collected data; SurveyDefinition describes the instrument. They join via an explicit `QuestionVariableLink` table, never by mutation. Same model whether a survey was extracted from an old Word doc or authored fresh in DVV — this is the strategic unification that makes "migrate off Decipher" a first-class path.

2. **SurveyJS Form Library (MIT, free) for rendering — explicitly NOT the paid Survey Creator.** DVV authors surveys in its own React builder writing DVV's own `SurveyDefinition`; a server-side compiler translates to SurveyJS JSON only at publish time. Cost: $0. The paid Creator would make SurveyJS's JSON the source of truth instead of DVV's own model — wrong direction.

3. **No new "Questionnaire" navigator specialist.** Extends the existing **Steward** specialist with 2-3 new tools instead (`get_question_details`, `explain_question_base`, `search_questionnaire`) — keeps the token-economy win from the specialist architecture rather than adding a 5th specialist for what's mostly context enrichment.

4. **Expression language is a safe, serializable AST — never raw code.** `{op: "eq"|"in"|"and"|...}`, always paired with the verbatim routing sentence it was derived from. Same language powers ingestion routing, quota cells, and SurveyJS `visibleIf` compilation. Decipher's `<exec>` (arbitrary embedded Python) is an explicit non-goal.

5. **Hallucinated routing logic is the top named risk on real client data**, and it's made *detectable from data*, not just reviewable: every extracted `show_if` gets cross-validated against the actual dataset via DuckDB (non-missing rows of the linked variable should be a subset of rows satisfying the routing condition). Mismatches get flagged `ROUTING_DATA_MISMATCH`.

6. **v1 scope is deliberately small** — explicitly OUT: loops/dynamic blocks, MaxDiff/Conjoint, multi-language, SMS/offline fielding, panel S2S APIs (redirect-only, matching how most agency traffic actually runs), incentives, custom themes. Matches Decipher's own quota-vs-effort tradeoffs, not a compromise unique to DVV.

7. **Status codes match industry convention exactly** (Lucid/Cint): complete=10, terminate=20, security=30, overquota=40 — so agency-supplied vendor redirect URLs work without translation.

Full research citations (Forsta/Decipher Word importer behavior, XML scripting primitives, Qualtrics Survey Flow model, SurveyJS licensing, LLM extraction failure modes, panel redirect protocols) are in the original planning transcript — ask if you want the source list surfaced again.

---

## Task Queue (15 tasks, 2 phases)

### Phase A — Questionnaire Ingestion

| # | Task | Size | Depends on |
|---|---|---|---|
| T1 | `SurveyDefinition` core model | Medium | none — **do first** |
| T2 | Expression evaluator + SurveyJS compiler | Medium | T1 |
| T3 | Questionnaire document intake (docx/PDF → segmented text) | Medium | none — parallel with T1/T2 |
| T4 | LLM extraction pipeline (text → draft SurveyDefinition) | **Large** | T1, T3 — **biggest de-risking task** |
| T5 | Question↔Variable linker + routing/data cross-validator | Medium-Large | T1, T4 |
| T6 | Persistence + API for questionnaire lifecycle | Medium | T4 |
| T7 | Review & correction UI | Large | T6 |
| T8 | Navigator enrichment (Steward tools) | Medium | T5, T6 |

### Phase B — Collector (survey programming & fielding)

| # | Task | Size | Depends on |
|---|---|---|---|
| T9 | Respondent runtime: serve + capture | Large | T2, T6 |
| T10 | Quota engine + status/redirect protocol | Medium-Large | T9 |
| T11 | Response materialization → DataMap → existing engines | Medium | T9 — **the actual payoff, prioritize over T12** |
| T12 | Authoring UI v1 (question builder) | Large | T1, T2, T6 |
| T13 | Field monitor dashboard | Medium | T10 |
| T14 | Fielding quality flags → Steward QC | Small | T11 |
| T15 | End-to-end pilot + hardening (launch gate) | Medium | T7, T10-T13 |

**Parallelization for multiple build sessions:** `{T1,T3} → {T2,T4} → {T5,T6} → {T7,T8} ∥ {T9→T10/T11, T12} → T13/T14 → T15`

**Full task descriptions** (each self-contained enough to hand to a fresh Opus 4.8 build session with zero prior context) are in the original plan — see task tracker for the working copy used during builds.

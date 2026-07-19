# Vision Diagnostic — Full Feedback-Loop Review (2026-07)

**Reviewed by:** Fable 5, at owner's request: "check everything … if the plan or our model is as per our vision or not and check how far we have deviated." Method: verify the original plan's seven load-bearing decisions directly in code, classify every departure as *deliberate deviation* vs *drift/debt*, implement what's actionable, and record the rest.

---

## 1. Architectural invariants — verified in code, all HOLD

| # | Plan decision | Verification | Status |
|---|---|---|---|
| 1 | Expression AST, never raw code (no Decipher-style `<exec>`) | zero `eval(`/`exec(` in survey_definition / compiler / fielding | ✅ |
| 2 | DVV `SurveyDefinition` is source of truth; SurveyJS compile-target only; paid Creator never | zero creator/designer packages; `compile_to_surveyjs` called only at serve time | ✅ |
| 3 | No 5th navigator specialist — extend Steward | exactly 4 `Specialist(` instances; Steward grew tools via T8/T14/T20 | ✅ |
| 4 | Lucid/Cint status codes 10/20/30/40 verbatim | constants in fielding.py + materialize.py | ✅ |
| 5 | Server-authoritative terminates/quotas | integration test asserts terminate logic absent from client payload; quota = atomic conditional UPDATE | ✅ |
| 6 | DataMap ⟂ SurveyDefinition, joined only by `QuestionVariableLink` | linker emits links; neither model mutates the other | ✅ |
| 7 | Hallucinated routing detectable **from data** | cross_validate_routing built (T5) AND wired live (T20) | ✅ |

**Verdict: no architectural drift.** Everything since the plan (T16–T28) extended the architecture without violating it.

## 2. Deliberate deviations (justified, keep monitoring)

- **T25 auto-approval** — the plan implied a human reviews every question; owner feedback ("what's the point of the LLM") changed this to *exception-based* review: verified + zero flags + ≥0.85 confidence auto-approves. The verify pass and blocking flags are the guardrail. **Monitor:** if a fielded study ever shows a wrong auto-approved question, tighten the floor or re-require grids/routing questions to be human-approved.
- **T9′ Postgres re-scope** — forced by the ephemeral-disk production incident; already documented in doc 13's review section. Strictly better than the plan.
- **Beyond-plan accuracy stack (T26 reconciliation, T27 spec-tables, T28 verbatim surfacing)** — unplanned but squarely ON-vision: the vision is "upload any real questionnaire and it just works"; three real client documents each taught the pipeline a convention. The feedback loop *is* the product process now: real doc → diagnose exact gap → fix that gap → regression test.

## 3. Drift / open debt (the honest gap list, ranked)

1. **SECURITY — rogue `changeme123` owner account (`user_5f45e811`) still live in production Postgres.** Created by the pre-guard seeder. Fix is a one-row delete awaiting the owner's go-ahead (or delete via Admin UI). *Oldest open item in the session; should not age further.*
2. **T15 launch gate never formally closed.** Pilot findings keep arriving (healthy) but no signed-off clean end-to-end pass exists: upload real doc → review → approve → field → N respondents → materialize → deck, all green in production, no fixes needed. Close it deliberately, don't let it evaporate.
3. **v2 roadmap doc lost** — the loops/MaxDiff/i18n/SMS/S2S/incentives/themes research completed but the write-up died with a session limit; doc 14 doesn't exist. Loops + piping remain the top feature gaps every real tracker hits (buyer assessment, this vault).
4. **Survey-programming small gaps:** randomization toggles (model + compiler support exist; builder UI doesn't expose them) and piping affordance (SurveyJS interpolates `{qid}` natively; builder has no insert-token affordance). → **Implemented this cycle (T30).**
5. **Audit debt:** `definition_path` dual-write (25 refs of vestigial file writing), 12× dead `sys.path.insert` boilerplate, 5 overlapping root docs. → **Implemented this cycle (T29).**
6. **Infra hardening** (paid Render tier, Postgres backups, custom domain, rotate the DB password that entered chat/shell history) — owner actions, blocks client pitches, ~₹2–3k/mo.
7. **Environment debt:** the graphify knowledge graph indexes the STALE Desktop copy of this repo, so its mandatory-query hooks misdirect every session. Rebuild it against `~/dev/dvv-research-cloud` or retarget/remove the hook.
8. Known small ceilings, documented where they live: fmcg/retail theme buttons at AA-large; duplicate-respondent check blind on fielded data (respondent_id in the comparison set); numeric-validation ranges and option-exclusivity ride in `instructions` pending schema fields.

## 4. Feedback loop — what this cycle implements

- **T29** audit-debt cleanup (definition_path write path removal, dead sys.path lines, docs consolidation).
- **T30** builder randomization toggles + piping insert affordance.
- This document + memory update, so the next session starts from truth.

*The plan doc's own closing line stands: the best version of this file is the next one.*

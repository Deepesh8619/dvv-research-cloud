# Roadmap — what is left, in the order it is worth doing

Written 2026-09-24, against `657de2e`.

Two kinds of work live here and they are not interchangeable:

- **MOAT** — shares the `SurveyDefinition` spine, so one change reaches
  extraction, preflight, the compiler, the data map and the tab engine at once.
  `SurveyDefinition` has 284 edges in the code graph; `Question` has 281. That
  is the leverage. Doing more of these genuinely makes the system better.
- **TABLE STAKES** — removes a buying objection and compounds with nothing.
  Necessary, not differentiating. Doing more of these makes the system
  *sellable*, not *better*.

Every task carries a **done-when** that can be measured, because the discipline
that made the parser work is that each fix was measured on the same three real
client questionnaires.

---

## Tier 0 — Safety. Before any demo. (days)

| # | Task | Kind | Done when |
|---|---|---|---|
| 0.1 | ✅ **Done 2026-09-24 (#66).** **Fixed the re-read hazard.** Refuse a prose re-read when the stored question has `extraction_method="spec_table"`; widen `_reread_is_regression` to catch shrinkage and type reversals, not only a drop to zero. | moat | `Re-read all` on the Google Ads study changes nothing it should not; `HIDDEN_TOPBENEFIT` stays `info`. |
| 0.2 | **Look at the three unviewed UI merges in a browser** (#61 source tile, #62 structure, #63 section banner). | — | Someone has seen each on a phone and a laptop. |
| 0.3 | Commit `graphify-out/` to `.gitignore`. | — | Clean `git status` in every worktree. |

**Nothing below matters if 0.1 ships broken into a demo.**

## Tier 1 — Proof. Unlocks the commercial conversation. (weeks)

| # | Task | Kind | Done when |
|---|---|---|---|
| 1.1 | **Field ONE real study end to end.** Small, internal if necessary. Document → link → real respondents → data file → tabs → an analyst accepts it. | moat | A deliverable exists that a person who is not us signed off. |
| 1.2 | **Deploy the client bundle once, for real.** | table stakes | Someone else's machine runs it. |
| 1.3 | **Exercise the AWS/Bedrock path on real AWS.** | table stakes | A study extracted through Bedrock, not a local model. |

Tier 1 is worth more than Tiers 2–4 combined. Every claim we make is currently
measured on extraction quality alone.

## Tier 2 — Cheap credibility. (days each)

| # | Task | Kind | Done when |
|---|---|---|---|
| 2.1 | ~~SPSS `.sav` export~~ — **already existed**; the gap analysis was wrong. What was actually missing: declared missing values, so "99: Prefer not to answer" counted as a value and a 1-2 satisfaction scale averaged 25.75. **Done 2026-09-24.** | table stakes | ✅ missing codes declared, measure levels set, round-trip tested. |
| 2.2 | **Re-read reaches the spec-table path.** Today a table-parser fix only lands on re-upload. | moat | A spec-table study picks up #58–#60 without re-uploading. |
| 2.3 | ⚠️ **Partly done 2026-09-24.** The symptom is fixed — Vivo is now 0 and all three documents are at zero — but by a different route than this line assumed. `Q21d` declares `[OPEN END]`; a quota grid's `n=166-167 \| n=166-167 \| n=166-167` read as a scale row and re-typed it GRID_SINGLE before its own directive was consulted. A declared free-text type now outranks a shape found below it. **The chunk boundary is STILL wrong** — the chunk still swallows a section header, a placeholder table and a PN quota table — and a question there that does NOT declare its type would still be mis-read. | moat | ✅ Vivo 1 → 0. ❌ chunk boundary open. |
| 2.4 | **Block rotation**, implemented rather than refused. | moat | The Google Ads study fields 50/50 and preflight stops failing it. |

## Tier 3 — Coverage. The four "no"s in a B3 room. (weeks each)

| # | Task | Kind | Done when |
|---|---|---|---|
| 3.1 | **`GRID_MULTI`.** The enum has `GRID_SINGLE` only. "Select all that apply, for each brand" cannot be fielded. | moat | Extraction, compiler, data map and tabs all handle it; a real document proves it. |
| 3.2 | **Image / video stimulus.** `SHOW IMAGE` is recognised and not modelled. **The Vivo campaign-tracking study we parse almost perfectly cannot be run, because it needs ad creative on screen.** | moat | The Vivo study fields with its Radio/Social/Display creative. |
| 3.3 | Sliders, star ratings, semantic differential. | moat | Each round-trips document → link → data. |
| 3.4 | Autocomplete / lookup lists (postcode, brand). | moat | — |
| 3.5 | Constant sum as a first-class type rather than a numeric grid. | moat | — |

3.1 and 3.2 are the two that change the price you can ask.

## Tier 4 — Platform. (months each)

| # | Task | Kind | Done when |
|---|---|---|---|
| 4.1 | **Multi-language.** A Delhi delivery centre runs multi-country studies. | table stakes | One instrument fields in two languages. |
| 4.2 | **Panel APIs** (Cint / Lucid / Dynata) — real-time quota sync, not just redirect templating. | table stakes | Quotas close across two vendors without manual reconciliation. |
| 4.3 | Client-facing dashboard / portal. | table stakes | — |
| 4.4 | WCAG statement and accessibility pass. | table stakes | — |
| 4.5 | **MaxDiff / conjoint** — design generation + utility estimation. Today detected and refused. High-margin studies. Consider partnering rather than building. | moat, large | A MaxDiff fields and produces utilities. |

## Tier 5 — The actual moat. (ongoing)

These are what nobody else is doing, and what the extraction work has been
building toward.

| # | Task | Kind |
|---|---|---|
| 5.1 | **Semantic comprehension checks** — does a pipe have something to fill it *at the point it fires*? Can a masked list be non-empty for a real respondent? Today the dry run simulates; it does not understand. | moat |
| 5.2 | **UI generated from structure** — sections, progress treatment, question-type-aware layout, all derived from what the document states. Started (#63); barely begun. | moat |
| 5.3 | **Correction loop expansion** — founders teaching the parser is the thing that makes every future document cheaper. | moat |
| 5.4 | Chunk-boundary understanding generally — the class of defect `Q21d` belongs to. | moat |

---

## Is "more tasks = better system" true?

**For the moat column, yes**, and measurably: a change to the shared model
reaches six subsystems. Every question type added makes extraction, fielding
and tabs better simultaneously.

**For the table-stakes column, no.** Those close objections and compound with
nothing. They are necessary and they should be done cheaply and fast, not
lovingly.

**The real constraint is not the list, it is that one person is maintaining six
products' worth of surface area.** The honest sequencing rule: do Tier 0 now,
Tier 1 before any pricing conversation, and never start a Tier 4 item while a
Tier 2 item is open.

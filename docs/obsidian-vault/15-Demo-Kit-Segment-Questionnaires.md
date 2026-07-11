# 15 — Demo Kit: Segment Questionnaires

Pre-seeded sample surveys for prospect demos. Six verticals, one 20-question study each, emitted as machine-readable fixtures in `scripts/demo_questionnaires.json` (all six pass `SurveyDefinition.validate()` with zero problems).

## Why these six segments

The Indian research & insights industry reached **USD 3.2bn in FY2024** (+12.6% YoY, MRSI Industry Report 2024), with e-commerce, automotive, and BFSI called out as the sectors driving demand for custom insights; globally, consumer non-durables (~19%) and pharma + media (~16% combined) lead client spend by ESOMAR's sector breakdowns. Sources: [MRSI Industry Report 2024](https://www.mrsi.co.in/newsdetail/indian-research-and-insights-industry-report-2024_90.html), [Research Live — India at $3.2bn](https://www.research-live.com/article/news/indias-market-research-industry-valued-at-32bn/id/5134253), [ESOMAR Global Market Research](https://esomar.org/global-market-research-report).

**FMCG / CPG** — The single largest client category for custom research worldwide (consumer non-durables ~19% of global spend) and the backbone of Indian agency revenue. Dominant study types: U&A (usage & attitudes), brand health tracking, concept/pack testing, pricing. Our demo: a toothpaste U&A — the most instantly recognisable questionnaire in Indian MR.

**Healthcare / Pharma** — Pharma is a top-3 global research spender; in India, OTC and consumer-health work is high-volume agency business. Dominant study types: patient/consumer journey, ATU (awareness-trial-usage), HCP and OTC brand tracking. Our demo: an OTC pain-relief consumer journey.

**BFSI** — MRSI names BFSI among the sectors capitalising on demand for personalised insights; banks and insurers run continuous CX programmes. Dominant study types: CSAT/NPS trackers, product concept tests, channel-experience studies. Our demo: a retail-banking CSAT + NPS tracker.

**Automotive** — A flagship Indian research vertical (also flagged by MRSI); large budgets for pre-purchase and after-sales work. Dominant study types: purchase-intent/funnel studies, brand consideration, clinic/concept tests, SSI/CSI-style service satisfaction. Our demo: a passenger-car purchase-intent & consideration study.

**Telecom / Tech & Durables** — Telcos are among the heaviest brand-track spenders in India (three-player market, constant churn wars). Dominant study types: continuous brand tracking, churn/porting studies, plan/pricing research. Our demo: a mobile-network brand track with churn module.

**Retail / E-commerce** — MRSI explicitly cites e-commerce as a growth driver of Indian research demand; platforms run always-on CX measurement. Dominant study types: platform CSAT/NPS, shopper journey, quick-commerce adoption. Our demo: an e-commerce platform CSAT & loyalty study.

## The six questionnaires

All follow the standard agency skeleton: 4 screeners (age terminate + sensitive-industry screen with `op:"in"` + category-usage terminate), unaided (open_text) + aided (multi) awareness, usage/frequency, a 5-point `grid_single` battery, NPS (numeric 0–10), a detractor follow-up gated `show_if` NPS < 7, purchase intent, one verbatim, 3 demographics. Indian-market brands throughout. Designed to be answerable in ~3 minutes.

| # | segment_slug | Study | Demos study type | Grid battery | Terminates |
|---|---|---|---|---|---|
| 1 | `fmcg-oral-care` | Toothpaste Usage & Attitudes | U&A | Brand satisfaction (5 rows) | S1 age, S2 industry, S3 non-decision-maker |
| 2 | `healthcare-otc-pain` | OTC Pain Relief Journey | Consumer/patient journey | Product satisfaction | S1, S2, S3 category non-user |
| 3 | `bfsi-retail-banking` | Banking Experience Study | CSAT/NPS tracker | Bank satisfaction | S1, S2, S3 no active account |
| 4 | `auto-purchase-intent` | Car Buying Study | Purchase intent / consideration | Attribute importance | S1, S2, S3 no ownership/intent |
| 5 | `telecom-brand-track` | Mobile Network Study | Brand track + churn | Network satisfaction | S1, S2, S3 non-payer |
| 6 | `retail-ecommerce-csat` | Online Shopping Study | Platform CSAT/loyalty | Platform satisfaction | S1, S2, S3 lapsed shopper |

Each fixture object: `{segment_slug, study_title, client_name, survey}` where `survey` is a full `SurveyDefinition.from_dict` payload (`study_id`, `title`, `questions[20]`, one `MAIN` block listing all qids). Question qids: `S1–S4`, `Q1–Q13`, `D1–D3`.

## Demo flow

1. Seed script loads `scripts/demo_questionnaires.json`, creates 6 approved studies, generates public fielding links.
2. Hand the prospect the link for *their* vertical ("here's the kind of study you run every week").
3. They fill it on their phone in ~3 minutes — hitting the terminate logic, the skip-logic detractor follow-up, and the grid along the way (the parts an agency researcher checks first).
4. Their response materialises instantly: crosstabs (e.g. NPS × age × city tier) + AI chat over their own answer alongside any pre-seeded sample base.
5. Close: "You just authored nothing, programmed nothing, and got a live dashboard. That was one respondent — imagine your panel."

Notes: keep a small pre-seeded synthetic base (n≈50–100 via `scripts/generate_sample_data.py` patterns) behind each study so the dashboard isn't a base-of-one; the prospect's own row lands on top of it.

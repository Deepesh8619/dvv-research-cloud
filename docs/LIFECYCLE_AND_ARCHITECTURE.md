# DVV Research Cloud — Lifecycle & Architecture

How one study moves through the platform, seen from each person who touches it,
and the system underneath. Diagrams are Mermaid (render on GitHub / VS Code).

---

## 1. Who uses it

| Role | Who | Can do |
|---|---|---|
| **Owner** | Runs the account | Everything; sees respondent data from approval |
| **Founder** | Methodology lead | Corrections + teaching the parser; sees data from approval |
| **Analyst** | Research exec / DP | Review, approve, links, quotas, tabs; sees data after soft-launch hold or a filled quota |
| **Reviewer** | Checker | Reads and flags; limited writes |
| **Respondent** | Panel member / customer | Answers the survey link — no account |
| **Client** | End customer | Read-only share link to the study's output |

---

## 2. The lifecycle of one study (single-user view)

```mermaid
flowchart TD
    A[Sign in] --> B[Create project]
    B --> C[Upload questionnaire .docx / .pdf]
    C --> D{Extraction<br/>deterministic parsers first,<br/>AI only for leftovers}
    D --> E[Questionnaire review]

    subgraph REVIEW [Review & fix]
      E --> E1[Bucket bar: Screener · Profile · Core · Loops · Open-ends · Hidden]
      E --> E2[Connections: asked if · options from · text uses · decides]
      E --> E3[Edit: type, options, routing, Adds-up-to, Split by…]
      E --> E4[Re-read with current parser / Re-read all]
      E --> E5[Teach this format / Correct this → parser learns]
      E --> E6[Clickable issues → jump to question]
    end

    E1 & E2 & E3 & E4 & E5 & E6 --> F[Approve questions → Finalize]
    F --> G[Data map fixed: every column known]
    G --> H[Dry run: synthetic respondents through every rule]
    H --> I[Test link]
    I --> I1[Populate with dry-run data]
    I --> I2[Jump to any question — earlier answers auto-filled to pass every condition]
    I1 & I2 --> J{Pre-link check}
    J -- fail --> E
    J -- warn: acknowledge --> K[Create live link]
    J -- pass --> K
    K --> L[Soft launch: holds at N completes]
    L --> M[Field report · Live data · Soft-launch QC]
    M -- problem --> E
    M -- OK --> N[Release hold → full field, quotas enforced]
    N --> O[Close link]
    O --> P[Materialize: responses → data file + data map]
    P --> Q[Tabs · crosstabs · deck · copilot questions]
    Q --> R[Deliver: Excel / SPSS / PPTX / client share link]
```

**What stops a mistake reaching a respondent**

1. Review screen issues list + blocking flags (Finalize refuses until resolved).
2. Dry run (conditions that never fire, questions never reached).
3. Pre-link check — enforced on the server; a real link is refused on failures unless "Create anyway" (logged).
4. Soft launch hold + QC on the first completes.

---

## 3. Researcher / scripter POV (market-research developer)

```mermaid
flowchart LR
    subgraph DOC [What the document says]
      d1[Q labels & wording]
      d2["[PN: …] directives"]
      d3[Tables: spec tables, grids, scales, carousels]
      d4[Routing: ASK IF / TERMINATE / SHOW Q26 LIST]
    end

    subgraph SYS [What the system builds]
      s1[Questions + types]
      s2[Bases show_if · screen-outs terminate_if]
      s3[Masks & pipes: options from / text uses]
      s4[Hidden variables HID_ · CODE · SPLIT]
      s5[Loops & carousels → one question per brand/category]
      s6[Constant sums · 0-10 ratings · grids]
    end

    subgraph OUT [What a scripter would hand over]
      o1[Live survey link]
      o2[Data map: every column, labels, missing values]
      o3[Data file: SAV / XLSX, one column per answer]
    end

    d1 --> s1
    d2 --> s2 & s3 & s4
    d3 --> s5 & s6
    d4 --> s2 & s3
    s1 & s2 & s3 & s4 & s5 & s6 --> o1 & o2 & o3
```

**Daily loop for the developer**

```mermaid
flowchart TD
    X[Something reads wrong] --> Y{Is it this document only?}
    Y -- yes --> Z1[Fix by hand: editor · Connections · Split by · Adds up to]
    Y -- "no, it is how this client writes" --> Z2[Teach this format<br/>type phrase · row label · answer tag · randomise ·<br/>screen-out range · hidden variable]
    Z2 --> Z3[Re-read all → fix lands on every question, no re-upload]
    Z1 & Z3 --> Z4[Re-approve → Finalize → pre-link check]
```

---

## 4. Respondent POV (what a panel member experiences)

```mermaid
flowchart TD
    R0[Clicks link from panel] --> R1{Entry checks}
    R1 -- link closed --> RX1[Survey closed screen]
    R1 -- soft-launch hold full --> RX2[Paused screen → panel redirect]
    R1 -- duplicate panel ID --> RX3[Security end → redirect]
    R1 -- OK --> R2[Welcome: est. time, question count]
    R2 --> R3[One question per screen]

    subgraph Q [Question types]
      q1[Choice cards]
      q2[Grid: one statement at a time, slides on]
      q3[0-10 number pad]
      q4[Rank by tapping]
      q5[Number stepper + 'prefer not to answer']
      q6[Adds up to 100 with running total]
    end
    R3 --> Q
    Q --> R4[Each answer saved to server]
    R4 --> R5{Server decides}
    R5 -- screen-out rule fires --> RX4[Not a match → terminate redirect]
    R5 -- continue --> R3
    R5 -- last question --> R6{Quota full?}
    R6 -- yes --> RX5[Over quota → redirect]
    R6 -- no --> R7[Thank you → complete redirect]
```

Rules are **server-authoritative**: the browser never decides who qualifies.
Hidden/computed questions are never shown; masks and pipes are applied live.

---

## 5. Architecture

```mermaid
flowchart TB
    subgraph CLIENTS [Browsers]
      W1[Staff app — React + Tailwind]
      W2[Survey link /r/:token — SurveyJS + custom controls]
      W3[Client share link]
    end

    subgraph API [FastAPI · api/routes]
      A1[auth · studies · share]
      A2[questionnaire: upload, review, map, links, re-read, split, approve]
      A3[fielding: links, test links, preflight, dry run, quotas, /r/ entry·answers·jump·complete]
      A4[materialize: field report, live data, delivery, data access rule]
      A5[corrections + learned patterns]
      A6[copilot — acts only via confirmed cards]
    end

    subgraph ENGINES [src/engines]
      E1["Intake: docx/pdf loader → segment into question chunks"]
      E2["Extraction: spec-table · prose · table readers (deterministic)<br/>→ AI residue only when needed"]
      E3["Survey passes: section paths · intro screens · grid split · carousels ·<br/>constant sums · fill lists · loops · derived vars"]
      E4["Renderer: SurveyDefinition → SurveyJS JSON"]
      E5["Fielding: decisions (terminate/quota) · dry run · path_to · preflight"]
      E6["Materialize: data map + data file · field report · access"]
      E7["Tabs / crosstab · deck renderers · buckets & links"]
    end

    subgraph AI [Model chain — open-source only]
      M1[Ollama local] --> M2[Bedrock Qwen3-32B] --> M3[Groq / others]
    end

    subgraph STORE [Storage]
      S1[(Postgres / SQLite:<br/>studies · questionnaires · links ·<br/>sessions · answers · quota cells · learned patterns)]
      S2[(S3 backups — Mumbai)]
    end

    W1 --> A1 & A2 & A3 & A4 & A5 & A6
    W2 --> A3
    W3 --> A1
    A2 --> E1 --> E2 --> E3 --> E4
    E2 -. only leftovers .-> AI
    A6 -. questions .-> AI
    A3 --> E4 & E5
    A4 --> E6 & E7
    API --> S1
    S1 --> S2
```

**Deployment**

```mermaid
flowchart LR
    Dev[Mac: code + tests] -->|PR → merge to main| GH[GitHub]
    GH -->|./dvv upgrade<br/>backup → pull → build → health check → rollback if unhealthy| LS[AWS Lightsail · Docker Compose<br/>13-203-248-222.sslip.io]
    LS --> BR[Bedrock in-region]
    LS --> S3[S3 offsite backups]
```

---

## 6. Where each object lives

| Object | Created at | Stored in |
|---|---|---|
| SurveyDefinition (questions, routing, masks) | Extraction / review | `questionnaires.definition_json` |
| Data map | Finalize | computed from the definition |
| Link snapshot | Create link (pins that version) | `fielding_links.survey_json` |
| Respondent answers | Every page | `respondent_answers` |
| Disposition (complete/terminate/overquota/security) | Server decision | `respondent_sessions.status_code` |
| Taught conventions | Teach this format | learned patterns (literal phrases, never regex) |
| Synthetic data | Dry run / Populate test link | sessions marked `synthetic` on a test link only |

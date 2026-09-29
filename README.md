<div align="center">
<img src="caffe-nero-logo.png" width="180" alt="Caffè Nero" />

# Nero Loyalty Data Platform

A contract-governed Snowflake pipeline for Caffè Nero's loyalty and sales
data, encompassing ingestion, dbt transformation, governance, and the
dashboards, chatbots, and Power BI marts constructed on top of it.

</div>

<br/>

<img src="docs/assets/pipeline.svg" width="100%" alt="Staging to Bronze to Silver to Gold to Mart pipeline, illustrated as bean to roast to grind to brew to cup" />

<br/>

## Overview

Four CSV extracts (`stores`, `loyalty_customers`, `transactions`,
`loyalty_events`) are ingested into Snowflake, validated against a versioned
data contract, and transformed through a five-stage medallion pipeline. The
resulting outputs comprise a Kimball star schema, three business marts, a
governance and security layer, five dashboards, and twelve Cortex-powered
chatbots. Every component described in this document is implemented,
tested, and deployed against the live account.

## Architecture

```mermaid
flowchart LR
    classDef staging fill:#fdf6e8,stroke:#b8691f,stroke-width:1.5px,color:#1c130d
    classDef bronze fill:#f5e2c4,stroke:#b8691f,stroke-width:1.5px,color:#1c130d
    classDef silver fill:#eef1f2,stroke:#6b7280,stroke-width:1.5px,color:#1c130d
    classDef gold fill:#fbead0,stroke:#b8691f,stroke-width:2px,color:#1c130d
    classDef mart fill:#1c130d,stroke:#1c130d,color:#fdf6e8
    classDef serve fill:#fffaf0,stroke:#c9a227,stroke-width:1.5px,color:#1c130d,stroke-dasharray: 3 3

    CSV["CSV extracts\nstores, customers\ntransactions, events"] --> STG
    STG["00_STAGING\ntyped, watermarked landing"]:::staging --> BRZ
    BRZ["01_BRONZE\ncontract-filtered (dbt)"]:::bronze --> SLV
    SLV["00_SILVER\nlight transform (views)"]:::silver --> GLD
    GLD["02_GOLD\nstar schema: dimensions and facts"]:::gold --> MART
    MART["Marts\nmarketing, operations"]:::mart --> BI
    MART --> DASH
    MART --> CHAT
    BI["Power BI"]:::serve
    DASH["Streamlit dashboards"]:::serve
    CHAT["Cortex chatbots"]:::serve
```

Each stage constitutes a distinct, versioned, contract-governed process
rather than a naming convention.

| Stage | Schema | Owner | Function |
|---|---|---|---|
| **Staging** | `NERO_DB."00_STAGING"` | Ingestion adapters | Typed landing, one row per source record, with a watermark (`ingested_at`) recorded per batch. No validation is applied at this stage. |
| **Bronze** | `NERO_DB."01_BRONZE"` | dbt | Every contract rule is enforced in SQL (`WHERE` and `QUALIFY` clauses), including nullability, enumerated values, foreign keys, and the reward identifier policy. `bronze_transactions` uses dbt's native incremental materialization. |
| **Silver** | `NERO_ANALYTICS."00_SILVER"` | dbt | Light transformation, implemented as views only, with one `silver_*` object per bronze source. |
| **Gold** | `NERO_ANALYTICS."02_GOLD"` | dbt | Kimball star schema comprising `DIM_*` and `FACT_*` objects, including a complete SCD2 customer-tier snapshot. |
| **Marts** | `NERO_ANALYTICS."03/04_MART_*"` | dbt | `mart_store_daily_performance`, `mart_customer_loyalty_activity` (PII-safe), and `mart_reward_redemption_daily`, which constitute the data source consumed by Power BI. |

## Governance

The governance layer operates in parallel with the pipeline rather than as a
downstream addition.

| Schema | Purpose |
|---|---|
| `NERO_DB."02_CONTROL"` | Live contract-rejection rate per dataset (`CONTRACT_REJECTIONS`). |
| `NERO_DB."04_METADATA"` | Freshness metrics (`DATASET_FRESHNESS`), watermarks, and per-model run history, recorded automatically by a dbt `on-run-end` hook. |
| `NERO_DB."05_PII_CONTROL"` | PII column registry, derived from the contract's own tags. |
| `NERO_ANALYTICS."05_QUALITY"` | Persisted results of every `dbt build` test, rather than output visible only to the invoking session. |
| `NERO_GOVERNANCE.SECURITY` / `.COST` | Trust Center findings, login activity, ACCOUNTADMIN role holders, and warehouse or Cortex compute expenditure. |
| `NERO_GOVERNANCE.REPORTING` | A Power BI-facing view layer wrapping the schemas above. |

## Applications built on the platform

- **Platform Governance.** A single Streamlit dashboard comprising six tabs: pipeline health, security and governance, data quality, platform cost, chatbot cost, and chatbot security.
- **Loyalty Engagement and Sales.** A dashboard intended for store operations and marketing stakeholders, organized around three questions: which stores or regions lead or lag in engagement, how loyalty activity relates to spend, and which additional observations merit attention.
- **Nero Assistant and ten leader-persona chatbots.** Cortex Agents operating over a loyalty semantic model and a security-findings search service, each executed under its own least-privilege service role. See [Chatbots](#chatbots) below.
- **Power BI.** A dedicated read-only service identity, scoped exclusively to the three marts listed above.

## Chatbots

Twelve Streamlit chatbots, the Nero Assistant, the Nero Governance
Assistant, and ten leader personas (CEO, CX, CDO, Finance, Marketing,
Operations, Audit, Regional, Security Lead, and Platform Lead), answer
natural-language questions through a Cortex Agent. Each runs under its
own least-privilege role, so a persona can reach only the data its role
has been granted.

Every answer opens with a **Minimum Viable Truth**: the single fact that
answers the question, with its number. Users then choose, per
conversation, how much more the agent writes, trading answer depth
against cost and speed:

| Answer style | Content | Median over 24 test questions |
|---|---|---|
| **Story** (default) | Headline, then *the setup*, *the turn*, and *so what* for that persona | 142 words, 26.4 s |
| **MVT** | Headline only | 34 words, 21.1 s |
| **Include a chart** (switch) | Adds a bar or line chart of the key finding | Disabling it saves a further 2–3 s |

<table>
<tr>
<td width="50%"><img src="docs/assets/chatbot-finance-revenue.png" alt="Nero Finance Assistant in Story style: revenue so far this month, flagged as covering only Sept 18-28, with a daily revenue line chart" /></td>
<td width="50%"><img src="docs/assets/chatbot-ops-mvt-mode.png" alt="Nero Store Operations Assistant in MVT style with the chart switched off: a single Minimum Viable Truth line naming the top stores, and a table" /></td>
</tr>
<tr>
<td><b>Story.</b> The Finance Assistant reports revenue to date, identifies that the data covers only part of the month, and advises against using the figure for month-end targets.</td>
<td><b>MVT.</b> The Store Operations Assistant, set to MVT with the chart disabled, returns a single headline and the supporting table.</td>
</tr>
</table>

Further answer screenshots, the precise story rules, and the measured
cost trade-off are documented in `streamlit_apps/chatbots/README.md`.

## Repository structure

```
sources/definitions/     DCM-managed schema definitions, organized by schema (see its own README)
ingestion/                Data contract (ingestion/contract/) and the generator that produces sources/definitions/
analytics/                dbt project: bronze, silver, gold, marts, snapshots, tests, and macros
account_setup/            Idempotent SQL: roles, grants, warehouses, chatbot stacks, and governance views
streamlit_apps/           Dashboards, chatbots, and leader-persona applications (snowflake.yml is the deployment manifest)
docs/                     Illustration assets referenced by this document
```

## Getting started

```bash
# 1. Generate DCM definitions from the contract
python ingestion/build.py --seed -c <connection>

# 2. Deploy the dbt project (bronze, silver, gold, marts)
cd analytics && dbt deps && dbt build --target prod

# 3. Deploy a dashboard
cd streamlit_apps && snow streamlit deploy platform_governance --replace -c <connection>
```

Refer to `analytics/README.md` and `sources/definitions/README.md` for a
complete account of the ownership boundaries among DCM, dbt, and plain SQL.
`ingestion/README.md` covers how the ingestion engine works, what it
enforces, and how to open a pull request for a new or changed source;
`analytics/CONTRIBUTING.md` covers the same for the transformation layer;
`streamlit_apps/chatbots/README.md` covers the Cortex Analyst and Cortex
Agent stack, including the exact SQL that built it.

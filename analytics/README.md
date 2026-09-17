# nero_analytics (dbt)

dbt owns everything downstream of the DCM-validated source tables:
staging, customer SCD2, Kimball dimensions/facts, and reporting marts.
Snowflake DCM (see `../sources/definitions/`) owns ingestion, contract
validation, and the `VALIDATED_*` tables — **DCM and dbt never manage the
same fully qualified table.**

## Ownership boundary

| Capability | Owner |
|---|---|
| File format, stage, raw landing | Snowflake DCM |
| Data contract, validation, release pointer | Snowflake DCM |
| `NERO_DB.NERO_LOYALTY.VALIDATED_*` | Snowflake DCM |
| Staging and conformance | dbt |
| Customer SCD Type 2 | dbt snapshot + model |
| Kimball dimensions and facts | dbt |
| Marketing / operations marts | dbt |
| Dashboard | Streamlit, reads dbt marts |

Enforced at the grant level: `NERO_DBT_ROLE` has `SELECT` only on
`NERO_DB.NERO_LOYALTY` (verified directly — a `CREATE TABLE` there as
`NERO_DBT_ROLE` fails with `Insufficient privileges`), and owns
`NERO_ANALYTICS` outright.

## Databases and schemas

```
NERO_DB.NERO_LOYALTY              DCM-owned ingestion + validated sources

NERO_ANALYTICS."00_STAGING"       one stg_* view per validated source
NERO_ANALYTICS."01_SNAPSHOTS"     dbt snapshot (customer tier history)
NERO_ANALYTICS."02_GOLD"          Kimball dimensions + facts
NERO_ANALYTICS."03_MART_MARKETING"
NERO_ANALYTICS."04_MART_OPERATIONS"
```

Numbered so Snowsight's alphabetical schema listing reads in pipeline
order. All owned by `NERO_DBT_ROLE`. Quoted identifiers, since Snowflake
requires quoting a name starting with a digit — `dbt_project.yml` sets
`quoting.schema: true` project-wide for this reason, and the case must
match exactly (quoted identifiers are case-sensitive). No `INTERMEDIATE`
or `AUDIT` schema — both were provisioned in PR1 speculatively and never
used by any model, so they were dropped rather than carried forward.

## Local setup

```bash
cd analytics
python -m pip install "dbt-snowflake~=1.11.0"
cp profiles.example.yml ~/.dbt/profiles.yml   # fill in your own values
dbt deps
dbt debug
```

## Delivery plan

Built as a sequence of small PRs — see the project's PR history for the
authoritative record. Summary:

1. **Bootstrap** (this PR): project scaffold, sources, role/grants, CI skeleton.
2. **Staging**: one `stg_*` view per validated source, UK-timezone dates, no aggregation.
3. **Customer SCD2**: `dbt snapshot` (check strategy) + `dim_customer_scd`. Known
   limitation: tier-change events don't carry `old_tier`/`new_tier`, so history is
   *snapshot-observed* (accurate to dbt run cadence), not source-effective-time —
   disclosed via `tier_effective_precision = 'unknown_between_dbt_runs'` on the model.
4. **Dimensions**: `dim_date`, `dim_time`, `dim_store` (Type 1), `dim_customer_scd`
   (Type 2), `dim_reward` (shell — no reward master source exists).
5. **Facts**: `fact_sales_transaction`, `fact_loyalty_event`. No direct fact-to-fact
   joins — aggregate to a compatible grain first.
6. **Marts**: `mart_customer_loyalty_activity`, `mart_store_daily_performance`,
   `mart_reward_redemption_daily`.
7. **CI**: PR builds into an isolated `DBT_CI_<PR_NUMBER>` schema; prod runs after
   DCM deploy + contract seed + ingestion smoke test, before Streamlit deploy.
8. **Streamlit cutover**: dashboard reads only `NERO_ANALYTICS.MART_*`.
9. **Retire `sources/definitions/star_schema.sql`**: only after multi-release
   reconciliation between the old DCM star schema and the new dbt marts passes.

Real reconciliation (steps 3+) needs actual data volume in `VALIDATED_*` —
right now it holds only synthetic smoke-test rows from
`ingestion/smoke_test.py`.

## Testing

Generic: `not_null`, `unique`, `relationships`, `accepted_values` on every model.
Singular (see `tests/`): no customer SCD interval overlap, exactly one current
customer version per `customer_id`, fact-to-staging count/amount reconciliation,
no null dimension surrogate keys, unknown-member rate below an agreed threshold.

## Materialization

| Layer | Materialization | Notes |
|---|---|---|
| Staging | view | |
| Customer history | snapshot | `strategy: check` |
| Dimensions / facts | table | move to incremental `merge` only after reconciliation passes |
| Marts | table or view | depends on query cost once real data is loaded |

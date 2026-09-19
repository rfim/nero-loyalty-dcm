# nero_analytics (dbt)

dbt owns everything from bronze onward: contract filtering, silver
(light transform + SCD), Kimball dimensions/facts, and reporting marts.
Snowflake DCM (see `../sources/definitions/`) owns only the staging
landing tables (`NERO_DB."00_STAGING"`) -- **DCM and dbt never manage the
same fully qualified table.** This is a rename/restructure of what used
to be a 2-layer split (DCM: bronze+silver via PROCESS_BATCH; dbt: staging
onward) -- PROCESS_BATCH is retired (see `../sources/definitions/
ingestion/engine.sql`), and the layer names shifted: what was "bronze"
(raw landing) is now "staging"; what was "silver" (validated, then
dbt-owned after PROCESS_BATCH's retirement) is now "bronze"; what was
dbt's own "staging" (stg_* views) is now "silver".

## Ownership boundary

| Capability | Owner |
|---|---|
| Raw landing (`00_STAGING`) | Snowflake DCM |
| Contract-filtered tables (`01_BRONZE`) | dbt (`analytics/models/bronze/`) |
| Lineage / PII registry (`03_LINEAGE`, `05_PII_CONTROL`) | Snowflake DCM |
| Silver (light transform, SCD) | dbt (`analytics/models/silver/` + snapshot) |
| Kimball dimensions and facts | dbt |
| Marketing / operations marts | dbt |
| Dashboard | Streamlit, reads dbt marts |

Enforced at the grant level: `NERO_DBT_ROLE` has `SELECT` on
`NERO_DB."00_STAGING"` (all tables + future tables) and `CREATE TABLE` on
`NERO_DB."01_BRONZE"` -- nothing else in `NERO_DB` -- and owns
`NERO_ANALYTICS` outright.

## Databases and schemas

```
NERO_DB."00_STAGING"               typed, unvalidated landing (DCM) -- was "00_BRONZE"
NERO_DB."01_BRONZE"                contract-filtered tables (dbt) -- was "01_SILVER"/VALIDATED_*
NERO_DB."02_CONTROL"               retired (was: contract, audit, release pointer, PROCESS_BATCH)
NERO_DB."03_LINEAGE"               pipeline stage/dependency documentation (DCM)
NERO_DB."04_METADATA"              ingestion freshness/reporting (DCM)
NERO_DB."05_PII_CONTROL"           PII column registry (DCM)
NERO_DB.NERO_LOYALTY               Streamlit app + its stage only — the "front door"

NERO_ANALYTICS."00_SILVER"        one silver_* view per bronze source -- was "00_STAGING"/stg_*
NERO_ANALYTICS."01_SNAPSHOTS"     dbt snapshot (customer tier history)
NERO_ANALYTICS."02_GOLD"          Kimball dimensions + facts
NERO_ANALYTICS."03_MART_MARKETING"
NERO_ANALYTICS."04_MART_OPERATIONS"
```

Numbered so Snowsight's alphabetical schema listing reads in pipeline
order, on both sides. Quoted identifiers, since Snowflake requires
quoting a name starting with a digit; the case must match exactly
(quoted identifiers are case-sensitive) — `dbt_project.yml` sets
`quoting.schema: true` project-wide, but `sources.yml` needs the quotes
written literally into the schema string, since project-level quoting
doesn't cascade to sources. No `INTERMEDIATE` or `AUDIT` schema on the
dbt side, and no legacy `DIM_*`/`FACT_*` star schema on the DCM side —
all were either never used or superseded by dbt's own gold layer, and
were dropped rather than carried forward.

## Local setup

```bash
cd analytics
python -m pip install "dbt-snowflake~=1.11.0"
cp profiles.example.yml ~/.dbt/profiles.yml   # fill in your own values
dbt deps
dbt debug
```

## dbt Projects on Snowflake (Snowsight)

This project is also registered as a native `DBT PROJECT` object
(`NERO_ANALYTICS.DBT_PROJECT.NERO_ANALYTICS`, owned by `NERO_DBT_ROLE`), so
it shows up under Projects in Snowsight and can be run from there in
addition to the CLI/CI paths above. Two things make this a separate concern
from the local/CI setup:

- **`dbt_projects_profiles.yml`** replaces `profiles.yml` for this object.
  A native Snowflake dbt project runs *inside* Snowflake under whichever
  role invokes it, so this file only needs `database`/`role`/`warehouse`/
  `schema` per target -- no credentials -- and is safe to commit. When both
  files are present Snowflake prefers this one.
- **Generic test syntax**: the engine behind `DBT PROJECT` objects currently
  runs dbt-core 1.9.4, which predates the `arguments:` nesting convention
  for generic test config (added in later dbt-core). All test blocks in
  this project use the flat, pre-`arguments:` style for that reason --
  locally this only produces a `MissingArgumentsPropertyInGenericTestDeprecation`
  warning (still fully supported), but the nested style fails to compile on
  Snowflake with `macro ... takes no keyword argument 'arguments'`.

Redeploy after a model/test change:

```bash
snow dbt deploy nero_analytics --source . --profiles-dir . \
  -c nero_dbt_test --database NERO_ANALYTICS --schema DBT_PROJECT
```

`snow dbt deploy`'s stage-upload step doesn't reliably respect
`.dbtignore` for `dbt_packages/`/`target/` (a known CLI limitation) --
if `--source .` fails with `Cannot join path to a file`, deploy from a
clean copy that excludes `target/`/`logs/` but keeps `dbt_packages/`
(Snowflake's dbt engine doesn't install packages from the Hub itself
unless an external access integration is attached, so the
already-resolved `dbt_packages/` needs to travel with the upload).

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
8. **Streamlit cutover** (done): dashboard reads only `NERO_ANALYTICS."02_GOLD"`/`"03_MART_MARKETING"`/`"04_MART_OPERATIONS"`.
9. **Retire the legacy DCM star schema** (done): `sources/definitions/star_schema.sql`
   removed and its `DIM_*`/`FACT_*` tables dropped, pulled forward alongside the
   `NERO_DB` schema restructuring rather than waiting for the original multi-release
   reconciliation gate — acceptable since the data involved was still synthetic
   smoke-test rows, not real production history.

Real reconciliation now runs against real data volume in `01_BRONZE.BRONZE_*`
(dbt-owned; formerly `01_SILVER.VALIDATED_*`) -- the real 4-CSV archive plus
daily synthetic/Google Sheets landings, not just synthetic smoke-test rows.
`ingestion/smoke_test.py` was retired along with `PROCESS_BATCH`.

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

# Contributing to the transformation layer

How the dbt project turns bronze into silver, gold, and marts, what is
enforced along the way, and how to open a pull request for a new or
changed model. See `analytics/README.md` for the ownership boundary,
schema layout, and local setup; this document covers the PR path
specifically.

## How it works

```
01_BRONZE (dbt reads, does not own)
    │  ref()
    ▼
00_SILVER   view, light transform (timezone, renames)
    │
    ▼
02_GOLD     table, Kimball dimensions and facts
    │
    ▼
03/04_MART_*  table or view, one mart per stakeholder question
```

Every model is plain dbt: a `.sql` file under `analytics/models/<layer>/`,
referencing its inputs with `ref()` (never a hardcoded schema path), plus
a `.yml` file in the same directory declaring its tests. dbt resolves the
dependency graph from `ref()` calls alone, so a model that does not exist
in the DAG cannot be built or referenced by name.

Two on-run-end hooks (`analytics/macros/log_dbt_test_results.sql` and
`log_pipeline_run.sql`) fire after every `dbt build`, regardless of pass
or fail, and persist what happened into Snowflake: every test's outcome
into `NERO_ANALYTICS."05_QUALITY".DBT_TEST_RESULTS`, and every model's
status, duration, and rows affected into `NERO_DB."04_METADATA".PIPELINE_RUN_LOG`,
with the dataset watermarks refreshed alongside. This is observability,
not enforcement: it records what a run did, it does not decide whether
the run succeeds.

The project also exists as a native `DBT PROJECT` object in Snowsight
(`NERO_ANALYTICS.DBT_PROJECT.NERO_ANALYTICS`), separate from the CLI/CI
path described here. Merging a PR does not redeploy it automatically;
see "After merge" below.

## What is enforced

- **The `ref()`-only rule**, enforced by dbt itself at compile time: a
  model that reaches across the DAG with a hardcoded schema path instead
  of `ref()`/`source()` will still run, but breaks dbt's own dependency
  ordering and lineage. Every model in this project uses `ref()`; keep it
  that way.
- **Generic tests** (`not_null`, `unique`, `relationships`,
  `accepted_values`), declared per column in each layer's `.yml` file.
  These fail the build, not just warn, unless a test is explicitly marked
  `severity: warn`.
- **Singular tests** (`analytics/tests/`): no customer SCD interval
  overlap, exactly one current version per `customer_id`, fact-to-staging
  count and amount reconciliation, no null dimension surrogate keys, and
  an unknown-member rate held below an agreed threshold. These exist
  because a generic test cannot express them; treat a new one of these as
  the right tool whenever a rule spans more than one column or one model.
- **The grant boundary**, enforced by Snowflake, not dbt: `NERO_DBT_ROLE`
  has `SELECT` only on `NERO_DB."00_STAGING"` and `CREATE TABLE` only on
  `NERO_DB."01_BRONZE"`, nothing else in `NERO_DB`. A model that tries to
  write anywhere else in `NERO_DB` fails on privileges, not on dbt logic.
- **CI**, `.github/workflows/dbt-ci.yml`: any PR touching `analytics/**`
  runs `dbt deps`, `debug`, `parse`, `compile`, and a full `dbt build`
  into an isolated `DBT_CI_<PR_NUMBER>` schema under `NERO_DBT_ROLE`, then
  drops that schema on completion regardless of outcome. A red check on
  this workflow means the build itself failed in a real Snowflake schema,
  not a lint warning.

## What is not enforced, stated plainly

Merging to `main` does not automatically redeploy the native `DBT PROJECT`
object Snowsight reads for its DAG view. That redeploy is a separate,
manual `snow dbt deploy` (see `analytics/README.md`'s "dbt Projects on
Snowflake" section) and was found stale by roughly two days during this
project's own history, silently, until someone thought to check it.
Whoever merges a transformation PR that should be visible in Snowsight is
responsible for that redeploy; nothing currently does it for them.

## Opening a PR

1. Identify the layer. A new column on an existing dataset touches
   `bronze` (the contract's job, see `ingestion/README.md`) and
   possibly `silver`/`gold`/marts downstream of it. A new derived metric
   or reporting cut is a `gold` or mart change only.
2. Write the model using `ref()` for every input, and add or update its
   test entries in the layer's `.yml` file in the same PR, not a
   follow-up. A model without at least a primary-key `unique`/`not_null`
   pair is under-tested.
3. If the change affects a fact or a mart, check whether an existing
   singular test in `analytics/tests/` needs a matching update (a new
   dimension usually needs a reconciliation test's column list extended),
   or whether the change warrants a new singular test.
4. Run `dbt build --target dev` locally against your own dev schema
   before opening the PR. `dev` is the configured target in
   `~/.dbt/profiles.yml` per `analytics/README.md`'s local setup; do not
   develop against `prod`.
5. Open the PR against `main`. CI builds it into an isolated
   `DBT_CI_<PR_NUMBER>` schema automatically; wait for that to pass
   rather than relying on the local dev build alone; the isolated schema
   catches grant and environment issues a personal dev schema can hide.
6. State in the PR description which layer changed, what test coverage
   was added, and whether the native `DBT PROJECT` object needs a manual
   redeploy after merge (see above) for the change to be visible in
   Snowsight.

## After merge

Run `dbt build --target prod` (or confirm whichever scheduled task does,
`DBT_DAILY_REFRESH_TASK` runs it daily, but a change merged between
schedule runs is not live until the next one) and, separately, redeploy
the native `DBT PROJECT` object if the change should be visible in
Snowsight's DAG view:

```bash
snow dbt deploy nero_analytics --source . --profiles-dir . \
  -c <connection> --database NERO_ANALYTICS --schema DBT_PROJECT
```

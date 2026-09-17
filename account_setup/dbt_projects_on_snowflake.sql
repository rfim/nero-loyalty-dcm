-- =============================================================================
-- Register the analytics/ dbt project as a native Snowflake DBT PROJECT
-- object, so it shows up under Projects in Snowsight and can be run there,
-- in addition to the existing local-CLI and GitHub Actions CI paths (see
-- analytics/README.md's "dbt Projects on Snowflake" section for the full
-- deploy/run workflow -- that part isn't plain SQL, since it needs to
-- upload project files to a stage via `snow dbt deploy`).
--
-- This file only covers the one piece that *is* plain SQL: the dedicated
-- schema the object lives in. Deliberately separate from the numbered
-- pipeline-stage schemas (00_STAGING, 01_SNAPSHOTS, 02_GOLD, ...), which
-- hold dbt's model *outputs* -- the DBT PROJECT object itself is metadata/
-- tooling, not a model output, so it doesn't belong mixed in with them.
--
-- Idempotent: safe to re-run. Apply with:
--   snow sql -f account_setup/dbt_projects_on_snowflake.sql
-- =============================================================================

USE ROLE NERO_DBT_ROLE;

CREATE SCHEMA IF NOT EXISTS NERO_ANALYTICS.DBT_PROJECT
  COMMENT = 'Home for the native Snowflake DBT PROJECT object (Snowsight integration) -- separate from the numbered pipeline-stage schemas, which hold dbt model outputs, not the project object itself.';

-- The DBT PROJECT object itself (NERO_ANALYTICS.DBT_PROJECT.NERO_ANALYTICS)
-- is created by `snow dbt deploy`, not by this file -- CREATE DBT PROJECT
-- needs project files on a stage, which is a file-upload operation the CLI
-- handles, not something expressible as a standalone SQL statement here.
-- Deployed and owned via the nero_dbt_test connection (NERO_DBT_ROLE), same
-- least-privilege pattern as every other owned object in this repo.

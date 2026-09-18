-- =============================================================================
-- Daily dbt refresh, scheduled shortly after the synthetic data generator
-- (account_setup/synthetic_daily_ingest.sql) so silver/gold/marts don't go
-- stale once bronze starts changing daily. `dbt build` now covers the whole
-- chain in one run -- bronze->silver (analytics/models/silver/) through to
-- staging/gold/marts -- since PROCESS_BATCH was retired in favor of dbt
-- models/tests (see sources/definitions/ingestion/engine.sql). 15-minute
-- offset is generous headroom over the ~30s the generator+landing actually
-- takes.
--
-- Independently cron-scheduled rather than a Snowflake task DAG (AFTER)
-- dependency on purpose -- the ingest task is owned by ACCOUNTADMIN
-- (NERO_DB."02_CONTROL") and this one by NERO_DBT_ROLE
-- (NERO_ANALYTICS.DBT_PROJECT, required: EXECUTE DBT PROJECT tasks must
-- live in the same schema as the dbt project object) -- a cross-owner AFTER
-- dependency works but adds real permission fragility for no benefit here.
--
-- Also temporary alongside the generator it follows -- drop/suspend both
-- together when a real feed replaces the synthetic one.
--
-- Apply with (as NERO_DBT_ROLE, matching NERO_ANALYTICS.DBT_PROJECT's owner):
--   snow sql -f account_setup/synthetic_daily_dbt_refresh.sql -c nero_dbt_test
-- Needs EXECUTE TASK ON ACCOUNT granted to NERO_DBT_ROLE first (run once,
-- as ACCOUNTADMIN -- account-level privilege, not scoped to a schema):
--   GRANT EXECUTE TASK ON ACCOUNT TO ROLE NERO_DBT_ROLE;
-- =============================================================================

CREATE OR REPLACE TASK NERO_ANALYTICS.DBT_PROJECT.DBT_DAILY_REFRESH_TASK
  WAREHOUSE = 'NERO_DBT_WH'
  SCHEDULE = 'USING CRON 15 6 * * * UTC'
  COMMENT = 'Rebuilds the dbt snapshot/gold/marts daily, after SYNTHETIC_DAILY_INGEST_TASK publishes the day''s data. Temporary alongside that task -- see account_setup/synthetic_daily_ingest.sql.'
AS
  EXECUTE DBT PROJECT NERO_ANALYTICS.DBT_PROJECT.NERO_ANALYTICS ARGS = 'build';

ALTER TASK NERO_ANALYTICS.DBT_PROJECT.DBT_DAILY_REFRESH_TASK RESUME;

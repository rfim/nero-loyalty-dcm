-- =============================================================================
-- Data quality audit log -- dbt's test results, persisted.
--
-- dbt runs 74 tests every `dbt build` (schema tests + singular reconcile
-- tests) but only ever reports pass/fail to whoever ran the CLI -- nothing
-- was queryable from Snowflake. This table gives the combined governance
-- dashboard's Data Quality tab something real to show: logged by the
-- log_dbt_test_results on-run-end hook (analytics/macros/
-- log_dbt_test_results.sql), one row per test per `dbt build` run.
--
-- Apply with: snow sql -f account_setup/dbt_quality_log.sql
-- =============================================================================

USE ROLE ACCOUNTADMIN;

CREATE SCHEMA IF NOT EXISTS NERO_ANALYTICS."05_QUALITY"
  COMMENT = 'dbt test-result audit log -- see analytics/macros/log_dbt_test_results.sql.';

CREATE TABLE IF NOT EXISTS NERO_ANALYTICS."05_QUALITY".DBT_TEST_RESULTS (
    INVOCATION_ID   VARCHAR(64)   NOT NULL,
    RUN_STARTED_AT  TIMESTAMP_TZ  NOT NULL,
    TEST_NAME       VARCHAR(300)  NOT NULL,
    STATUS          VARCHAR(20)   NOT NULL,
    FAILURES        NUMBER,
    EXECUTION_TIME  FLOAT,
    "MESSAGE"       VARCHAR(2000),
    LOGGED_AT       TIMESTAMP_TZ  DEFAULT CURRENT_TIMESTAMP()
)
COMMENT = 'One row per dbt test per `dbt build`/`dbt test` invocation. STATUS is pass/fail/error/skipped (dbt''s own test status strings). Logged by the log_dbt_test_results on-run-end hook.';

-- NERO_DBT_ROLE owns the schema/table (dbt created it via the hook, or
-- ACCOUNTADMIN pre-creates it here) -- either way it needs INSERT to log.
GRANT USAGE ON SCHEMA NERO_ANALYTICS."05_QUALITY" TO ROLE NERO_DBT_ROLE;
GRANT INSERT, SELECT ON TABLE NERO_ANALYTICS."05_QUALITY".DBT_TEST_RESULTS TO ROLE NERO_DBT_ROLE;

GRANT USAGE ON SCHEMA NERO_ANALYTICS."05_QUALITY" TO ROLE NERO_GOVERNANCE_ROLE;
GRANT SELECT ON TABLE NERO_ANALYTICS."05_QUALITY".DBT_TEST_RESULTS TO ROLE NERO_GOVERNANCE_ROLE;
GRANT USAGE ON SCHEMA NERO_ANALYTICS."05_QUALITY" TO ROLE NERO_BI_ROLE;
GRANT SELECT ON TABLE NERO_ANALYTICS."05_QUALITY".DBT_TEST_RESULTS TO ROLE NERO_BI_ROLE;

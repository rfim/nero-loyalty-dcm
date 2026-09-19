-- =============================================================================
-- Pipeline run performance + watermark ledger -- lives in NERO_DB."04_METADATA"
-- (see account_setup/control_metadata_rework.sql, which reworked this
-- schema from empty into DATASET_FRESHNESS, a live snapshot view). These
-- two tables are the historical/persisted counterpart: DATASET_FRESHNESS
-- answers "what's the lag right now"; PIPELINE_WATERMARKS answers "what
-- was the watermark as of the last run" (a stored fact, not recomputed
-- live), and PIPELINE_RUN_LOG answers "how has every run performed."
--
-- PIPELINE_WATERMARKS: one row per dataset, upserted at the end of every
-- `dbt build` -- the current incremental position (latest ingested_at
-- published to bronze) and the row count at that watermark. Explicit and
-- queryable, rather than only ever living inside is_incremental()'s
-- MAX(ingested_at) subquery against the target table.
--
-- PIPELINE_RUN_LOG: one row per model per `dbt build` invocation --
-- status, duration, rows affected. Same "was previously invisible outside
-- the CLI" problem DBT_TEST_RESULTS solved for tests (see
-- dbt_quality_log.sql), but for the transform run itself: successful-run
-- history and ETL/pipeline performance in one place.
--
-- Both logged by the log_pipeline_run on-run-end hook (analytics/macros/
-- log_pipeline_run.sql, analytics/macros/log_pipeline_watermarks.sql).
--
-- Apply with: snow sql -f account_setup/dbt_pipeline_metadata.sql
-- =============================================================================

USE ROLE ACCOUNTADMIN;

CREATE TABLE IF NOT EXISTS NERO_DB."04_METADATA".PIPELINE_WATERMARKS (
    DATASET             VARCHAR(100)  NOT NULL,
    WATERMARK_COLUMN    VARCHAR(100)  NOT NULL,
    WATERMARK_VALUE     TIMESTAMP_TZ,
    ROWS_AT_WATERMARK   NUMBER,
    UPDATED_AT          TIMESTAMP_TZ  NOT NULL,
    PRIMARY KEY (DATASET)
)
COMMENT = 'Current incremental watermark per dataset (latest ingested_at published to bronze), upserted at the end of every dbt build by the log_pipeline_watermarks macro. A stored fact, not a live recomputation -- see NERO_DB."04_METADATA".DATASET_FRESHNESS for that.';

CREATE TABLE IF NOT EXISTS NERO_DB."04_METADATA".PIPELINE_RUN_LOG (
    INVOCATION_ID    VARCHAR(64)   NOT NULL,
    RUN_STARTED_AT   TIMESTAMP_TZ  NOT NULL,
    MODEL_NAME       VARCHAR(300)  NOT NULL,
    MATERIALIZATION  VARCHAR(50),
    STATUS           VARCHAR(20)   NOT NULL,
    EXECUTION_TIME   FLOAT,
    ROWS_AFFECTED    NUMBER,
    "MESSAGE"        VARCHAR(2000),
    LOGGED_AT        TIMESTAMP_TZ  DEFAULT CURRENT_TIMESTAMP()
)
COMMENT = 'One row per model per `dbt build` invocation: status (success/error/skipped), execution time, rows affected. Logged by the log_pipeline_run on-run-end hook -- successful-run history and ETL/pipeline performance, previously visible only to whoever ran the CLI.';

-- NERO_DBT_ROLE writes both tables via the on-run-end hook.
GRANT USAGE ON SCHEMA NERO_DB."04_METADATA" TO ROLE NERO_DBT_ROLE;
GRANT INSERT, SELECT, UPDATE ON TABLE NERO_DB."04_METADATA".PIPELINE_WATERMARKS TO ROLE NERO_DBT_ROLE;
GRANT INSERT, SELECT ON TABLE NERO_DB."04_METADATA".PIPELINE_RUN_LOG TO ROLE NERO_DBT_ROLE;

-- NERO_GOVERNANCE_ROLE/NERO_POWERBI_ROLE already have USAGE on this schema
-- (control_metadata_rework.sql, powerbi_governance_reporting.sql) -- just
-- extend SELECT to the two new tables.
GRANT SELECT ON TABLE NERO_DB."04_METADATA".PIPELINE_WATERMARKS TO ROLE NERO_GOVERNANCE_ROLE;
GRANT SELECT ON TABLE NERO_DB."04_METADATA".PIPELINE_RUN_LOG TO ROLE NERO_GOVERNANCE_ROLE;
GRANT USAGE ON SCHEMA NERO_DB."04_METADATA" TO ROLE NERO_POWERBI_ROLE;
GRANT SELECT ON TABLE NERO_DB."04_METADATA".PIPELINE_WATERMARKS TO ROLE NERO_POWERBI_ROLE;
GRANT SELECT ON TABLE NERO_DB."04_METADATA".PIPELINE_RUN_LOG TO ROLE NERO_POWERBI_ROLE;

-- Wrap into the Power BI reporting surface, same pattern as
-- CONTRACT_REJECTIONS/DATASET_FRESHNESS (powerbi_governance_reporting.sql).
CREATE OR REPLACE VIEW NERO_GOVERNANCE.REPORTING.PIPELINE_WATERMARKS AS
SELECT * FROM NERO_DB."04_METADATA".PIPELINE_WATERMARKS;

CREATE OR REPLACE VIEW NERO_GOVERNANCE.REPORTING.PIPELINE_RUN_LOG AS
SELECT * FROM NERO_DB."04_METADATA".PIPELINE_RUN_LOG;

GRANT SELECT ON VIEW NERO_GOVERNANCE.REPORTING.PIPELINE_WATERMARKS TO ROLE NERO_POWERBI_ROLE;
GRANT SELECT ON VIEW NERO_GOVERNANCE.REPORTING.PIPELINE_RUN_LOG TO ROLE NERO_POWERBI_ROLE;

SELECT 'dbt_pipeline_metadata applied' AS STATUS;

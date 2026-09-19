-- =============================================================================
-- Compute separation & cost governance (SnowPro cost-management practices)
--
-- WAREHOUSE and RESOURCE MONITOR are account-level objects — outside what a
-- schema-scoped DCM project (NERO_DB.NERO_LOYALTY.NERO_LOYALTY_PROJECT) can
-- manage. Tracked here as plain SQL and applied with:
--   snow sql -c <connection> -f account_setup/warehouses_and_monitors.sql
--
-- One warehouse per workload, each right-sized and auto-suspended for its
-- own traffic shape, each behind its own resource monitor so a runaway job
-- in one workload can't burn credits meant for another:
--
--   NERO_LOAD_WH  ingestion (staging landing tasks + manual batch loads)  bursty, short
--   NERO_DBT_WH   dbt transforms (snapshot/build)                 bursty, short
--   NERO_BI_WH    Streamlit / Power BI reporting                  interactive,
--                 variable concurrency -> multi-cluster, longer idle timeout
--   NERO_CI_WH    GitHub Actions (DCM plan/deploy, contract seed) bursty, short
--
-- Credit quotas below are conservative demo defaults — raise them with
-- ALTER RESOURCE MONITOR ... SET CREDIT_QUOTA = ... as real usage is observed.
-- =============================================================================

-- ============================ RESOURCE MONITORS =============================
-- NOTIFY is advisory only; SUSPEND lets running queries finish but blocks new
-- ones; SUSPEND_IMMEDIATE cancels in-flight queries too. Three-tier response
-- per monitor is the standard SnowPro pattern: warn, then soft-stop, then
-- hard-stop.

CREATE RESOURCE MONITOR IF NOT EXISTS MON_NERO_LOAD
  WITH CREDIT_QUOTA = 10
  FREQUENCY = MONTHLY
  START_TIMESTAMP = IMMEDIATELY
  TRIGGERS
    ON 75 PERCENT DO NOTIFY
    ON 100 PERCENT DO SUSPEND
    ON 110 PERCENT DO SUSPEND_IMMEDIATE;

CREATE RESOURCE MONITOR IF NOT EXISTS MON_NERO_DBT
  WITH CREDIT_QUOTA = 15
  FREQUENCY = MONTHLY
  START_TIMESTAMP = IMMEDIATELY
  TRIGGERS
    ON 75 PERCENT DO NOTIFY
    ON 100 PERCENT DO SUSPEND
    ON 110 PERCENT DO SUSPEND_IMMEDIATE;

CREATE RESOURCE MONITOR IF NOT EXISTS MON_NERO_BI
  WITH CREDIT_QUOTA = 15
  FREQUENCY = MONTHLY
  START_TIMESTAMP = IMMEDIATELY
  TRIGGERS
    ON 75 PERCENT DO NOTIFY
    ON 100 PERCENT DO SUSPEND
    ON 110 PERCENT DO SUSPEND_IMMEDIATE;

CREATE RESOURCE MONITOR IF NOT EXISTS MON_NERO_CI
  WITH CREDIT_QUOTA = 10
  FREQUENCY = MONTHLY
  START_TIMESTAMP = IMMEDIATELY
  TRIGGERS
    ON 75 PERCENT DO NOTIFY
    ON 100 PERCENT DO SUSPEND
    ON 110 PERCENT DO SUSPEND_IMMEDIATE;

-- =============================== WAREHOUSES ==================================
-- All XSMALL — this dataset is ~50k transaction rows; size up only if a real
-- workload proves it's needed (SnowPro guidance: start small, scale on
-- evidence, prefer more/shorter-lived clusters over one bigger warehouse).

CREATE WAREHOUSE IF NOT EXISTS NERO_LOAD_WH
  WAREHOUSE_SIZE = 'XSMALL'
  AUTO_SUSPEND = 60
  AUTO_RESUME = TRUE
  INITIALLY_SUSPENDED = TRUE
  RESOURCE_MONITOR = MON_NERO_LOAD
  COMMENT = 'Ingestion compute: the 3 staging-landing adapters (Task-triggered and manual). Bursty, short-lived — fast auto-suspend.';

CREATE WAREHOUSE IF NOT EXISTS NERO_DBT_WH
  WAREHOUSE_SIZE = 'XSMALL'
  AUTO_SUSPEND = 60
  AUTO_RESUME = TRUE
  INITIALLY_SUSPENDED = TRUE
  RESOURCE_MONITOR = MON_NERO_DBT
  COMMENT = 'dbt transform compute: snapshot/build. Bursty, short-lived — fast auto-suspend.';

-- MIN/MAX_CLUSTER_COUNT (multi-cluster) omitted: MULTI_CLUSTER_WAREHOUSES is
-- not enabled on this account (Standard edition). Single-cluster XSMALL with
-- a longer auto-suspend is the fallback for this workload shape; revisit
-- multi-cluster if/when the edition supports it and concurrent BI load
-- actually queues.
CREATE WAREHOUSE IF NOT EXISTS NERO_BI_WH
  WAREHOUSE_SIZE = 'XSMALL'
  AUTO_SUSPEND = 300
  AUTO_RESUME = TRUE
  INITIALLY_SUSPENDED = TRUE
  RESOURCE_MONITOR = MON_NERO_BI
  COMMENT = 'Reporting compute: Streamlit app + future Power BI reader. Interactive, variable concurrency; slower auto-suspend so a dashboard demo does not cold-start mid-click.';

CREATE WAREHOUSE IF NOT EXISTS NERO_CI_WH
  WAREHOUSE_SIZE = 'XSMALL'
  AUTO_SUSPEND = 60
  AUTO_RESUME = TRUE
  INITIALLY_SUSPENDED = TRUE
  RESOURCE_MONITOR = MON_NERO_CI
  COMMENT = 'CI/CD compute: GitHub Actions DCM plan/deploy and contract-seed steps. Bursty, short-lived — fast auto-suspend.';

-- ================================ GRANTS ======================================
-- NERO_DBT_ROLE is the only dedicated service role that exists today; wire it
-- to its own warehouse instead of the shared COMPUTE_WH. Ingestion/CI still
-- run as ACCOUNTADMIN in this environment (documented as a follow-up: give
-- each workload its own least-privilege service role, mirroring NERO_DBT_ROLE).

GRANT USAGE ON WAREHOUSE NERO_DBT_WH TO ROLE NERO_DBT_ROLE;

-- =============================================================================
-- Read-only grants for the Pipeline Health dashboard (NERO_GOVERNANCE_ROLE).
--
-- This is the first governance app to read across NERO_DB (ingestion/
-- control) and NERO_ANALYTICS (dbt) in one place -- every governance app
-- until now only touched NERO_GOVERNANCE.* and SNOWFLAKE.ACCOUNT_USAGE.
-- SELECT-only, matching this role's existing read-only posture everywhere
-- else in the account.
--
-- Idempotent: safe to re-run. Apply with:
--   snow sql -f account_setup/pipeline_health_grants.sql
-- =============================================================================

USE ROLE ACCOUNTADMIN;

-- NERO_DB: bronze/silver row counts. No more 02_CONTROL grants -- the
-- audit trail and release pointer PROCESS_BATCH used to write are retired;
-- pipeline health now reads DBT_DAILY_REFRESH_TASK's run history instead
-- (SNOWFLAKE.ACCOUNT_USAGE.TASK_HISTORY, already granted below).
GRANT USAGE ON DATABASE NERO_DB TO ROLE NERO_GOVERNANCE_ROLE;
GRANT USAGE ON SCHEMA NERO_DB."00_BRONZE" TO ROLE NERO_GOVERNANCE_ROLE;
GRANT USAGE ON SCHEMA NERO_DB."01_SILVER" TO ROLE NERO_GOVERNANCE_ROLE;

GRANT SELECT ON ALL TABLES IN SCHEMA NERO_DB."00_BRONZE" TO ROLE NERO_GOVERNANCE_ROLE;
GRANT SELECT ON FUTURE TABLES IN SCHEMA NERO_DB."00_BRONZE" TO ROLE NERO_GOVERNANCE_ROLE;
GRANT SELECT ON ALL TABLES IN SCHEMA NERO_DB."01_SILVER" TO ROLE NERO_GOVERNANCE_ROLE;
GRANT SELECT ON FUTURE TABLES IN SCHEMA NERO_DB."01_SILVER" TO ROLE NERO_GOVERNANCE_ROLE;

-- NERO_ANALYTICS: gold dims/facts row counts (dbt-owned schemas, read-only)
GRANT USAGE ON DATABASE NERO_ANALYTICS TO ROLE NERO_GOVERNANCE_ROLE;
GRANT USAGE ON SCHEMA NERO_ANALYTICS."02_GOLD" TO ROLE NERO_GOVERNANCE_ROLE;
GRANT SELECT ON ALL TABLES IN SCHEMA NERO_ANALYTICS."02_GOLD" TO ROLE NERO_GOVERNANCE_ROLE;
GRANT SELECT ON FUTURE TABLES IN SCHEMA NERO_ANALYTICS."02_GOLD" TO ROLE NERO_GOVERNANCE_ROLE;

-- Task run history reads SNOWFLAKE.ACCOUNT_USAGE.TASK_HISTORY, which needs
-- IMPORTED PRIVILEGES on the SNOWFLAKE database -- already granted to this
-- role in account_setup/governance_role_imported_privileges.sql, so no
-- further grant needed here.
SELECT 'pipeline_health_grants applied' AS STATUS;

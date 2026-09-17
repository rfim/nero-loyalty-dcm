-- =============================================================================
-- NERO_GOVERNANCE — cost & security tracking, kept separate from NERO_DB
-- (application data) and NERO_ANALYTICS (dbt-owned) on purpose: a cost/
-- security tracker should not share fate, ownership, or access grants with
-- the data it's watching, and its own change cadence (new checks, new
-- checks against ACCOUNT_USAGE) is independent of either.
--
-- Account-level object (CREATE DATABASE) plus views over
-- SNOWFLAKE.ACCOUNT_USAGE — outside what the schema-scoped DCM project can
-- manage, so tracked here as plain SQL, same as warehouses_and_monitors.sql.
-- Apply with: snow sql -f account_setup/governance_db.sql
--
-- ACCOUNT_USAGE views have replication latency (45 min-3 hr depending on
-- the view) — this is a governance/audit tracker, not a real-time one.
--
-- All views here are owned by ACCOUNTADMIN and query SNOWFLAKE.ACCOUNT_USAGE
-- directly. Snowflake views execute underlying-object access checks against
-- the OWNER's privileges, so any role later granted SELECT on these views
-- can read them without itself needing ACCOUNT_USAGE access — the standard
-- pattern for exposing a governance dashboard to a non-ACCOUNTADMIN role.
-- =============================================================================

CREATE DATABASE IF NOT EXISTS NERO_GOVERNANCE
  COMMENT = 'Cost and security tracking for the Nero loyalty platform. Reads SNOWFLAKE.ACCOUNT_USAGE — never joins application data in NERO_DB/NERO_ANALYTICS.';

CREATE SCHEMA IF NOT EXISTS NERO_GOVERNANCE.COST
  COMMENT = 'Warehouse credit consumption and budget-vs-actual tracking, one row of context per NERO_*_WH.';

CREATE SCHEMA IF NOT EXISTS NERO_GOVERNANCE.SECURITY
  COMMENT = 'Login activity, role-grant inventory, and least-privilege checks (e.g. who holds ACCOUNTADMIN).';

USE DATABASE NERO_GOVERNANCE;

-- ================================ COST ======================================

-- Reference table, not a view: CREDIT_QUOTA/FREQUENCY on a resource monitor
-- has no ACCOUNT_USAGE view to read back from, so the assignment made in
-- warehouses_and_monitors.sql is mirrored here for the budget-vs-actual
-- comparison below. Keep the two files in sync if a quota changes.
CREATE TABLE IF NOT EXISTS COST.WAREHOUSE_BUDGET (
    WAREHOUSE_NAME   VARCHAR(200)  NOT NULL,
    RESOURCE_MONITOR VARCHAR(200)  NOT NULL,
    CREDIT_QUOTA     NUMBER(10,2)  NOT NULL,
    FREQUENCY        VARCHAR(20)   NOT NULL,
    WORKLOAD         VARCHAR(100)  NOT NULL,
    PRIMARY KEY (WAREHOUSE_NAME)
)
COMMENT = 'Mirrors the CREDIT_QUOTA each NERO_*_WH is budgeted for in account_setup/warehouses_and_monitors.sql.';

MERGE INTO COST.WAREHOUSE_BUDGET t
USING (
    SELECT * FROM VALUES
        ('NERO_LOAD_WH', 'MON_NERO_LOAD', 10, 'MONTHLY', 'Ingestion'),
        ('NERO_DBT_WH',  'MON_NERO_DBT',  15, 'MONTHLY', 'dbt transforms'),
        ('NERO_BI_WH',   'MON_NERO_BI',   15, 'MONTHLY', 'Reporting / BI'),
        ('NERO_CI_WH',   'MON_NERO_CI',   10, 'MONTHLY', 'CI/CD')
    AS s(WAREHOUSE_NAME, RESOURCE_MONITOR, CREDIT_QUOTA, FREQUENCY, WORKLOAD)
) s
ON t.WAREHOUSE_NAME = s.WAREHOUSE_NAME
WHEN MATCHED THEN UPDATE SET
    RESOURCE_MONITOR = s.RESOURCE_MONITOR, CREDIT_QUOTA = s.CREDIT_QUOTA,
    FREQUENCY = s.FREQUENCY, WORKLOAD = s.WORKLOAD
WHEN NOT MATCHED THEN INSERT (WAREHOUSE_NAME, RESOURCE_MONITOR, CREDIT_QUOTA, FREQUENCY, WORKLOAD)
    VALUES (s.WAREHOUSE_NAME, s.RESOURCE_MONITOR, s.CREDIT_QUOTA, s.FREQUENCY, s.WORKLOAD);

CREATE OR REPLACE VIEW COST.WAREHOUSE_CREDITS_DAILY AS
SELECT
    WAREHOUSE_NAME,
    DATE_TRUNC('day', START_TIME)               AS USAGE_DATE,
    SUM(CREDITS_USED)                            AS CREDITS_USED,
    SUM(CREDITS_USED_COMPUTE)                    AS CREDITS_USED_COMPUTE,
    SUM(CREDITS_USED_CLOUD_SERVICES)             AS CREDITS_USED_CLOUD_SERVICES
FROM SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY
WHERE WAREHOUSE_NAME LIKE 'NERO\_%' ESCAPE '\\'
GROUP BY WAREHOUSE_NAME, USAGE_DATE;

COMMENT ON VIEW COST.WAREHOUSE_CREDITS_DAILY IS
  'Daily credit burn per NERO_*_WH from ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY. Subject to that view''s replication latency (up to ~3h).';

CREATE OR REPLACE VIEW COST.BUDGET_VS_ACTUAL_MONTH_TO_DATE AS
SELECT
    b.WAREHOUSE_NAME,
    b.WORKLOAD,
    b.RESOURCE_MONITOR,
    b.CREDIT_QUOTA,
    COALESCE(SUM(d.CREDITS_USED), 0)                                   AS CREDITS_USED_MTD,
    ROUND(COALESCE(SUM(d.CREDITS_USED), 0) / b.CREDIT_QUOTA * 100, 1)  AS PCT_OF_BUDGET_USED
FROM COST.WAREHOUSE_BUDGET b
LEFT JOIN COST.WAREHOUSE_CREDITS_DAILY d
    ON d.WAREHOUSE_NAME = b.WAREHOUSE_NAME
    AND d.USAGE_DATE >= DATE_TRUNC('month', CURRENT_DATE())
GROUP BY b.WAREHOUSE_NAME, b.WORKLOAD, b.RESOURCE_MONITOR, b.CREDIT_QUOTA
ORDER BY PCT_OF_BUDGET_USED DESC;

COMMENT ON VIEW COST.BUDGET_VS_ACTUAL_MONTH_TO_DATE IS
  'One row per NERO_*_WH: this-month credit usage against its resource-monitor quota. >100% means the monitor should already have suspended the warehouse.';

CREATE OR REPLACE VIEW COST.QUERY_COST_BY_ROLE_DAILY AS
SELECT
    WAREHOUSE_NAME,
    ROLE_NAME,
    DATE_TRUNC('day', START_TIME)     AS QUERY_DATE,
    COUNT(*)                          AS QUERY_COUNT,
    SUM(EXECUTION_TIME) / 1000 / 3600 AS EXECUTION_HOURS,
    SUM(CREDITS_USED_CLOUD_SERVICES)  AS CLOUD_SERVICES_CREDITS
FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY
WHERE WAREHOUSE_NAME LIKE 'NERO\_%' ESCAPE '\\'
GROUP BY WAREHOUSE_NAME, ROLE_NAME, QUERY_DATE;

COMMENT ON VIEW COST.QUERY_COST_BY_ROLE_DAILY IS
  'Query volume and execution time by role, per warehouse per day — the attribution granularity warehouse-level cost alone cannot give. Today this will mostly show ACCOUNTADMIN, since ingestion/CI don''t yet run under dedicated roles (see repo follow-up).';

-- =============================== SECURITY ====================================

CREATE OR REPLACE VIEW SECURITY.LOGIN_HISTORY_RECENT AS
SELECT
    EVENT_TIMESTAMP, USER_NAME, CLIENT_IP, REPORTED_CLIENT_TYPE,
    FIRST_AUTHENTICATION_FACTOR, IS_SUCCESS, ERROR_CODE, ERROR_MESSAGE
FROM SNOWFLAKE.ACCOUNT_USAGE.LOGIN_HISTORY
WHERE EVENT_TIMESTAMP >= DATEADD('day', -30, CURRENT_TIMESTAMP())
ORDER BY EVENT_TIMESTAMP DESC;

COMMENT ON VIEW SECURITY.LOGIN_HISTORY_RECENT IS
  'Last 30 days of login attempts, success and failure, account-wide.';

CREATE OR REPLACE VIEW SECURITY.FAILED_LOGINS_DAILY AS
SELECT
    USER_NAME,
    DATE_TRUNC('day', EVENT_TIMESTAMP) AS LOGIN_DATE,
    COUNT(*)                           AS FAILED_ATTEMPTS,
    COUNT(DISTINCT CLIENT_IP)          AS DISTINCT_SOURCE_IPS
FROM SNOWFLAKE.ACCOUNT_USAGE.LOGIN_HISTORY
WHERE IS_SUCCESS = 'NO'
  AND EVENT_TIMESTAMP >= DATEADD('day', -30, CURRENT_TIMESTAMP())
GROUP BY USER_NAME, LOGIN_DATE
ORDER BY FAILED_ATTEMPTS DESC;

COMMENT ON VIEW SECURITY.FAILED_LOGINS_DAILY IS
  'Failed-login attempts by user/day, last 30 days — a spike or a burst of distinct source IPs against one user is the signal worth chasing.';

CREATE OR REPLACE VIEW SECURITY.ROLE_GRANTS_INVENTORY AS
SELECT
    GRANTEE_NAME AS ROLE_NAME, PRIVILEGE, GRANTED_ON, NAME AS OBJECT_NAME,
    TABLE_CATALOG AS DATABASE_NAME, TABLE_SCHEMA AS SCHEMA_NAME,
    GRANTED_BY, CREATED_ON
FROM SNOWFLAKE.ACCOUNT_USAGE.GRANTS_TO_ROLES
WHERE DELETED_ON IS NULL
  AND GRANTED_TO = 'ROLE';

COMMENT ON VIEW SECURITY.ROLE_GRANTS_INVENTORY IS
  'Current (non-revoked) privilege grants to roles, account-wide — the base inventory for any access review.';

CREATE OR REPLACE VIEW SECURITY.ACCOUNTADMIN_HOLDERS AS
SELECT GRANTEE_NAME AS USER_NAME, ROLE AS GRANTED_ROLE, GRANTED_BY, CREATED_ON
FROM SNOWFLAKE.ACCOUNT_USAGE.GRANTS_TO_USERS
WHERE ROLE = 'ACCOUNTADMIN'
  AND DELETED_ON IS NULL;

COMMENT ON VIEW SECURITY.ACCOUNTADMIN_HOLDERS IS
  'Least-privilege check: every user currently holding ACCOUNTADMIN. Today this includes the service user driving ingestion/CI/DCM (ENGINEERSUPPORT26) — see repo follow-up on giving each workload its own least-privilege role instead.';

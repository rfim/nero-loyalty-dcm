-- =============================================================================
-- Cortex usage tracking — extends NERO_GOVERNANCE.COST alongside warehouse
-- credit tracking (see governance_db.sql). Cortex (Cortex Code CLI, LLM
-- functions like COMPLETE, Cortex Analyst, Cortex Search) is billed in
-- credits but NOT through WAREHOUSE_METERING_HISTORY — it's serverless,
-- metered per-request/per-token through its own ACCOUNT_USAGE views. If
-- Cortex Code CLI, Cortex Analyst, or Cortex Search get used against this
-- account, this is where that spend shows up.
--
-- Apply with: snow sql -f account_setup/cortex_usage_tracking.sql
-- =============================================================================

USE DATABASE NERO_GOVERNANCE;

CREATE OR REPLACE VIEW COST.CORTEX_CREDITS_DAILY AS
SELECT
    'Cortex Code CLI'          AS SOURCE,
    USER_NAME                  AS PRINCIPAL,
    CAST(NULL AS VARCHAR)      AS MODEL_NAME,
    DATE_TRUNC('day', USAGE_TIME) AS USAGE_DATE,
    SUM(TOKEN_CREDITS)         AS CREDITS,
    SUM(TOKENS)                AS TOKENS,
    COUNT(*)                   AS REQUEST_COUNT
FROM SNOWFLAKE.ACCOUNT_USAGE.CORTEX_CODE_CLI_USAGE_HISTORY
GROUP BY USER_NAME, USAGE_DATE

UNION ALL

SELECT
    'LLM function (COMPLETE etc.)' AS SOURCE,
    CAST(NULL AS VARCHAR)           AS PRINCIPAL,
    MODEL_NAME,
    DATE_TRUNC('day', START_TIME)  AS USAGE_DATE,
    SUM(TOKEN_CREDITS)              AS CREDITS,
    SUM(TOKENS)                     AS TOKENS,
    COUNT(*)                        AS REQUEST_COUNT
FROM SNOWFLAKE.ACCOUNT_USAGE.CORTEX_FUNCTIONS_USAGE_HISTORY
GROUP BY MODEL_NAME, USAGE_DATE

UNION ALL

SELECT
    'Cortex Analyst'               AS SOURCE,
    USERNAME                       AS PRINCIPAL,
    CAST(NULL AS VARCHAR)           AS MODEL_NAME,
    DATE_TRUNC('day', START_TIME)  AS USAGE_DATE,
    SUM(CREDITS)                    AS CREDITS,
    CAST(NULL AS NUMBER(38,0))      AS TOKENS,
    SUM(REQUEST_COUNT)              AS REQUEST_COUNT
FROM SNOWFLAKE.ACCOUNT_USAGE.CORTEX_ANALYST_USAGE_HISTORY
GROUP BY USERNAME, USAGE_DATE

UNION ALL

SELECT
    'Cortex Search'                 AS SOURCE,
    DATABASE_NAME || '.' || SCHEMA_NAME || '.' || SERVICE_NAME AS PRINCIPAL,
    MODEL_NAME,
    DATE_TRUNC('day', USAGE_DATE)   AS USAGE_DATE,
    SUM(CREDITS)                     AS CREDITS,
    SUM(TOKENS)                      AS TOKENS,
    CAST(NULL AS NUMBER(38,0))       AS REQUEST_COUNT
FROM SNOWFLAKE.ACCOUNT_USAGE.CORTEX_SEARCH_DAILY_USAGE_HISTORY
GROUP BY DATABASE_NAME, SCHEMA_NAME, SERVICE_NAME, MODEL_NAME, USAGE_DATE;

COMMENT ON VIEW COST.CORTEX_CREDITS_DAILY IS
  'Daily Cortex credit burn across the account — Cortex Code CLI, LLM functions (COMPLETE etc.), Cortex Analyst, Cortex Search. Serverless: not tied to any NERO_*_WH, so not part of the warehouse budget-vs-actual view. Empty today (no Cortex usage against this account yet) — this is the view to check once any Cortex product is actually invoked.';

CREATE OR REPLACE VIEW COST.ALL_COMPUTE_CREDITS_DAILY AS
SELECT CAST(USAGE_DATE AS DATE) AS USAGE_DATE, 'Warehouse: ' || WAREHOUSE_NAME AS COMPUTE_SOURCE, CREDITS_USED AS CREDITS
FROM COST.WAREHOUSE_CREDITS_DAILY
UNION ALL
SELECT CAST(USAGE_DATE AS DATE) AS USAGE_DATE, 'Cortex: ' || SOURCE AS COMPUTE_SOURCE, CREDITS
FROM COST.CORTEX_CREDITS_DAILY;

COMMENT ON VIEW COST.ALL_COMPUTE_CREDITS_DAILY IS
  'Single daily rollup across every metered compute source in this account — warehouses and Cortex alike — for a one-query answer to "what did we spend today."';

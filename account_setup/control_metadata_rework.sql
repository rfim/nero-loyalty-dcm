-- =============================================================================
-- Reworks two schemas left dead/broken by earlier migrations, instead of
-- leaving them as dead weight:
--
--   NERO_DB."02_CONTROL" -- used to hold the PROCESS_BATCH-era contract
--   registry, run audit, release pointer and MERGE work tables, all
--   retired when PROCESS_BATCH was replaced by dbt. All that was left was
--   an orphaned MERGE work table (STAGE_MERGE_TRANSACTIONS, zero code
--   references) and an unused schema. Reworked into a live contract-
--   enforcement view: how many staged keys per dataset didn't make it to
--   bronze.
--
--   NERO_DB."04_METADATA" -- comment promised "ingestion freshness/
--   reporting metadata" but held zero tables; the release-pointer
--   mechanism it was meant to support was retired with PROCESS_BATCH.
--   Reworked into a live freshness view: staging vs. bronze lag per
--   dataset.
--
-- Both are plain views over existing data -- no new ETL, always current.
-- Also wrapped into NERO_GOVERNANCE.REPORTING for Power BI (see
-- powerbi_governance_reporting.sql).
--
-- Apply with:
--   snow sql -c snowflake_ai_dev_kit -f account_setup/control_metadata_rework.sql
-- =============================================================================

USE ROLE ACCOUNTADMIN;

-- Orphaned PROCESS_BATCH-era work table -- dbt's native incremental
-- materialization (bronze_transactions.sql) replaced the MERGE this fed.
DROP TABLE IF EXISTS NERO_DB."02_CONTROL".STAGE_MERGE_TRANSACTIONS;

CREATE OR REPLACE VIEW NERO_DB."02_CONTROL".CONTRACT_REJECTIONS
COMMENT = 'Live contract-rejection rate per dataset: distinct keys landed in staging vs. published to bronze. Not batch-scoped -- a snapshot of current staging/bronze state, recomputed on every query.'
AS
WITH staging AS (
  SELECT 'STORES' AS DATASET, COUNT(DISTINCT STORE_ID) AS STAGED_KEYS FROM NERO_DB."00_STAGING".STAGING_STORES
  UNION ALL SELECT 'LOYALTY_CUSTOMERS', COUNT(DISTINCT CUSTOMER_ID) FROM NERO_DB."00_STAGING".STAGING_LOYALTY_CUSTOMERS
  UNION ALL SELECT 'TRANSACTIONS', COUNT(DISTINCT TRANSACTION_ID) FROM NERO_DB."00_STAGING".STAGING_TRANSACTIONS
  UNION ALL SELECT 'LOYALTY_EVENTS', COUNT(DISTINCT EVENT_ID) FROM NERO_DB."00_STAGING".STAGING_LOYALTY_EVENTS
),
bronze AS (
  SELECT 'STORES' AS DATASET, COUNT(*) AS PUBLISHED_KEYS FROM NERO_DB."01_BRONZE".BRONZE_STORES
  UNION ALL SELECT 'LOYALTY_CUSTOMERS', COUNT(*) FROM NERO_DB."01_BRONZE".BRONZE_LOYALTY_CUSTOMERS
  UNION ALL SELECT 'TRANSACTIONS', COUNT(*) FROM NERO_DB."01_BRONZE".BRONZE_TRANSACTIONS
  UNION ALL SELECT 'LOYALTY_EVENTS', COUNT(*) FROM NERO_DB."01_BRONZE".BRONZE_LOYALTY_EVENTS
)
SELECT s.DATASET, s.STAGED_KEYS, b.PUBLISHED_KEYS,
  GREATEST(s.STAGED_KEYS - b.PUBLISHED_KEYS, 0) AS REJECTED_KEYS,
  ROUND(DIV0(GREATEST(s.STAGED_KEYS - b.PUBLISHED_KEYS, 0), s.STAGED_KEYS) * 100, 2) AS REJECT_RATE_PCT
FROM staging s JOIN bronze b ON b.DATASET = s.DATASET
ORDER BY REJECT_RATE_PCT DESC;

CREATE OR REPLACE VIEW NERO_DB."04_METADATA".DATASET_FRESHNESS
COMMENT = 'Per-dataset landing freshness: latest row staged vs. latest row published to bronze, and the lag between them in minutes.'
AS
WITH staging AS (
  SELECT 'STORES' AS DATASET, MAX(INGESTED_AT) AS LATEST_STAGED_AT FROM NERO_DB."00_STAGING".STAGING_STORES
  UNION ALL SELECT 'LOYALTY_CUSTOMERS', MAX(INGESTED_AT) FROM NERO_DB."00_STAGING".STAGING_LOYALTY_CUSTOMERS
  UNION ALL SELECT 'TRANSACTIONS', MAX(INGESTED_AT) FROM NERO_DB."00_STAGING".STAGING_TRANSACTIONS
  UNION ALL SELECT 'LOYALTY_EVENTS', MAX(INGESTED_AT) FROM NERO_DB."00_STAGING".STAGING_LOYALTY_EVENTS
),
bronze AS (
  SELECT 'STORES' AS DATASET, MAX(INGESTED_AT) AS LATEST_PUBLISHED_AT FROM NERO_DB."01_BRONZE".BRONZE_STORES
  UNION ALL SELECT 'LOYALTY_CUSTOMERS', MAX(INGESTED_AT) FROM NERO_DB."01_BRONZE".BRONZE_LOYALTY_CUSTOMERS
  UNION ALL SELECT 'TRANSACTIONS', MAX(INGESTED_AT) FROM NERO_DB."01_BRONZE".BRONZE_TRANSACTIONS
  UNION ALL SELECT 'LOYALTY_EVENTS', MAX(INGESTED_AT) FROM NERO_DB."01_BRONZE".BRONZE_LOYALTY_EVENTS
)
SELECT s.DATASET, s.LATEST_STAGED_AT, b.LATEST_PUBLISHED_AT,
  DATEDIFF('minute', b.LATEST_PUBLISHED_AT, s.LATEST_STAGED_AT) AS BRONZE_LAG_MINUTES
FROM staging s JOIN bronze b ON b.DATASET = s.DATASET
ORDER BY BRONZE_LAG_MINUTES DESC;

GRANT SELECT ON VIEW NERO_DB."02_CONTROL".CONTRACT_REJECTIONS TO ROLE NERO_GOVERNANCE_ROLE;
GRANT USAGE ON SCHEMA NERO_DB."04_METADATA" TO ROLE NERO_GOVERNANCE_ROLE;
GRANT SELECT ON VIEW NERO_DB."04_METADATA".DATASET_FRESHNESS TO ROLE NERO_GOVERNANCE_ROLE;

-- dbt_dev is the configured `dbt build --target dev` schema (see
-- ~/.dbt/profiles.yml), but held a stale, pre-retirement dev run --
-- including STG_CURRENT_RELEASE, which read the now-dropped release
-- pointer. Dropped clean; dbt recreates it fresh on the next dev run.
DROP SCHEMA IF EXISTS NERO_ANALYTICS.dbt_dev CASCADE;

SELECT 'control_metadata_rework applied' AS STATUS;

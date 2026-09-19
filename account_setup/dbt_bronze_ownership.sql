-- =============================================================================
-- NERO_DBT_ROLE owns bronze, not just reads it.
--
-- Staging->bronze used to be PROCESS_BATCH (Python, owned by ACCOUNTADMIN in
-- NERO_DB."02_CONTROL"); it's now dbt models (analytics/models/bronze/),
-- which means NERO_DBT_ROLE needs to read NERO_DB."00_STAGING" (renamed
-- from "00_BRONZE" -- see analytics/README.md for the full Staging/Bronze/
-- Silver rename mapping) and CREATE (not just SELECT) in
-- NERO_DB."01_BRONZE" (renamed from "01_SILVER") -- dbt materializes
-- BRONZE_<DATASET> there directly (table for full datasets, incremental
-- for transactions).
--
-- Apply this AFTER the DCM deploy that empties sources/definitions/
-- ingestion/validated.sql (which drops the old, ACCOUNTADMIN-owned
-- VALIDATED_* tables) and BEFORE the first `dbt build` -- dbt's
-- CREATE OR REPLACE TABLE then creates fresh, NERO_DBT_ROLE-owned tables
-- with no ownership conflict, instead of fighting DCM for the same object.
--
-- Apply with: snow sql -f account_setup/dbt_bronze_ownership.sql
-- =============================================================================

USE ROLE ACCOUNTADMIN;

CREATE SCHEMA IF NOT EXISTS NERO_DB."01_BRONZE"
  COMMENT = 'Contract-filtered tables (BRONZE_<DATASET>), dbt-owned. Renamed from "01_SILVER"/VALIDATED_* -- see analytics/README.md.';

GRANT USAGE ON SCHEMA NERO_DB."00_STAGING" TO ROLE NERO_DBT_ROLE;
GRANT SELECT ON ALL TABLES IN SCHEMA NERO_DB."00_STAGING" TO ROLE NERO_DBT_ROLE;
GRANT SELECT ON FUTURE TABLES IN SCHEMA NERO_DB."00_STAGING" TO ROLE NERO_DBT_ROLE;

GRANT USAGE ON SCHEMA NERO_DB."01_BRONZE" TO ROLE NERO_DBT_ROLE;
GRANT CREATE TABLE ON SCHEMA NERO_DB."01_BRONZE" TO ROLE NERO_DBT_ROLE;

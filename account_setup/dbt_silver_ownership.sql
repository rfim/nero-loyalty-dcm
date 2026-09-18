-- =============================================================================
-- NERO_DBT_ROLE now owns silver, not just reads it.
--
-- Bronze->silver used to be PROCESS_BATCH (Python, owned by ACCOUNTADMIN in
-- NERO_DB."02_CONTROL"); it's now dbt models (analytics/models/silver/),
-- which means NERO_DBT_ROLE needs to read NERO_DB."00_BRONZE" (new) and
-- CREATE (not just SELECT) in NERO_DB."01_SILVER" (new) -- dbt materializes
-- VALIDATED_<DATASET> there directly (table for full datasets, incremental
-- for transactions).
--
-- Apply this AFTER the DCM deploy that empties sources/definitions/
-- ingestion/validated.sql (which drops the old, ACCOUNTADMIN-owned
-- VALIDATED_* tables) and BEFORE the first `dbt build` -- dbt's
-- CREATE OR REPLACE TABLE then creates fresh, NERO_DBT_ROLE-owned tables
-- with no ownership conflict, instead of fighting DCM for the same object.
--
-- Apply with: snow sql -f account_setup/dbt_silver_ownership.sql
-- =============================================================================

USE ROLE ACCOUNTADMIN;

GRANT USAGE ON SCHEMA NERO_DB."00_BRONZE" TO ROLE NERO_DBT_ROLE;
GRANT SELECT ON ALL TABLES IN SCHEMA NERO_DB."00_BRONZE" TO ROLE NERO_DBT_ROLE;
GRANT SELECT ON FUTURE TABLES IN SCHEMA NERO_DB."00_BRONZE" TO ROLE NERO_DBT_ROLE;

GRANT USAGE ON SCHEMA NERO_DB."01_SILVER" TO ROLE NERO_DBT_ROLE;
GRANT CREATE TABLE ON SCHEMA NERO_DB."01_SILVER" TO ROLE NERO_DBT_ROLE;

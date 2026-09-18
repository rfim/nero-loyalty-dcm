-- =============================================================================
-- Bronze-layer ground truth: a stage holding the *original, unmodified*
-- source files, as received -- separate from LANDING_STAGE, which only ever
-- holds the per-dataset CSVs load_csv_batch.py stages before COPY INTO
-- BRONZE_<DATASET>.
--
-- Until this file, nothing in Snowflake preserved the actual raw CSVs --
-- only the already-typed landing tables. A real medallion bronze layer
-- should keep the literal source bytes as an immutable audit record,
-- independent of whatever transformation the ingestion pipeline happens to
-- do on its way to BRONZE_<DATASET>. This stage is that record.
--
-- Not DCM-managed (raw.sql is generated from ingestion/contract/ and
-- shouldn't be hand-edited) -- this is a structural addition alongside the
-- contract, not part of it.
--
-- Apply with: snow sql -f account_setup/raw_source_files_stage.sql
-- =============================================================================

USE ROLE ACCOUNTADMIN;

CREATE STAGE IF NOT EXISTS NERO_DB."00_BRONZE".RAW_SOURCE_FILES
  COMMENT = 'Immutable ground truth: original, unmodified source files exactly as received, organized one subdirectory per batch_id. Never transformed, never overwritten -- the audit record LANDING_STAGE/BRONZE_<DATASET> do not provide, since those only ever hold the derived/typed landing form.';

GRANT READ, WRITE ON STAGE NERO_DB."00_BRONZE".RAW_SOURCE_FILES TO ROLE NERO_INGEST_ROLE;

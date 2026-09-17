-- =============================================================================
-- Data Contract Ingestion Pattern — Work (frozen batch)
--
-- VALIDATED_* tables and REPORTING_CURRENT_RELEASE are now GENERATED from
-- contracts/loyalty.yaml by tools/build.py — see
-- sources/definitions/contract_validated_tables.sql. This file only holds
-- the structural WORK_ENVELOPES table, which is dataset-agnostic and never
-- changes shape when a dataset is added.
-- =============================================================================

DEFINE TABLE NERO_DB.NERO_LOYALTY.WORK_ENVELOPES (
    RUN_ID       VARCHAR(200)  NOT NULL,
    BATCH_ID     VARCHAR(200)  NOT NULL,
    DOC          VARIANT       NOT NULL,
    FROZEN_AT    TIMESTAMP_TZ  DEFAULT CURRENT_TIMESTAMP()
)
COMMENT = 'Frozen snapshot of one batch''s raw envelopes for a single PROCESS_BATCH run. Written by one INSERT...SELECT so all downstream checks read the same fixed set. Cleared after normal runs.';

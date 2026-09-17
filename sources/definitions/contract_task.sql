-- =============================================================================
-- Data Contract Ingestion Pattern — Gate Task
-- Suspended by default (DCM default) — resume explicitly once smoke tests
-- pass. Snowflake auto-suspends after 3 consecutive failures.
-- =============================================================================

DEFINE TASK NERO_DB.NERO_LOYALTY.CONTRACT_GATE_TASK
    WAREHOUSE = 'COMPUTE_WH'
    SCHEDULE = '15 MINUTE'
    COMMENT = 'Calls RUN_PENDING to evaluate/publish outstanding manifested batches. Suspended until explicitly resumed post smoke-test.'
AS
    CALL NERO_DB.NERO_LOYALTY.RUN_PENDING();

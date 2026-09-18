-- =============================================================================
-- Readable, per-dataset bronze views over RAW_ENVELOPES.
--
-- RAW_ENVELOPES is one shared, DCM-generated table (sources/definitions/
-- ingestion/raw.sql) that every ingestion source -- CSV upload, the synthetic
-- daily generator, Google Sheets -- writes the same JSON envelope format
-- into, so PROCESS_BATCH (engine.sql) can validate any of them with one code
-- path. That's a deliberate, working design and this file does not change
-- it -- splitting it into real per-contract tables would mean rewriting the
-- generated engine for every source, not just one.
--
-- What actually made debugging hard was reading raw JSON by eye. These 4
-- views fix exactly that: one readable, typed table per data contract
-- (STORES, TRANSACTIONS, LOYALTY_EVENTS, LOYALTY_CUSTOMERS), unnesting
-- RAW_ENVELOPES' JSON PAYLOAD into real columns -- `SELECT * FROM
-- NERO_DB."00_BRONZE".STORES` instead of parsing JSON by hand. Since they sit
-- on top of RAW_ENVELOPES itself, they cover every batch from every source
-- (CSV, synthetic, Sheets), not just one -- and need zero maintenance when a
-- new source is added, because they only ever look at the columns the
-- already-pinned contract defines.
--
-- Apply with: snow sql -f account_setup/bronze_dataset_views.sql
-- =============================================================================

USE ROLE ACCOUNTADMIN;

CREATE OR REPLACE VIEW NERO_DB."00_BRONZE".STORES AS
SELECT
    r.FILE_CONTENT_KEY AS BATCH_ID,
    r.FILE_ROW_NUMBER,
    r.INGESTED_AT,
    j.value:values:store_id::NUMBER AS STORE_ID,
    j.value:values:store_name::STRING AS STORE_NAME,
    j.value:values:region::STRING AS REGION,
    j.value:values:format::STRING AS FORMAT,
    j.value:values:opened_date::DATE AS OPENED_DATE
FROM NERO_DB."00_BRONZE".RAW_ENVELOPES r,
     LATERAL (SELECT PARSE_JSON(r.PAYLOAD) AS value) j
WHERE j.value:type::STRING = 'record'
  AND j.value:dataset::STRING = 'stores';

CREATE OR REPLACE VIEW NERO_DB."00_BRONZE".TRANSACTIONS AS
SELECT
    r.FILE_CONTENT_KEY AS BATCH_ID,
    r.FILE_ROW_NUMBER,
    r.INGESTED_AT,
    j.value:values:transaction_id::NUMBER AS TRANSACTION_ID,
    j.value:values:store_id::NUMBER AS STORE_ID,
    j.value:values:transaction_ts::TIMESTAMP_TZ AS TRANSACTION_TS,
    j.value:values:customer_id::NUMBER AS CUSTOMER_ID,
    j.value:values:basket_total::NUMBER(10,2) AS BASKET_TOTAL,
    j.value:values:item_count::NUMBER AS ITEM_COUNT,
    j.value:values:payment_type::STRING AS PAYMENT_TYPE
FROM NERO_DB."00_BRONZE".RAW_ENVELOPES r,
     LATERAL (SELECT PARSE_JSON(r.PAYLOAD) AS value) j
WHERE j.value:type::STRING = 'record'
  AND j.value:dataset::STRING = 'transactions';

CREATE OR REPLACE VIEW NERO_DB."00_BRONZE".LOYALTY_EVENTS AS
SELECT
    r.FILE_CONTENT_KEY AS BATCH_ID,
    r.FILE_ROW_NUMBER,
    r.INGESTED_AT,
    j.value:values:event_id::NUMBER AS EVENT_ID,
    j.value:values:customer_id::NUMBER AS CUSTOMER_ID,
    j.value:values:event_ts::TIMESTAMP_TZ AS EVENT_TS,
    j.value:values:event_type::STRING AS EVENT_TYPE,
    j.value:values:reward_id::NUMBER AS REWARD_ID,
    j.value:values:store_id::NUMBER AS STORE_ID
FROM NERO_DB."00_BRONZE".RAW_ENVELOPES r,
     LATERAL (SELECT PARSE_JSON(r.PAYLOAD) AS value) j
WHERE j.value:type::STRING = 'record'
  AND j.value:dataset::STRING = 'loyalty_events';

CREATE OR REPLACE VIEW NERO_DB."00_BRONZE".LOYALTY_CUSTOMERS AS
SELECT
    r.FILE_CONTENT_KEY AS BATCH_ID,
    r.FILE_ROW_NUMBER,
    r.INGESTED_AT,
    j.value:values:customer_id::NUMBER AS CUSTOMER_ID,
    j.value:values:signup_date::DATE AS SIGNUP_DATE,
    j.value:values:home_store_id::NUMBER AS HOME_STORE_ID,
    j.value:values:tier::STRING AS TIER
FROM NERO_DB."00_BRONZE".RAW_ENVELOPES r,
     LATERAL (SELECT PARSE_JSON(r.PAYLOAD) AS value) j
WHERE j.value:type::STRING = 'record'
  AND j.value:dataset::STRING = 'loyalty_customers';

GRANT SELECT ON VIEW NERO_DB."00_BRONZE".STORES TO ROLE NERO_INGEST_ROLE;
GRANT SELECT ON VIEW NERO_DB."00_BRONZE".TRANSACTIONS TO ROLE NERO_INGEST_ROLE;
GRANT SELECT ON VIEW NERO_DB."00_BRONZE".LOYALTY_EVENTS TO ROLE NERO_INGEST_ROLE;
GRANT SELECT ON VIEW NERO_DB."00_BRONZE".LOYALTY_CUSTOMERS TO ROLE NERO_INGEST_ROLE;

GRANT SELECT ON VIEW NERO_DB."00_BRONZE".STORES TO ROLE NERO_GOVERNANCE_ROLE;
GRANT SELECT ON VIEW NERO_DB."00_BRONZE".TRANSACTIONS TO ROLE NERO_GOVERNANCE_ROLE;
GRANT SELECT ON VIEW NERO_DB."00_BRONZE".LOYALTY_EVENTS TO ROLE NERO_GOVERNANCE_ROLE;
GRANT SELECT ON VIEW NERO_DB."00_BRONZE".LOYALTY_CUSTOMERS TO ROLE NERO_GOVERNANCE_ROLE;

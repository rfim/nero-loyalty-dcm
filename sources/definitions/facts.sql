-- =============================================================================
-- Caffe Nero Loyalty Star Schema — Fact Tables
-- Database: NERO_DB  Schema: NERO_LOYALTY
-- =============================================================================

-- ---------------------------------------------------------------------------
-- FACT_TRANSACTIONS: Transaction fact table
-- Grain: one row per transaction_id
-- Measures: BASKET_TOTAL, ITEM_COUNT
-- FKs: DIM_STORE, DIM_CUSTOMER (nullable), DIM_DATE, DIM_TIME
-- Source: transactions
-- ---------------------------------------------------------------------------
DEFINE TABLE NERO_DB.NERO_LOYALTY.FACT_TRANSACTIONS (
    TRANSACTION_SK     NUMBER         NOT NULL AUTOINCREMENT,
    TRANSACTION_ID     NUMBER         NOT NULL,
    STORE_SK           NUMBER         NOT NULL,
    CUSTOMER_SK        NUMBER,
    DATE_SK            NUMBER         NOT NULL,
    TIME_SK            NUMBER         NOT NULL,
    BASKET_TOTAL       NUMBER(12,2),
    ITEM_COUNT         NUMBER,
    PAYMENT_TYPE       VARCHAR(50),
    TRANSACTION_TS     TIMESTAMP_NTZ  NOT NULL
)
COMMENT = 'Transaction fact table. Grain: one row per transaction_id. Measures: BASKET_TOTAL, ITEM_COUNT. FKs: STORE_SK->DIM_STORE, CUSTOMER_SK->DIM_CUSTOMER (nullable for non-loyalty txns), DATE_SK->DIM_DATE, TIME_SK->DIM_TIME. Source: transactions.';

-- ---------------------------------------------------------------------------
-- FACT_LOYALTY_EVENTS: Loyalty event fact table
-- Grain: one row per event_id
-- Degenerate dimension: EVENT_TYPE
-- FKs: DIM_CUSTOMER, DIM_STORE (nullable), DIM_REWARD (nullable), DIM_DATE
-- Source: loyalty_events
-- ---------------------------------------------------------------------------
DEFINE TABLE NERO_DB.NERO_LOYALTY.FACT_LOYALTY_EVENTS (
    LOYALTY_EVENT_SK   NUMBER         NOT NULL AUTOINCREMENT,
    EVENT_ID           NUMBER         NOT NULL,
    CUSTOMER_SK        NUMBER         NOT NULL,
    STORE_SK           NUMBER,
    REWARD_SK          NUMBER,
    DATE_SK            NUMBER         NOT NULL,
    EVENT_TYPE         VARCHAR(100)   NOT NULL,
    REWARD_ID          VARCHAR(100),
    EVENT_TS           TIMESTAMP_NTZ  NOT NULL
)
COMMENT = 'Loyalty event fact table. Grain: one row per event_id. Degenerate dim: EVENT_TYPE. FKs: CUSTOMER_SK->DIM_CUSTOMER, STORE_SK->DIM_STORE (nullable), REWARD_SK->DIM_REWARD (nullable), DATE_SK->DIM_DATE. Source: loyalty_events.';

-- =============================================================================
-- Data Contract Ingestion Pattern — Work (frozen batch) and Validated output
-- =============================================================================

DEFINE TABLE NERO_DB.NERO_LOYALTY.WORK_ENVELOPES (
    RUN_ID       VARCHAR(200)  NOT NULL,
    BATCH_ID     VARCHAR(200)  NOT NULL,
    DOC          VARIANT       NOT NULL,
    FROZEN_AT    TIMESTAMP_TZ  DEFAULT CURRENT_TIMESTAMP()
)
COMMENT = 'Frozen snapshot of one batch''s raw envelopes for a single PROCESS_BATCH run. Written by one INSERT...SELECT so all downstream checks read the same fixed set. Cleared after normal runs.';

-- ---------------------------------------------------------------------------
-- Validated, typed output tables — one per source dataset. These are the
-- inputs to the star schema (dim_store, dim_customer, fact_transactions,
-- fact_loyalty_events); loading the star schema from these is a follow-on
-- transform step, not built here.
-- ---------------------------------------------------------------------------
DEFINE TABLE NERO_DB.NERO_LOYALTY.VALIDATED_STORES (
    STORE_ID     NUMBER        NOT NULL,
    STORE_NAME   VARCHAR(200)  NOT NULL,
    REGION       VARCHAR(100)  NOT NULL,
    FORMAT       VARCHAR(100)  NOT NULL,
    OPENED_DATE  DATE          NOT NULL,
    BATCH_ID     VARCHAR(200)  NOT NULL,
    PRIMARY KEY (STORE_ID)
)
COMMENT = 'Validated stores dataset, current release only. Source: contract-validated snapshot delivery.';

DEFINE TABLE NERO_DB.NERO_LOYALTY.VALIDATED_LOYALTY_CUSTOMERS (
    CUSTOMER_ID    NUMBER        NOT NULL,
    SIGNUP_DATE    DATE          NOT NULL,
    TIER           VARCHAR(50)   NOT NULL,
    HOME_STORE_ID  NUMBER,
    BATCH_ID       VARCHAR(200)  NOT NULL,
    PRIMARY KEY (CUSTOMER_ID)
)
COMMENT = 'Validated loyalty_customers dataset, current release only.';

DEFINE TABLE NERO_DB.NERO_LOYALTY.VALIDATED_TRANSACTIONS (
    TRANSACTION_ID   NUMBER         NOT NULL,
    STORE_ID         NUMBER         NOT NULL,
    TRANSACTION_TS   TIMESTAMP_TZ   NOT NULL,
    CUSTOMER_ID      NUMBER,
    BASKET_TOTAL     NUMBER(8,2)    NOT NULL,
    ITEM_COUNT       NUMBER         NOT NULL,
    PAYMENT_TYPE     VARCHAR(50)    NOT NULL,
    BATCH_ID         VARCHAR(200)   NOT NULL,
    PRIMARY KEY (TRANSACTION_ID)
)
COMMENT = 'Validated transactions dataset, current release only.';

DEFINE TABLE NERO_DB.NERO_LOYALTY.VALIDATED_LOYALTY_EVENTS (
    EVENT_ID     NUMBER         NOT NULL,
    CUSTOMER_ID  NUMBER         NOT NULL,
    EVENT_TS     TIMESTAMP_TZ   NOT NULL,
    EVENT_TYPE   VARCHAR(100)   NOT NULL,
    REWARD_ID    NUMBER,
    STORE_ID     NUMBER,
    BATCH_ID     VARCHAR(200)   NOT NULL,
    PRIMARY KEY (EVENT_ID)
)
COMMENT = 'Validated loyalty_events dataset, current release only.';

DEFINE VIEW NERO_DB.NERO_LOYALTY.REPORTING_CURRENT_RELEASE AS
SELECT
    p.CURRENT_BATCH_ID,
    p.CURRENT_RELEASE_AT,
    (SELECT COUNT(*) FROM NERO_DB.NERO_LOYALTY.VALIDATED_STORES)             AS STORE_COUNT,
    (SELECT COUNT(*) FROM NERO_DB.NERO_LOYALTY.VALIDATED_LOYALTY_CUSTOMERS)  AS CUSTOMER_COUNT,
    (SELECT COUNT(*) FROM NERO_DB.NERO_LOYALTY.VALIDATED_TRANSACTIONS)       AS TRANSACTION_COUNT,
    (SELECT COUNT(*) FROM NERO_DB.NERO_LOYALTY.VALIDATED_LOYALTY_EVENTS)     AS EVENT_COUNT
FROM NERO_DB.NERO_LOYALTY.CONTROL_RELEASE_POINTER p
WHERE p.POINTER_NAME = 'LOYALTY_SNAPSHOT';

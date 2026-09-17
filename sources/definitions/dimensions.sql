-- =============================================================================
-- Caffe Nero Loyalty Star Schema — Dimension Tables
-- Database: NERO_DB  Schema: NERO_LOYALTY
-- =============================================================================

-- ---------------------------------------------------------------------------
-- DIM_DATE: Calendar date dimension
-- Grain: one row per calendar date
-- ---------------------------------------------------------------------------
DEFINE TABLE NERO_DB.NERO_LOYALTY.DIM_DATE (
    DATE_SK            NUMBER       NOT NULL,
    FULL_DATE          DATE         NOT NULL,
    YEAR               NUMBER       NOT NULL,
    QUARTER            NUMBER       NOT NULL,
    MONTH              NUMBER       NOT NULL,
    MONTH_NAME         VARCHAR(9)   NOT NULL,
    DAY_OF_MONTH       NUMBER       NOT NULL,
    DAY_OF_WEEK        NUMBER       NOT NULL,
    DAY_NAME           VARCHAR(9)   NOT NULL,
    WEEK_OF_YEAR       NUMBER       NOT NULL,
    IS_WEEKEND         BOOLEAN      NOT NULL
)
COMMENT = 'Date dimension. Grain: one row per calendar date. DATE_SK = YYYYMMDD integer surrogate key.';

-- ---------------------------------------------------------------------------
-- DIM_TIME: Time-of-day dimension (minute grain)
-- Grain: one row per minute of the day (1440 rows)
-- ---------------------------------------------------------------------------
DEFINE TABLE NERO_DB.NERO_LOYALTY.DIM_TIME (
    TIME_SK            NUMBER       NOT NULL,
    HOUR_24            NUMBER       NOT NULL,
    MINUTE             NUMBER       NOT NULL,
    PERIOD             VARCHAR(2)   NOT NULL
)
COMMENT = 'Time-of-day dimension. Grain: one row per minute (1440 rows). TIME_SK = HHMM integer surrogate key.';

-- ---------------------------------------------------------------------------
-- DIM_STORE: Store dimension (Type 1 SCD)
-- Grain: one row per store_id
-- Source: stores
-- ---------------------------------------------------------------------------
DEFINE TABLE NERO_DB.NERO_LOYALTY.DIM_STORE (
    STORE_SK           NUMBER       NOT NULL AUTOINCREMENT,
    STORE_ID           NUMBER       NOT NULL,
    STORE_NAME         VARCHAR(200),
    REGION             VARCHAR(100),
    FORMAT             VARCHAR(100),
    OPENED_DATE        DATE
)
COMMENT = 'Store dimension (Type 1 SCD). Grain: one row per store_id. Source: stores.';

-- ---------------------------------------------------------------------------
-- DIM_CUSTOMER: Customer dimension (Type 1 SCD, stable attributes only)
-- Grain: one row per customer_id
-- Source: loyalty_customers
-- ---------------------------------------------------------------------------
DEFINE TABLE NERO_DB.NERO_LOYALTY.DIM_CUSTOMER (
    CUSTOMER_SK        NUMBER       NOT NULL AUTOINCREMENT,
    CUSTOMER_ID        NUMBER       NOT NULL,
    SIGNUP_DATE        DATE,
    HOME_STORE_ID      NUMBER
)
COMMENT = 'Customer dimension (Type 1 SCD, stable attributes). Grain: one row per customer_id. Source: loyalty_customers. Tier tracked separately in DIM_CUSTOMER_TIER_HISTORY.';

-- ---------------------------------------------------------------------------
-- DIM_CUSTOMER_TIER_HISTORY: Mini-dimension / Type 2 SCD for customer tier
-- Grain: one row per customer_id + tier effective period
-- Source: loyalty_events (signup / tier_change events, conceptual)
-- ---------------------------------------------------------------------------
DEFINE TABLE NERO_DB.NERO_LOYALTY.DIM_CUSTOMER_TIER_HISTORY (
    CUSTOMER_TIER_SK   NUMBER         NOT NULL AUTOINCREMENT,
    CUSTOMER_ID        NUMBER         NOT NULL,
    TIER               VARCHAR(50)    NOT NULL,
    EFFECTIVE_FROM     TIMESTAMP_NTZ  NOT NULL,
    EFFECTIVE_TO       TIMESTAMP_NTZ,
    IS_CURRENT         BOOLEAN        NOT NULL DEFAULT FALSE
)
COMMENT = 'Type 2 mini-dimension tracking customer tier changes. Grain: one row per customer_id + tier effective period. Source: loyalty_events (signup/tier_change). IS_CURRENT=TRUE for active tier.';

-- ---------------------------------------------------------------------------
-- DIM_REWARD: Reward dimension (placeholder — no source table provided)
-- Grain: one row per reward_id
-- ---------------------------------------------------------------------------
DEFINE TABLE NERO_DB.NERO_LOYALTY.DIM_REWARD (
    REWARD_SK          NUMBER       NOT NULL AUTOINCREMENT,
    REWARD_ID          VARCHAR(100) NOT NULL,
    REWARD_NAME        VARCHAR(200),
    REWARD_TYPE        VARCHAR(100),
    POINTS_REQUIRED    NUMBER
)
COMMENT = 'This is Tweak Reward dimension (placeholder — no source table given). Grain: one row per reward_id. Populate when reward catalog becomes available.';

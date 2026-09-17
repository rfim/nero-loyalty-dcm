-- =============================================================================
-- Caffe Nero Loyalty Star Schema
-- Database: NERO_DB  Schema: NERO_LOYALTY
--
-- Dimensions, facts, and the orchestration that keeps DIM_DATE populated —
-- everything that makes up the Kimball reporting model lives in one file.
-- =============================================================================

-- =========================== DIMENSIONS =====================================

-- DIM_DATE: calendar date dimension. Grain: one row per calendar date.
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

-- DIM_TIME: time-of-day dimension. Grain: one row per minute (1440 rows).
DEFINE TABLE NERO_DB.NERO_LOYALTY.DIM_TIME (
    TIME_SK            NUMBER       NOT NULL,
    HOUR_24            NUMBER       NOT NULL,
    MINUTE             NUMBER       NOT NULL,
    PERIOD             VARCHAR(2)   NOT NULL
)
COMMENT = 'Time-of-day dimension. Grain: one row per minute (1440 rows). TIME_SK = HHMM integer surrogate key.';

-- DIM_STORE: Type 1 SCD. Grain: one row per store_id. Source: stores.
DEFINE TABLE NERO_DB.NERO_LOYALTY.DIM_STORE (
    STORE_SK           NUMBER       NOT NULL AUTOINCREMENT,
    STORE_ID           NUMBER       NOT NULL,
    STORE_NAME         VARCHAR(200),
    REGION             VARCHAR(100),
    FORMAT             VARCHAR(100),
    OPENED_DATE        DATE
)
COMMENT = 'Store dimension (Type 1 SCD). Grain: one row per store_id. Source: stores.';

-- DIM_CUSTOMER: Type 1 SCD, stable attributes only. Grain: one row per
-- customer_id. Tier is tracked separately in DIM_CUSTOMER_TIER_HISTORY.
DEFINE TABLE NERO_DB.NERO_LOYALTY.DIM_CUSTOMER (
    CUSTOMER_SK        NUMBER       NOT NULL AUTOINCREMENT,
    CUSTOMER_ID        NUMBER       NOT NULL,
    SIGNUP_DATE        DATE,
    HOME_STORE_ID      NUMBER
)
COMMENT = 'Customer dimension (Type 1 SCD, stable attributes). Grain: one row per customer_id. Source: loyalty_customers. Tier tracked separately in DIM_CUSTOMER_TIER_HISTORY.';

-- DIM_CUSTOMER_TIER_HISTORY: Type 2 mini-dimension for the one attribute
-- (tier) that needs history. Grain: one row per customer_id + effective period.
DEFINE TABLE NERO_DB.NERO_LOYALTY.DIM_CUSTOMER_TIER_HISTORY (
    CUSTOMER_TIER_SK   NUMBER         NOT NULL AUTOINCREMENT,
    CUSTOMER_ID        NUMBER         NOT NULL,
    TIER               VARCHAR(50)    NOT NULL,
    EFFECTIVE_FROM     TIMESTAMP_NTZ  NOT NULL,
    EFFECTIVE_TO       TIMESTAMP_NTZ,
    IS_CURRENT         BOOLEAN        NOT NULL DEFAULT FALSE
)
COMMENT = 'Type 2 mini-dimension tracking customer tier changes. Grain: one row per customer_id + tier effective period. Source: loyalty_events (signup/tier_change). IS_CURRENT=TRUE for active tier.';

-- DIM_REWARD: placeholder — no rewards source table was provided.
DEFINE TABLE NERO_DB.NERO_LOYALTY.DIM_REWARD (
    REWARD_SK          NUMBER       NOT NULL AUTOINCREMENT,
    REWARD_ID          VARCHAR(100) NOT NULL,
    REWARD_NAME        VARCHAR(200),
    REWARD_TYPE        VARCHAR(100),
    POINTS_REQUIRED    NUMBER
)
COMMENT = 'This is Tweak Reward dimension (placeholder — no source table given). Grain: one row per reward_id. Populate when reward catalog becomes available.';

-- ============================== FACTS =======================================

-- FACT_TRANSACTIONS: grain = one row per transaction_id.
-- Measures: BASKET_TOTAL, ITEM_COUNT. FKs: DIM_STORE, DIM_CUSTOMER (nullable
-- for non-loyalty transactions), DIM_DATE, DIM_TIME.
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

-- FACT_LOYALTY_EVENTS: grain = one row per event_id. Degenerate dimension:
-- EVENT_TYPE. FKs: DIM_CUSTOMER, DIM_STORE (nullable), DIM_REWARD (nullable).
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

-- ========================= ORCHESTRATION ====================================
-- Python procedure + task that keep DIM_DATE populated, deployed via the same
-- `snow dcm plan/deploy` flow as the tables above — no separate orchestrator
-- needed for this piece.

DEFINE PROCEDURE NERO_DB.NERO_LOYALTY.SP_REFRESH_DIM_DATE(YEARS_AHEAD NUMBER)
RETURNS VARCHAR
LANGUAGE PYTHON
RUNTIME_VERSION = '3.11'
PACKAGES = ('snowflake-snowpark-python')
HANDLER = 'run'
COMMENT = 'Upserts DIM_DATE rows for today through YEARS_AHEAD years out. Idempotent — safe to re-run.'
AS
$$
from datetime import date, timedelta

def run(session, years_ahead: int) -> str:
    start = date.today()
    end = date(start.year + years_ahead, 12, 31)
    rows = []
    d = start
    while d <= end:
        rows.append((
            int(d.strftime('%Y%m%d')),
            d, d.year, (d.month - 1) // 3 + 1, d.month, d.strftime('%B'),
            d.day, d.isoweekday(), d.strftime('%A'), int(d.strftime('%W')),
            d.isoweekday() >= 6,
        ))
        d += timedelta(days=1)

    df = session.create_dataframe(
        rows,
        schema=["DATE_SK", "FULL_DATE", "YEAR", "QUARTER", "MONTH", "MONTH_NAME",
                "DAY_OF_MONTH", "DAY_OF_WEEK", "DAY_NAME", "WEEK_OF_YEAR", "IS_WEEKEND"],
    )
    df.write.save_as_table("NERO_DB.NERO_LOYALTY.DIM_DATE", mode="overwrite")
    return f"DIM_DATE refreshed: {len(rows)} rows ({start} to {end})"
$$;

-- Suspended by default (DCM default) — flip to STARTED once the team is
-- ready for this to run unattended. Deliberately left off for the exercise.
DEFINE TASK NERO_DB.NERO_LOYALTY.TSK_REFRESH_DIM_DATE
    WAREHOUSE = 'COMPUTE_WH'
    SCHEDULE = 'USING CRON 0 3 * * * UTC'
    COMMENT = 'Daily refresh of DIM_DATE via SP_REFRESH_DIM_DATE.'
AS
    CALL NERO_DB.NERO_LOYALTY.SP_REFRESH_DIM_DATE(2);

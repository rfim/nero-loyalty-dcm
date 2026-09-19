-- =============================================================================
-- Synthetic daily data generator + schedule.
--
-- TEMPORARY, by design -- there is no real source system feeding this
-- account yet (no live POS/loyalty feed, no S3 auto-ingest configured).
-- This generates one new "day" of plausible loyalty/sales activity and
-- lands it into staging, purely so dashboards/chatbots have something
-- fresh to show daily. Meant to be swapped out for a real feed later --
-- everything here is self-contained in one procedure + two tasks, so
-- removing it later is just:
--   ALTER TASK NERO_DB."02_CONTROL".SYNTHETIC_DAILY_INGEST_TASK SUSPEND;
--   ALTER TASK NERO_ANALYTICS.DBT_PROJECT.DBT_DAILY_REFRESH_TASK SUSPEND;
--   DROP TASK/PROCEDURE ... (optional, once a real feed replaces this)
--
-- Not DCM-managed, unlike sources/definitions/ (see its own README) --
-- deliberately kept out of the core contract-managed pipeline since it's
-- expected to be temporary. Declared in the contract as source
-- 'synthetic_generator' (ingestion/contract/meta.yaml) so it is at least
-- named alongside every other source, even without a DCM-deployed
-- definition of its own -- see the contract's own note on why.
--
-- Lands into staging only -- validation/publish into bronze is now dbt's
-- job (analytics/models/bronze/), triggered by DBT_DAILY_REFRESH_TASK, not
-- called synchronously from here (see sources/definitions/README.md for
-- why PROCESS_BATCH was retired).
--
-- transactions lands incrementally (only today's new rows -- dbt's
-- bronze_transactions model MERGEs them in by transaction_id) --
-- transactions are append-only by nature (a POS transaction never mutates
-- once written), so this is the honest full picture, not a contrived
-- demo. stores/loyalty_customers/loyalty_events still resubmit their full
-- current state every run, same as before -- see
-- account_setup/google_sheets_ingest.sql for a source that has no natural
-- incremental signal (a full CSV export every time) and stays full-mode
-- throughout.
--
-- Apply with (as ACCOUNTADMIN, same as engine.sql's other 02_CONTROL objects):
--   snow sql -f account_setup/synthetic_daily_ingest.sql
-- =============================================================================

CREATE OR REPLACE PROCEDURE NERO_DB."02_CONTROL".GENERATE_SYNTHETIC_DAY()
RETURNS VARIANT
LANGUAGE PYTHON
RUNTIME_VERSION = '3.11'
PACKAGES = ('snowflake-snowpark-python')
HANDLER = 'run'
COMMENT = 'Generates one new synthetic day of stores/customers/transactions/loyalty_events on top of the current bronze snapshot and lands it into staging. dbt validates and publishes into bronze separately (see analytics/models/bronze/). transactions lands incrementally (today''s new rows only); everything else stays full. Temporary stand-in for a real data feed -- see account_setup/synthetic_daily_ingest.sql.'
AS
$$
import json
import random
from datetime import datetime, timedelta, timezone

from snowflake.snowpark.functions import col, lit, parse_json, seq8

CONTRACT_ID = "nero_loyalty_contract"
CONTRACT_VERSION = 7


def _land(session, dataset, rows, columns, batch_id):
    if not rows:
        return
    tuples = [tuple(list(r[c] for c in columns) + [batch_id, i]) for i, r in enumerate(rows, start=1)]
    df = session.create_dataframe(tuples, schema=[c.upper() for c in columns] + ["BATCH_ID", "FILE_ROW_NUMBER"])
    df.write.save_as_table(f'NERO_DB."00_STAGING".STAGING_{dataset.upper()}', mode="append", column_order="name")


def run(session):
    # captured_at comes from Snowflake's own CURRENT_TIMESTAMP(), not this
    # sandbox's local Python clock -- see the matching comment in
    # account_setup/google_sheets_ingest.sql for why (observed live clock
    # skew under EXTERNAL_ACCESS_INTEGRATIONS). This proc has no external
    # access integration itself, but keeping the source consistent across
    # every adapter is cheap insurance against the same class of bug.
    today = session.sql("SELECT CURRENT_TIMESTAMP()::DATE AS TODAY").collect()[0]["TODAY"]
    batch_id = f"synthetic_{today.strftime('%Y%m%d')}"

    already = session.sql(
        "SELECT 1 FROM NERO_DB.\"00_STAGING\".STAGING_BATCH_MANIFESTS WHERE BATCH_ID = ? LIMIT 1",
        params=[batch_id],
    ).collect()
    if already:
        return {"status": "ALREADY_LANDED_TODAY", "batch_id": batch_id}

    stores = session.sql('SELECT STORE_ID, STORE_NAME, REGION, FORMAT, OPENED_DATE FROM NERO_DB."01_BRONZE".BRONZE_STORES').collect()
    customers = session.sql('SELECT CUSTOMER_ID, SIGNUP_DATE, TIER, HOME_STORE_ID FROM NERO_DB."01_BRONZE".BRONZE_LOYALTY_CUSTOMERS').collect()
    max_txn_id = session.sql('SELECT MAX(TRANSACTION_ID) AS M FROM NERO_DB."01_BRONZE".BRONZE_TRANSACTIONS').collect()[0]["M"] or 9000000
    events = session.sql('SELECT EVENT_ID, CUSTOMER_ID, EVENT_TS, EVENT_TYPE, REWARD_ID, STORE_ID FROM NERO_DB."01_BRONZE".BRONZE_LOYALTY_EVENTS').collect()

    store_ids = [r["STORE_ID"] for r in stores]
    customer_ids = [r["CUSTOMER_ID"] for r in customers]
    max_event_id = max([r["EVENT_ID"] for r in events], default=900000)
    max_cust_id = max(customer_ids, default=0)

    def day_ts(hour_lo=6, hour_hi=21):
        return datetime(today.year, today.month, today.day, tzinfo=timezone.utc) + timedelta(
            hours=random.randint(hour_lo, hour_hi), minutes=random.randint(0, 59), seconds=random.randint(0, 59)
        )

    new_customers = []
    for _ in range(random.choices([0, 1, 2], weights=[70, 25, 5])[0]):
        max_cust_id += 1
        new_customers.append({
            "customer_id": max_cust_id, "signup_date": today,
            "tier": "Bronze", "home_store_id": random.choice(store_ids),
        })
    all_customer_ids_today = customer_ids + [c["customer_id"] for c in new_customers]

    new_transactions = []
    for _ in range(random.randint(30, 80)):
        max_txn_id += 1
        ts = day_ts()
        cust = random.choice(all_customer_ids_today) if random.random() > 0.15 else None
        new_transactions.append({
            "transaction_id": max_txn_id, "store_id": random.choice(store_ids),
            "transaction_ts": ts, "customer_id": cust,
            "basket_total": round(random.uniform(1.5, 25.0), 2),
            "item_count": random.randint(1, 5),
            "payment_type": random.choice(["Card", "App", "Cash"]),
        })

    new_events = []
    for c in new_customers:
        max_event_id += 1
        new_events.append({
            "event_id": max_event_id, "customer_id": c["customer_id"],
            "event_ts": day_ts(), "event_type": "signup",
            "reward_id": None, "store_id": c["home_store_id"],
        })
    for txn in new_transactions:
        if txn["customer_id"] is None:
            continue
        roll = random.random()
        if roll < 0.35:
            max_event_id += 1
            new_events.append({
                "event_id": max_event_id, "customer_id": txn["customer_id"],
                "event_ts": txn["transaction_ts"], "event_type": "earn",
                "reward_id": None, "store_id": txn["store_id"],
            })
        elif roll < 0.40:
            max_event_id += 1
            new_events.append({
                "event_id": max_event_id, "customer_id": txn["customer_id"],
                "event_ts": txn["transaction_ts"], "event_type": "redeem",
                "reward_id": random.randint(200, 299), "store_id": txn["store_id"],
            })

    all_stores = [{"store_id": r["STORE_ID"], "store_name": r["STORE_NAME"], "region": r["REGION"],
                    "format": r["FORMAT"], "opened_date": r["OPENED_DATE"]} for r in stores]
    all_customers = [{"customer_id": r["CUSTOMER_ID"], "signup_date": r["SIGNUP_DATE"],
                       "tier": r["TIER"], "home_store_id": r["HOME_STORE_ID"]} for r in customers] + new_customers
    all_events = [{"event_id": r["EVENT_ID"], "customer_id": r["CUSTOMER_ID"],
                    "event_ts": r["EVENT_TS"], "event_type": r["EVENT_TYPE"],
                    "reward_id": r["REWARD_ID"], "store_id": r["STORE_ID"]} for r in events] + new_events

    _land(session, "stores", all_stores, ["store_id", "store_name", "region", "format", "opened_date"], batch_id)
    _land(session, "loyalty_customers", all_customers, ["customer_id", "signup_date", "tier", "home_store_id"], batch_id)
    _land(session, "loyalty_events", all_events, ["event_id", "customer_id", "event_ts", "event_type", "reward_id", "store_id"], batch_id)
    _land(session, "transactions", new_transactions, ["transaction_id", "store_id", "transaction_ts", "customer_id", "basket_total", "item_count", "payment_type"], batch_id)

    captured_at = session.sql("SELECT CURRENT_TIMESTAMP() AS NOW").collect()[0]["NOW"]
    datasets_meta = {
        "stores": {"row_count": len(all_stores)},
        "loyalty_customers": {"row_count": len(all_customers)},
        "loyalty_events": {"row_count": len(all_events)},
        "transactions": {"row_count": len(new_transactions)},
    }

    # Audit trail only now -- dbt (analytics/models/silver/) reads
    # BRONZE_<DATASET> directly and validates/publishes into silver itself;
    # nothing consults this manifest to decide what to do.
    session.sql(
        "INSERT INTO NERO_DB.\"00_STAGING\".STAGING_BATCH_MANIFESTS "
        "(BATCH_ID, CONTRACT_ID, CONTRACT_VERSION, SOURCE_SYSTEM, CAPTURED_AT, DATASETS) "
        "SELECT ?, ?, ?, ?, ?, PARSE_JSON(?)",
        params=[batch_id, CONTRACT_ID, CONTRACT_VERSION, "synthetic_daily_generator",
                captured_at, json.dumps(datasets_meta)],
    ).collect()

    return {
        "batch_id": batch_id,
        "new_customers": len(new_customers),
        "new_transactions": len(new_transactions),
        "new_events": len(new_events),
    }
$$;

CREATE OR REPLACE TASK NERO_DB."02_CONTROL".SYNTHETIC_DAILY_INGEST_TASK
  WAREHOUSE = 'NERO_LOAD_WH'
  SCHEDULE = 'USING CRON 0 6 * * * UTC'
  COMMENT = 'Runs GENERATE_SYNTHETIC_DAY() once daily. Temporary stand-in for a real data feed -- safe to SUSPEND at any time. See account_setup/synthetic_daily_ingest.sql.'
AS
  CALL NERO_DB."02_CONTROL".GENERATE_SYNTHETIC_DAY();

ALTER TASK NERO_DB."02_CONTROL".SYNTHETIC_DAILY_INGEST_TASK RESUME;

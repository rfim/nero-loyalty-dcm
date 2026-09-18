-- =============================================================================
-- Synthetic daily data generator + schedule.
--
-- TEMPORARY, by design -- there is no real source system feeding this
-- account yet (no live POS/loyalty feed, no S3 auto-ingest configured).
-- This generates one new "day" of plausible loyalty/sales activity and
-- publishes it through the real DCM pipeline (same PROCESS_BATCH validation
-- every other load goes through), purely so dashboards/chatbots have
-- something fresh to show daily. Meant to be swapped out for a real feed
-- later -- everything here is self-contained in one procedure + two tasks,
-- so removing it later is just:
--   ALTER TASK NERO_DB."02_CONTROL".SYNTHETIC_DAILY_INGEST_TASK SUSPEND;
--   ALTER TASK NERO_ANALYTICS.DBT_PROJECT.DBT_DAILY_REFRESH_TASK SUSPEND;
--   DROP TASK/PROCEDURE ... (optional, once a real feed replaces this)
--
-- Not DCM-managed (unlike sources/definitions/ingestion/engine.sql) --
-- deliberately kept out of the core contract-managed pipeline since it's
-- expected to be temporary.
--
-- Because PROCESS_BATCH does a full DELETE+replace of each VALIDATED_*
-- table per batch (see engine.sql), this can't just insert "today's new
-- rows" -- it has to read the full current SILVER snapshot and re-publish
-- it plus the new day's activity, same as every other batch this pipeline
-- has ever taken.
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
COMMENT = 'Generates one new synthetic day of stores/customers/transactions/loyalty_events on top of the current published snapshot, and publishes it via PROCESS_BATCH. Temporary stand-in for a real data feed -- see account_setup/synthetic_daily_ingest.sql.'
AS
$$
import json
import random
from datetime import datetime, timedelta, timezone

from snowflake.snowpark.functions import col, lit, parse_json, seq8

CONTRACT_ID = "nero_loyalty_contract"
CONTRACT_VERSION = 4


def run(session):
    today = datetime.now(timezone.utc).date()
    batch_id = f"synthetic_{today.strftime('%Y%m%d')}"

    already = session.sql(
        "SELECT 1 FROM NERO_DB.\"02_CONTROL\".CONTROL_RUN_AUDIT WHERE BATCH_ID = ? AND STATUS = 'PUBLISHED' LIMIT 1",
        params=[batch_id],
    ).collect()
    if already:
        return {"status": "ALREADY_PUBLISHED_TODAY", "batch_id": batch_id}

    pinned = session.sql(
        "SELECT CONTRACT_HASH FROM NERO_DB.\"02_CONTROL\".CONTROL_DATA_CONTRACTS WHERE CONTRACT_ID = ? AND VERSION = ?",
        params=[CONTRACT_ID, CONTRACT_VERSION],
    ).collect()
    contract_hash = pinned[0]["CONTRACT_HASH"]

    stores = session.sql('SELECT STORE_ID, STORE_NAME, REGION, FORMAT, OPENED_DATE FROM NERO_DB."01_SILVER".VALIDATED_STORES').collect()
    customers = session.sql('SELECT CUSTOMER_ID, SIGNUP_DATE, TIER, HOME_STORE_ID FROM NERO_DB."01_SILVER".VALIDATED_LOYALTY_CUSTOMERS').collect()
    transactions = session.sql('SELECT TRANSACTION_ID, STORE_ID, TRANSACTION_TS, CUSTOMER_ID, BASKET_TOTAL, ITEM_COUNT, PAYMENT_TYPE FROM NERO_DB."01_SILVER".VALIDATED_TRANSACTIONS').collect()
    events = session.sql('SELECT EVENT_ID, CUSTOMER_ID, EVENT_TS, EVENT_TYPE, REWARD_ID, STORE_ID FROM NERO_DB."01_SILVER".VALIDATED_LOYALTY_EVENTS').collect()

    store_ids = [r["STORE_ID"] for r in stores]
    customer_ids = [r["CUSTOMER_ID"] for r in customers]
    max_txn_id = max([r["TRANSACTION_ID"] for r in transactions], default=9000000)
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
            "customer_id": max_cust_id, "signup_date": today.isoformat(),
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
            "transaction_ts": ts.isoformat(), "customer_id": cust,
            "basket_total": f"{round(random.uniform(1.5, 25.0), 2):.2f}",
            "item_count": random.randint(1, 5),
            "payment_type": random.choice(["Card", "App", "Cash"]),
        })

    new_events = []
    for c in new_customers:
        max_event_id += 1
        new_events.append({
            "event_id": max_event_id, "customer_id": c["customer_id"],
            "event_ts": day_ts().isoformat(), "event_type": "signup",
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
                    "format": r["FORMAT"], "opened_date": str(r["OPENED_DATE"])} for r in stores]
    all_customers = [{"customer_id": r["CUSTOMER_ID"], "signup_date": str(r["SIGNUP_DATE"]),
                       "tier": r["TIER"], "home_store_id": r["HOME_STORE_ID"]} for r in customers] + new_customers
    all_transactions = [{"transaction_id": r["TRANSACTION_ID"], "store_id": r["STORE_ID"],
                          "transaction_ts": r["TRANSACTION_TS"].isoformat(), "customer_id": r["CUSTOMER_ID"],
                          "basket_total": str(r["BASKET_TOTAL"]), "item_count": r["ITEM_COUNT"],
                          "payment_type": r["PAYMENT_TYPE"]} for r in transactions] + new_transactions
    all_events = [{"event_id": r["EVENT_ID"], "customer_id": r["CUSTOMER_ID"],
                    "event_ts": r["EVENT_TS"].isoformat(), "event_type": r["EVENT_TYPE"],
                    "reward_id": r["REWARD_ID"], "store_id": r["STORE_ID"]} for r in events] + new_events

    datasets = {"stores": all_stores, "loyalty_customers": all_customers,
                "transactions": all_transactions, "loyalty_events": all_events}

    captured_at = datetime.now(timezone.utc).isoformat()
    lines = [json.dumps({
        "type": "manifest", "batch_id": batch_id, "contract_id": CONTRACT_ID,
        "contract_version": CONTRACT_VERSION, "contract_hash": contract_hash,
        "source_system": "synthetic_daily_generator", "captured_at": captured_at,
        "datasets": {name: {"row_count": len(rows)} for name, rows in datasets.items()},
    }, default=str)]
    for name, rows in datasets.items():
        for i, values in enumerate(rows, start=1):
            lines.append(json.dumps({
                "type": "record", "batch_id": batch_id, "dataset": name,
                "row_number": i, "values": values, "metadata": {},
            }, default=str))

    df = session.create_dataframe([[l] for l in lines], schema=["PAYLOAD_STR"])
    df2 = df.select(
        parse_json(col("PAYLOAD_STR")).alias("PAYLOAD"),
        lit("synthetic_daily_generator").alias("FILE_NAME"),
        seq8().alias("FILE_ROW_NUMBER"),
        lit(batch_id).alias("FILE_CONTENT_KEY"),
    )
    df2.write.save_as_table('NERO_DB."00_BRONZE".RAW_ENVELOPES', mode="append", column_order="name")

    result = session.sql(
        "CALL NERO_DB.\"02_CONTROL\".PROCESS_BATCH(?)", params=[batch_id]
    ).collect()

    return {
        "batch_id": batch_id,
        "new_customers": len(new_customers),
        "new_transactions": len(new_transactions),
        "new_events": len(new_events),
        "process_batch_result": result[0][0],
    }
$$;

CREATE OR REPLACE TASK NERO_DB."02_CONTROL".SYNTHETIC_DAILY_INGEST_TASK
  WAREHOUSE = 'NERO_LOAD_WH'
  SCHEDULE = 'USING CRON 0 6 * * * UTC'
  COMMENT = 'Runs GENERATE_SYNTHETIC_DAY() once daily. Temporary stand-in for a real data feed -- safe to SUSPEND at any time. See account_setup/synthetic_daily_ingest.sql.'
AS
  CALL NERO_DB."02_CONTROL".GENERATE_SYNTHETIC_DAY();

ALTER TASK NERO_DB."02_CONTROL".SYNTHETIC_DAILY_INGEST_TASK RESUME;

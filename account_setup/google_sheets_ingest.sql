-- =============================================================================
-- Google Sheets ingestion: fetches all 4 source datasets from public Google
-- Sheets URLs and lands them through the *existing* pipeline, unchanged.
--
-- No data contract modification needed -- and this is a deliberate design
-- choice, not an oversight. The contract (ingestion/contract/) governs the
-- *business rules* of the data (column types, enums, FKs) -- those are
-- identical regardless of whether bytes arrive as an uploaded file or a
-- fetched URL. Baking "source: Google Sheets" into the same hashed,
-- versioned contract that governs business-rule validation would conflate
-- two different concerns and force a real contract re-pin (new hash, new
-- CONTRACT_VERSION, PROCESS_BATCH redeploy) for what is actually just a new
-- *delivery mechanism*. So this file only adds a new way to get bytes into
-- RAW_ENVELOPES -- the contract, PROCESS_BATCH, and everything downstream
-- are completely untouched.
--
-- Not true Snowpipe -- Google Sheets isn't S3/GCS/Azure Blob, so there's no
-- cloud event-notification integration Snowflake can subscribe to. This is
-- the honest alternative: a scheduled Task (automatic trigger) wrapping a
-- stored procedure that's also directly callable (manual trigger) --
-- functionally the same duality real Snowpipe offers (auto-ingest vs.
-- REST-triggered), just polling instead of event-driven.
--
-- Apply with: snow sql -f account_setup/google_sheets_ingest.sql
-- =============================================================================

USE ROLE ACCOUNTADMIN;

-- Redirects observed live: docs.google.com -> a wildcard
-- doc-XX-XX-sheets.googleusercontent.com host that varies per request, so
-- both need to be allowlisted or the redirect leg of the fetch fails.
CREATE NETWORK RULE IF NOT EXISTS NERO_DB."02_CONTROL".GOOGLE_SHEETS_NETWORK_RULE
  TYPE = HOST_PORT
  MODE = EGRESS
  VALUE_LIST = ('docs.google.com', '*.googleusercontent.com')
  COMMENT = 'Egress allowlist for fetching public Google Sheets CSV exports -- nothing else.';

CREATE EXTERNAL ACCESS INTEGRATION IF NOT EXISTS NERO_GOOGLE_SHEETS_ACCESS_INTEGRATION
  ALLOWED_NETWORK_RULES = (NERO_DB."02_CONTROL".GOOGLE_SHEETS_NETWORK_RULE)
  ENABLED = TRUE
  COMMENT = 'Lets INGEST_FROM_GOOGLE_SHEETS() fetch public Sheets CSV exports. Scoped to Google Sheets export hosts only.';

ALTER EXTERNAL ACCESS INTEGRATION NERO_GOOGLE_SHEETS_ACCESS_INTEGRATION
  SET ALLOWED_NETWORK_RULES = (NERO_DB."02_CONTROL".GOOGLE_SHEETS_NETWORK_RULE);

CREATE OR REPLACE PROCEDURE NERO_DB."02_CONTROL".INGEST_FROM_GOOGLE_SHEETS()
RETURNS VARIANT
LANGUAGE PYTHON
RUNTIME_VERSION = '3.11'
PACKAGES = ('snowflake-snowpark-python', 'tzdata', 'requests')
HANDLER = 'run'
EXTERNAL_ACCESS_INTEGRATIONS = (NERO_GOOGLE_SHEETS_ACCESS_INTEGRATION)
COMMENT = 'Fetches all 4 source datasets from public Google Sheets CSV exports and publishes them via PROCESS_BATCH, same contract validation as every other load. Callable directly (manual trigger) or via GOOGLE_SHEETS_INGEST_TASK (automatic trigger). See account_setup/google_sheets_ingest.sql.'
AS
$$
import csv
import io
import json
import requests
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from snowflake.snowpark.functions import col, lit, parse_json, seq8

CONTRACT_ID = "nero_loyalty_contract"
CONTRACT_VERSION = 4
LONDON = ZoneInfo("Europe/London")

SOURCES = {
    "stores": "https://docs.google.com/spreadsheets/d/1HT5vSoVp-SIU-3-W_og2LxSfStEWjRvy1mEHNdjlTpM/export?format=csv",
    "loyalty_customers": "https://docs.google.com/spreadsheets/d/e/2PACX-1vTgZnkCztREaCPDx4ah2CEcOmPtcpTzyPfO9fv13XPXY_fcLwZdXNuC7X7ydzVh8E-s1XtPMtPuvQ1R/pub?output=csv",
    "transactions": "https://docs.google.com/spreadsheets/d/1ZI9ceDcvdremwa_GJLtKB25vfqnKCAFDs0eaVWoWBz0/export?format=csv",
    "loyalty_events": "https://docs.google.com/spreadsheets/d/1pjipgssqwx5Ojg3CxEYK1ACU6EpGz9TyX0tFVJp3P0U/export?format=csv",
}

# Mirrors ingestion/contract/datasets/*.yaml exactly. Hardcoded rather than
# read from the contract files, same as GENERATE_SYNTHETIC_DAY() -- Snowflake
# stored procs can't import local repo files, only the CONTRACT_HASH itself
# is read live (below), so a real contract change would still be caught by
# PROCESS_BATCH's hash check even though the column shapes here are fixed.
COLUMN_TYPES = {
    "stores": {"store_id": "integer", "store_name": "string", "region": "string", "format": "string", "opened_date": "date"},
    "loyalty_customers": {"customer_id": "integer", "signup_date": "date", "home_store_id": "integer", "tier": "string"},
    "transactions": {"transaction_id": "integer", "store_id": "integer", "transaction_ts": "timestamp_tz", "customer_id": "integer", "basket_total": "decimal", "item_count": "integer", "payment_type": "string"},
    "loyalty_events": {"event_id": "integer", "customer_id": "integer", "event_ts": "timestamp_tz", "event_type": "string", "reward_id": "integer", "store_id": "integer"},
}


def coerce_value(raw, col_type):
    if raw is None or raw == "":
        return None
    if col_type == "integer":
        return int(raw)
    if col_type in ("decimal", "date"):
        return raw
    if col_type == "timestamp_tz":
        naive = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
        return naive.replace(tzinfo=LONDON).isoformat()
    return raw


def fetch_csv_rows(url, columns):
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    reader = csv.DictReader(io.StringIO(resp.text))
    rows = []
    for row in reader:
        values = {c: coerce_value(row.get(c, ""), t) for c, t in columns.items()}
        rows.append(values)
    return rows


def run(session):
    today = datetime.now(timezone.utc).date()
    batch_id = f"google_sheets_{today.strftime('%Y%m%d')}"
    captured_at = datetime.now(timezone.utc).isoformat()

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

    datasets = {name: fetch_csv_rows(url, COLUMN_TYPES[name]) for name, url in SOURCES.items()}

    lines = [json.dumps({
        "type": "manifest", "batch_id": batch_id, "contract_id": CONTRACT_ID,
        "contract_version": CONTRACT_VERSION, "contract_hash": contract_hash,
        "source_system": "google_sheets", "captured_at": captured_at,
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
        lit("google_sheets_ingest").alias("FILE_NAME"),
        seq8().alias("FILE_ROW_NUMBER"),
        lit(batch_id).alias("FILE_CONTENT_KEY"),
    )
    df2.write.save_as_table('NERO_DB."00_BRONZE".RAW_ENVELOPES', mode="append", column_order="name")

    result = session.sql(
        "CALL NERO_DB.\"02_CONTROL\".PROCESS_BATCH(?)", params=[batch_id]
    ).collect()

    return {
        "batch_id": batch_id,
        "row_counts": {name: len(rows) for name, rows in datasets.items()},
        "process_batch_result": result[0][0],
    }
$$;

-- Automatic trigger: daily, matching the cadence every other feed in this
-- pipeline uses. Manual trigger is just CALL NERO_DB."02_CONTROL".INGEST_FROM_GOOGLE_SHEETS()
-- directly -- the same procedure serves both, same duality real Snowpipe
-- offers between auto-ingest and REST-triggered runs.
CREATE OR REPLACE TASK NERO_DB."02_CONTROL".GOOGLE_SHEETS_INGEST_TASK
  WAREHOUSE = 'NERO_LOAD_WH'
  SCHEDULE = 'USING CRON 30 6 * * * UTC'
  COMMENT = 'Automatic trigger for INGEST_FROM_GOOGLE_SHEETS(). Manual trigger: CALL the procedure directly. See account_setup/google_sheets_ingest.sql.'
AS
  CALL NERO_DB."02_CONTROL".INGEST_FROM_GOOGLE_SHEETS();

ALTER TASK NERO_DB."02_CONTROL".GOOGLE_SHEETS_INGEST_TASK RESUME;

-- Let the ingestion role trigger this manually too, not just ACCOUNTADMIN.
GRANT USAGE ON INTEGRATION NERO_GOOGLE_SHEETS_ACCESS_INTEGRATION TO ROLE NERO_INGEST_ROLE;
GRANT USAGE ON PROCEDURE NERO_DB."02_CONTROL".INGEST_FROM_GOOGLE_SHEETS() TO ROLE NERO_INGEST_ROLE;

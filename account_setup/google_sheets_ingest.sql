-- =============================================================================
-- Google Sheets ingestion: fetches all 4 source datasets from public Google
-- Sheets URLs and lands them through the *existing* pipeline, unchanged.
--
-- All 4 datasets stay full-mode: a Google Sheets CSV export has no concept
-- of "just the changed rows" -- every fetch returns the whole sheet, so
-- there's no watermark to track here. (Contrast with the synthetic daily
-- generator's transactions feed, which genuinely is incremental -- see
-- account_setup/synthetic_daily_ingest.sql.)
--
-- Lands into the typed BRONZE_<DATASET> tables (sources/definitions/
-- ingestion/raw.sql) + one BRONZE_BATCH_MANIFESTS row, then calls the
-- existing, unmodified PROCESS_BATCH -- same contract validation as every
-- other load.
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
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

CONTRACT_ID = "nero_loyalty_contract"
CONTRACT_VERSION = 5
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
    if col_type == "decimal":
        return float(raw)
    if col_type == "date":
        return date.fromisoformat(raw)
    if col_type == "timestamp_tz":
        naive = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
        return naive.replace(tzinfo=LONDON)
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


def _land(session, dataset, rows, columns, batch_id):
    if not rows:
        return
    tuples = [tuple(list(r[c] for c in columns) + [batch_id, i]) for i, r in enumerate(rows, start=1)]
    df = session.create_dataframe(tuples, schema=[c.upper() for c in columns] + ["BATCH_ID", "FILE_ROW_NUMBER"])
    df.write.save_as_table(f'NERO_DB."00_BRONZE".BRONZE_{dataset.upper()}', mode="append", column_order="name")


def run(session):
    # captured_at comes from Snowflake's own CURRENT_TIMESTAMP(), not this
    # sandbox's local Python clock -- observed live to run ~14 hours ahead
    # of Snowflake's clock under EXTERNAL_ACCESS_INTEGRATIONS, which would
    # otherwise permanently win the release-pointer's "is this batch newer"
    # gate against every correctly-clocked adapter until real time catches
    # up. The gate compares against Snowflake's clock, so captured_at has
    # to come from that same clock to never drift relative to it.
    now_row = session.sql("SELECT CURRENT_TIMESTAMP() AS NOW").collect()[0]
    captured_at = now_row["NOW"]
    today = captured_at.date()
    batch_id = f"google_sheets_{today.strftime('%Y%m%d')}"

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

    for name, rows in datasets.items():
        _land(session, name, rows, list(COLUMN_TYPES[name].keys()), batch_id)

    datasets_meta = {name: {"row_count": len(rows)} for name, rows in datasets.items()}
    session.sql(
        "INSERT INTO NERO_DB.\"00_BRONZE\".BRONZE_BATCH_MANIFESTS "
        "(BATCH_ID, CONTRACT_ID, CONTRACT_VERSION, CONTRACT_HASH, SOURCE_SYSTEM, CAPTURED_AT, DATASETS) "
        "SELECT ?, ?, ?, ?, ?, ?, PARSE_JSON(?)",
        params=[batch_id, CONTRACT_ID, CONTRACT_VERSION, contract_hash, "google_sheets",
                captured_at, json.dumps(datasets_meta)],
    ).collect()

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

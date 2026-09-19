#!/usr/bin/env python3
"""One-off (but reusable) loader: converts the original 4 source CSVs
(stores.csv, loyalty_customers.csv, transactions.csv, loyalty_events.csv)
into one batch and lands each dataset directly into its typed
STAGING_<DATASET> table plus one STAGING_BATCH_MANIFESTS row (audit trail
only). Validation/publish into bronze is dbt's job now (analytics/models/
bronze/) -- see sources/definitions/README.md for why
PROCESS_BATCH was retired.

Naive timestamps in transactions.csv/loyalty_events.csv are localized to
Europe/London (the plan's own recommendation) before landing -- staging
columns are TIMESTAMP_TZ, so this has to happen here, at the point values
are cast to their target type, same as before.

Values are staged locally as one CSV per dataset and landed via COPY INTO
STAGING_<DATASET> (through LANDING_STAGE, same credential-free internal
stage as always) -- not row-by-row INSERTs, so this scales the same way
the original JSONL-based loader did for the full 49k-row transactions file.

Usage: python ingestion/load_csv_batch.py --csv-dir <dir> -c <connection> [--batch-id <id>]
"""
import argparse
import csv
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from contract_loader import load_contract

LONDON = ZoneInfo("Europe/London")

DATASET_FILES = {
    "stores": "stores.csv",
    "loyalty_customers": "loyalty_customers.csv",
    "transactions": "transactions.csv",
    "loyalty_events": "loyalty_events.csv",
}


def coerce_value(raw, col_type):
    if raw == "" or raw is None:
        return None
    if col_type == "integer":
        return int(raw)
    if col_type == "decimal":
        return float(raw)
    if col_type == "date":
        return raw  # already YYYY-MM-DD, COPY INTO's DATE_FORMAT=AUTO parses it
    if col_type == "timestamp_tz":
        naive = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
        return naive.replace(tzinfo=LONDON).isoformat()
    return raw


def read_dataset(csv_dir, ds_name, ds_spec):
    path = Path(csv_dir) / DATASET_FILES[ds_name]
    rows = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            values = {
                col: coerce_value(row.get(col, ""), spec["type"])
                for col, spec in ds_spec["columns"].items()
            }
            rows.append(values)
    return rows


def write_staged_csv(rows, columns, batch_id, out_path):
    with open(out_path, "w", newline="") as f:
        writer = csv.writer(f)
        for i, values in enumerate(rows, start=1):
            row = [values.get(c) if values.get(c) is not None else "" for c in columns]
            row += [batch_id, i]
            writer.writerow(row)


def land_dataset(connection, ds_name, ds_spec, rows, batch_id, tmp_dir):
    if not rows:
        return
    columns = list(ds_spec["columns"].keys())
    local_path = tmp_dir / f"{batch_id}_{ds_name}.csv"
    write_staged_csv(rows, columns, batch_id, local_path)

    subprocess.run(["snow", "stage", "copy", str(local_path),
                     '@NERO_DB."00_STAGING".LANDING_STAGE', "--overwrite", "-c", connection], check=True)

    col_list = ", ".join(c.upper() for c in columns) + ", BATCH_ID, FILE_ROW_NUMBER"
    table = f'NERO_DB."00_STAGING".STAGING_{ds_name.upper()}'
    copy_sql = f"""
    COPY INTO {table} ({col_list})
    FROM @NERO_DB."00_STAGING".LANDING_STAGE
    FILES = ('{local_path.name}')
    FILE_FORMAT = (TYPE = CSV EMPTY_FIELD_AS_NULL = TRUE)
    ON_ERROR = 'ABORT_STATEMENT';
    """
    subprocess.run(["snow", "sql", "-c", connection, "-q", copy_sql], check=True)


def insert_manifest(connection, batch_id, contract, dataset_rows, captured_at):
    datasets_json = json.dumps({name: {"row_count": len(rows)} for name, rows in dataset_rows.items()})
    sql = f"""
    INSERT INTO NERO_DB."00_STAGING".STAGING_BATCH_MANIFESTS
        (BATCH_ID, CONTRACT_ID, CONTRACT_VERSION, SOURCE_SYSTEM, CAPTURED_AT, DATASETS)
    SELECT '{batch_id}', '{contract["contract_id"]}', {contract["version"]},
        'csv_archive_upload', '{captured_at}', PARSE_JSON($${datasets_json}$$);
    """
    subprocess.run(["snow", "sql", "-c", connection, "-q", sql], check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv-dir", required=True, help="directory containing the 4 source CSVs")
    parser.add_argument("-c", "--connection", required=True)
    parser.add_argument("--batch-id", default=None)
    args = parser.parse_args()

    doc, _ = load_contract()
    batch_id = args.batch_id or f"csv_load_{int(time.time())}"
    # From Snowflake's own clock, not this machine's -- a stored proc under
    # EXTERNAL_ACCESS_INTEGRATIONS was observed live running ~14 hours ahead
    # of it (see account_setup/google_sheets_ingest.sql). Nothing gates on
    # this timestamp anymore, but keeping every adapter's captured_at
    # sourced the same way is cheap insurance against that bug recurring.
    captured_at_result = subprocess.run(
        ["snow", "sql", "-c", args.connection, "--format", "JSON", "-q", "SELECT CURRENT_TIMESTAMP() AS NOW;"],
        check=True, capture_output=True, text=True,
    )
    captured_at = json.loads(captured_at_result.stdout)[0]["NOW"]

    dataset_rows = {
        ds_name: read_dataset(args.csv_dir, ds_name, ds_spec)
        for ds_name, ds_spec in doc["datasets"].items()
    }

    tmp_dir = Path("/tmp")
    for ds_name, ds_spec in doc["datasets"].items():
        land_dataset(args.connection, ds_name, ds_spec, dataset_rows[ds_name], batch_id, tmp_dir)
        print(f"{ds_name}: {len(dataset_rows[ds_name])} rows landed")

    insert_manifest(args.connection, batch_id, doc, dataset_rows, captured_at)
    print(f"Landed batch {batch_id} into staging. Run `dbt build` (or wait for "
          f"DBT_DAILY_REFRESH_TASK) to validate and publish into bronze.")


if __name__ == "__main__":
    main()

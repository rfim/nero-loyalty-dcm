#!/usr/bin/env python3
"""One-off (but reusable) loader: converts the original 4 source CSVs
(stores.csv, loyalty_customers.csv, transactions.csv, loyalty_events.csv)
into one nero_snapshot_v2 batch, uploads it, and calls PROCESS_BATCH.

Naive timestamps in transactions.csv/loyalty_events.csv are localized to
Europe/London (the plan's own recommendation) before being emitted as
TIMESTAMP_TZ-parseable ISO strings — PROCESS_BATCH's generic parser has
no concept of "assume local time for naive timestamps", so that
localization has to happen here, at the point the wire-format envelope
is built.

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


def envelope(**kwargs):
    return json.dumps(kwargs, default=str)


def coerce_value(raw, col_type):
    if raw == "" or raw is None:
        return None
    if col_type == "integer":
        return int(raw)
    if col_type == "decimal":
        return raw  # keep as string, contract parser handles decimal strings
    if col_type in ("date",):
        return raw
    if col_type == "timestamp_tz":
        naive = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
        localized = naive.replace(tzinfo=LONDON)
        return localized.isoformat()
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv-dir", required=True, help="directory containing the 4 source CSVs")
    parser.add_argument("-c", "--connection", required=True)
    parser.add_argument("--batch-id", default=None)
    args = parser.parse_args()

    doc, contract_hash = load_contract()
    batch_id = args.batch_id or f"csv_load_{int(time.time())}"
    captured_at = datetime.now(timezone.utc).isoformat()

    dataset_rows = {
        ds_name: read_dataset(args.csv_dir, ds_name, ds_spec)
        for ds_name, ds_spec in doc["datasets"].items()
    }

    lines = [envelope(
        type="manifest", batch_id=batch_id,
        contract_id=doc["contract_id"], contract_version=doc["version"],
        contract_hash=contract_hash, source_system="csv_archive_upload",
        captured_at=captured_at,
        datasets={name: {"row_count": len(rows)} for name, rows in dataset_rows.items()},
    )]
    for ds_name, rows in dataset_rows.items():
        for i, values in enumerate(rows, start=1):
            lines.append(envelope(
                type="record", batch_id=batch_id, dataset=ds_name,
                row_number=i, values=values, metadata={},
            ))
        print(f"{ds_name}: {len(rows)} rows")

    local_path = Path(f"/tmp/{batch_id}.jsonl")
    local_path.write_text("\n".join(lines) + "\n")
    print(f"Wrote {len(lines)} lines ({local_path.stat().st_size:,} bytes) to {local_path}")

    subprocess.run(["snow", "stage", "copy", str(local_path),
                     '@NERO_DB."00_BRONZE".LANDING_STAGE', "--overwrite", "-c", args.connection], check=True)

    copy_sql = f"""
    COPY INTO NERO_DB."00_BRONZE".RAW_ENVELOPES (PAYLOAD, FILE_NAME, FILE_ROW_NUMBER, FILE_CONTENT_KEY)
    FROM (
        SELECT TO_JSON($1), METADATA$FILENAME, METADATA$FILE_ROW_NUMBER, METADATA$FILE_CONTENT_KEY
        FROM @NERO_DB."00_BRONZE".LANDING_STAGE
    )
    FILES = ('{batch_id}.jsonl')
    FILE_FORMAT = (FORMAT_NAME = NERO_DB."00_BRONZE".JSON_LINES)
    ON_ERROR = 'ABORT_STATEMENT';
    """
    subprocess.run(["snow", "sql", "-c", args.connection, "-q", copy_sql], check=True)

    call_sql = f'CALL NERO_DB."02_CONTROL".PROCESS_BATCH(\'{batch_id}\');'
    result = subprocess.run(["snow", "sql", "-c", args.connection, "--format", "JSON", "-q", call_sql],
                             check=True, capture_output=True, text=True)
    print(result.stdout)


if __name__ == "__main__":
    main()

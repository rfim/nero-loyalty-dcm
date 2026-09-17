#!/usr/bin/env python3
"""Generates 3 synthetic batches (PUBLISHED, REJECTED, INCOMPLETE), uploads each
to LANDING_STAGE, loads RAW_ENVELOPES, calls PROCESS_BATCH, and prints outcomes.

Scoped down from the source guide's 6-scenario suite to 3 representative
outcomes. Fixtures are synthetic, not derived from the 90-day sample data.
Usage: python tools/run_smoke_test.py -c <snow_connection_name>
"""
import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

CONTRACT_PATH = Path(__file__).resolve().parent.parent / "contracts" / "loyalty.json"
CONTRACT = json.loads(CONTRACT_PATH.read_text())
CONTRACT_HASH = hashlib.sha256(CONTRACT_PATH.read_bytes()).hexdigest()


def envelope(**kwargs):
    return json.dumps(kwargs)


def manifest(batch_id, dataset_counts, captured_at):
    return envelope(
        type="manifest", batch_id=batch_id,
        contract_id=CONTRACT["contract_id"], contract_version=CONTRACT["version"],
        contract_hash=CONTRACT_HASH, source_system="smoke-test",
        captured_at=captured_at,
        datasets={k: {"row_count": v} for k, v in dataset_counts.items()},
    )


def record(batch_id, dataset, row_number, values):
    return envelope(type="record", batch_id=batch_id, dataset=dataset,
                     row_number=row_number, values=values, metadata={})


def build_valid_batch(batch_id, captured_at):
    lines = [manifest(batch_id, {"stores": 1, "loyalty_customers": 1, "transactions": 1, "loyalty_events": 1}, captured_at)]
    lines.append(record(batch_id, "stores", 1, {
        "store_id": 1042, "store_name": "Manchester Deansgate", "region": "North West",
        "format": "High street", "opened_date": "2019-03-11"}))
    lines.append(record(batch_id, "loyalty_customers", 1, {
        "customer_id": 55291, "signup_date": "2026-05-02", "tier": "Bronze", "home_store_id": 1042}))
    lines.append(record(batch_id, "transactions", 1, {
        "transaction_id": 8834021, "store_id": 1042, "transaction_ts": "2026-06-14T08:12:03+01:00",
        "customer_id": 55291, "basket_total": "6.85", "item_count": 2, "payment_type": "Card"}))
    lines.append(record(batch_id, "loyalty_events", 1, {
        "event_id": 990233, "customer_id": 55291, "event_ts": "2026-05-20T09:41:10+01:00",
        "event_type": "redeem", "reward_id": 204, "store_id": 1042}))
    return lines


def build_rejected_batch(batch_id, captured_at):
    """Duplicate primary key in stores -> REJECTED."""
    lines = [manifest(batch_id, {"stores": 2, "loyalty_customers": 0, "transactions": 0, "loyalty_events": 0}, captured_at)]
    for row_number in (1, 2):
        lines.append(record(batch_id, "stores", row_number, {
            "store_id": 1042, "store_name": "Manchester Deansgate", "region": "North West",
            "format": "High street", "opened_date": "2019-03-11"}))
    return lines


def build_incomplete_batch(batch_id, captured_at):
    """Manifest declares 2 store rows, only 1 delivered -> INCOMPLETE."""
    lines = [manifest(batch_id, {"stores": 2, "loyalty_customers": 0, "transactions": 0, "loyalty_events": 0}, captured_at)]
    lines.append(record(batch_id, "stores", 1, {
        "store_id": 1043, "store_name": "Leeds Briggate", "region": "Yorkshire",
        "format": "High street", "opened_date": "2020-01-15"}))
    return lines


def upload_and_process(connection, batch_id, lines):
    local_path = Path(f"/tmp/{batch_id}.jsonl")
    local_path.write_text("\n".join(lines) + "\n")

    subprocess.run(["snow", "stage", "copy", str(local_path),
                     "@NERO_DB.NERO_LOYALTY.LANDING_STAGE", "--overwrite", "-c", connection], check=True)

    copy_sql = f"""
    COPY INTO NERO_DB.NERO_LOYALTY.RAW_ENVELOPES (PAYLOAD, FILE_NAME, FILE_ROW_NUMBER, FILE_CONTENT_KEY)
    FROM (
        SELECT TO_JSON($1), METADATA$FILENAME, METADATA$FILE_ROW_NUMBER, METADATA$FILE_CONTENT_KEY
        FROM @NERO_DB.NERO_LOYALTY.LANDING_STAGE
    )
    FILES = ('{batch_id}.jsonl')
    FILE_FORMAT = (FORMAT_NAME = NERO_DB.NERO_LOYALTY.JSON_LINES)
    ON_ERROR = 'ABORT_STATEMENT';
    """
    subprocess.run(["snow", "sql", "-c", connection, "-q", copy_sql], check=True)

    call_sql = f"CALL NERO_DB.NERO_LOYALTY.PROCESS_BATCH('{batch_id}');"
    result = subprocess.run(["snow", "sql", "-c", connection, "--format", "JSON", "-q", call_sql],
                             check=True, capture_output=True, text=True)
    return result.stdout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-c", "--connection", required=True)
    args = parser.parse_args()

    ts = int(time.time())
    scenarios = [
        ("PUBLISHED (expected)", f"smoke_valid_{ts}", build_valid_batch),
        ("REJECTED (expected)", f"smoke_dup_pk_{ts}", build_rejected_batch),
        ("INCOMPLETE (expected)", f"smoke_incomplete_{ts}", build_incomplete_batch),
    ]

    for label, batch_id, builder in scenarios:
        captured_at = datetime.now(timezone.utc).isoformat()
        lines = builder(batch_id, captured_at)
        print(f"\n=== {label}: {batch_id} ===")
        output = upload_and_process(args.connection, batch_id, lines)
        print(output)


if __name__ == "__main__":
    main()

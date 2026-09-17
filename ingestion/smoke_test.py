#!/usr/bin/env python3
"""Generates 3 synthetic batches (PUBLISHED, REJECTED, INCOMPLETE), uploads each
to LANDING_STAGE, loads RAW_ENVELOPES, calls PROCESS_BATCH, and prints outcomes.

The PUBLISHED (valid) batch is generated automatically from each dataset's
`example` column values (ingestion/contract/datasets/*.yaml) — adding a
dataset to the contract automatically extends this scenario with no code
change here. REJECTED/INCOMPLETE stay hand-crafted since they represent
deliberately broken data.

Scoped down from the source guide's 6-scenario suite to 3 representative
outcomes. Fixtures are synthetic, not derived from the 90-day sample data.
Usage: python ingestion/smoke_test.py -c <snow_connection_name>
"""
import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from contract_loader import load_contract

CONTRACT, CONTRACT_HASH = load_contract()


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
    """One example row per dataset, values pulled straight from the contract's
    `example` fields — adding a dataset file under ingestion/contract/datasets/ with examples
    extends this scenario automatically, no edit needed here."""
    dataset_counts = {name: 1 for name in CONTRACT["datasets"]}
    lines = [manifest(batch_id, dataset_counts, captured_at)]
    for ds_name, ds in CONTRACT["datasets"].items():
        missing = [c for c, spec in ds["columns"].items() if "example" not in spec]
        if missing:
            sys.exit(f"run_smoke_test.py: dataset '{ds_name}' columns missing an "
                      f"'example' value in the contract: {missing}")
        values = {col: spec["example"] for col, spec in ds["columns"].items()}
        lines.append(record(batch_id, ds_name, 1, values))
    return lines


def _zero_counts_except(dataset, count):
    return {name: (count if name == dataset else 0) for name in CONTRACT["datasets"]}


def build_rejected_batch(batch_id, captured_at):
    """Duplicate primary key in stores -> REJECTED. Deliberately broken data,
    so hand-crafted rather than derived from contract examples."""
    lines = [manifest(batch_id, _zero_counts_except("stores", 2), captured_at)]
    for row_number in (1, 2):
        lines.append(record(batch_id, "stores", row_number, {
            "store_id": 1042, "store_name": "Manchester Deansgate", "region": "North West",
            "format": "High street", "opened_date": "2019-03-11"}))
    return lines


def build_incomplete_batch(batch_id, captured_at):
    """Manifest declares 2 store rows, only 1 delivered -> INCOMPLETE."""
    lines = [manifest(batch_id, _zero_counts_except("stores", 2), captured_at)]
    lines.append(record(batch_id, "stores", 1, {
        "store_id": 1043, "store_name": "Leeds Briggate", "region": "Yorkshire",
        "format": "High street", "opened_date": "2020-01-15"}))
    return lines


def upload_and_process(connection, batch_id, lines):
    local_path = Path(f"/tmp/{batch_id}.jsonl")
    local_path.write_text("\n".join(lines) + "\n")

    subprocess.run(["snow", "stage", "copy", str(local_path),
                     "@NERO_DB.\"00_BRONZE\".LANDING_STAGE", "--overwrite", "-c", connection], check=True)

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
    subprocess.run(["snow", "sql", "-c", connection, "-q", copy_sql], check=True)

    call_sql = f"CALL NERO_DB.\"02_CONTROL\".PROCESS_BATCH('{batch_id}');"
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

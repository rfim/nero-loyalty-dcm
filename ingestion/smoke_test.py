#!/usr/bin/env python3
"""Generates 3 synthetic batches (PUBLISHED, REJECTED, INCOMPLETE), lands each
directly into the typed BRONZE_<DATASET>/BRONZE_BATCH_MANIFESTS tables, calls
PROCESS_BATCH, and prints outcomes.

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

from contract_loader import load_contract

CONTRACT, CONTRACT_HASH = load_contract()


def sql_value(value, col_type):
    if value is None:
        return "NULL"
    if col_type == "integer":
        return str(int(value))
    if col_type == "decimal":
        return str(float(value))
    if col_type == "date":
        return f"TO_DATE('{value}')"
    if col_type == "timestamp_tz":
        return f"TO_TIMESTAMP_TZ('{value}')"
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"


def manifest_sql(batch_id, dataset_counts, captured_at, load_modes=None):
    load_modes = load_modes or {}
    datasets = {
        name: {"row_count": count, **({"load_mode": load_modes[name]} if name in load_modes else {})}
        for name, count in dataset_counts.items()
    }
    datasets_json = json.dumps(datasets)
    return f"""
    INSERT INTO NERO_DB."00_BRONZE".BRONZE_BATCH_MANIFESTS
        (BATCH_ID, CONTRACT_ID, CONTRACT_VERSION, CONTRACT_HASH, SOURCE_SYSTEM, CAPTURED_AT, DATASETS)
    SELECT '{batch_id}', '{CONTRACT["contract_id"]}', {CONTRACT["version"]}, '{CONTRACT_HASH}',
        'smoke-test', '{captured_at}', PARSE_JSON($${datasets_json}$$);
    """


def record_insert_sql(batch_id, dataset, rows):
    ds_spec = CONTRACT["datasets"][dataset]
    cols = list(ds_spec["columns"].keys())
    col_list = ", ".join(c.upper() for c in cols) + ", BATCH_ID, FILE_ROW_NUMBER"
    values_sql = []
    for i, values in enumerate(rows, start=1):
        vals = [sql_value(values.get(c), ds_spec["columns"][c]["type"]) for c in cols]
        vals.append(f"'{batch_id}'")
        vals.append(str(i))
        values_sql.append(f"({', '.join(vals)})")
    table = f'NERO_DB."00_BRONZE".BRONZE_{dataset.upper()}'
    return f"INSERT INTO {table} ({col_list}) VALUES {', '.join(values_sql)};"


def build_valid_batch(batch_id, captured_at):
    """One example row per dataset, values pulled straight from the contract's
    `example` fields — adding a dataset file under ingestion/contract/datasets/ with examples
    extends this scenario automatically, no edit needed here."""
    dataset_counts = {name: 1 for name in CONTRACT["datasets"]}
    statements = [manifest_sql(batch_id, dataset_counts, captured_at)]
    for ds_name, ds in CONTRACT["datasets"].items():
        missing = [c for c, spec in ds["columns"].items() if "example" not in spec]
        if missing:
            sys.exit(f"smoke_test.py: dataset '{ds_name}' columns missing an "
                      f"'example' value in the contract: {missing}")
        values = {col: spec["example"] for col, spec in ds["columns"].items()}
        statements.append(record_insert_sql(batch_id, ds_name, [values]))
    return statements


def _zero_counts_except(dataset, count):
    return {name: (count if name == dataset else 0) for name in CONTRACT["datasets"]}


def build_rejected_batch(batch_id, captured_at):
    """Duplicate primary key in stores -> REJECTED. Deliberately broken data,
    so hand-crafted rather than derived from contract examples."""
    statements = [manifest_sql(batch_id, _zero_counts_except("stores", 2), captured_at)]
    row = {"store_id": 1042, "store_name": "Manchester Deansgate", "region": "North West",
           "format": "High street", "opened_date": "2019-03-11"}
    statements.append(record_insert_sql(batch_id, "stores", [row, row]))
    return statements


def build_incomplete_batch(batch_id, captured_at):
    """Manifest declares 2 store rows, only 1 delivered -> INCOMPLETE."""
    statements = [manifest_sql(batch_id, _zero_counts_except("stores", 2), captured_at)]
    row = {"store_id": 1043, "store_name": "Leeds Briggate", "region": "Yorkshire",
           "format": "High street", "opened_date": "2020-01-15"}
    statements.append(record_insert_sql(batch_id, "stores", [row]))
    return statements


def upload_and_process(connection, batch_id, statements):
    combined = "\n".join(statements)
    subprocess.run(["snow", "sql", "-c", connection, "-q", combined], check=True)

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
        # From Snowflake's own clock -- see load_csv_batch.py's matching
        # comment for why (observed clock skew under EXTERNAL_ACCESS_INTEGRATIONS).
        now_result = subprocess.run(
            ["snow", "sql", "-c", args.connection, "--format", "JSON", "-q", "SELECT CURRENT_TIMESTAMP() AS NOW;"],
            check=True, capture_output=True, text=True,
        )
        captured_at = json.loads(now_result.stdout)[0]["NOW"]
        statements = builder(batch_id, captured_at)
        print(f"\n=== {label}: {batch_id} ===")
        output = upload_and_process(args.connection, batch_id, statements)
        print(output)


if __name__ == "__main__":
    main()

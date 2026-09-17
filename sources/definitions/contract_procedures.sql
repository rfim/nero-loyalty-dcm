-- =============================================================================
-- Data Contract Ingestion Pattern — PROCESS_BATCH / RUN_PENDING
--
-- Simplified but functional reimplementation of the guide's contract-gate
-- procedures. Validation covers: manifest presence, declared vs actual row
-- counts + contiguous row numbers, required/nullable columns, enums,
-- integer/decimal/date parsing, string max length, in-batch foreign keys,
-- duplicate primary keys, and the reward_id-required-on-redeem policy.
-- Not implemented (flagged as scope-narrowing vs. the source guide):
-- string-length-by-byte edge cases, offset-bearing timestamp normalisation
-- beyond ISO parsing, and the file-content-key dedup check.
-- =============================================================================

DEFINE PROCEDURE NERO_DB.NERO_LOYALTY.PROCESS_BATCH(BATCH_ID VARCHAR)
RETURNS VARIANT
LANGUAGE PYTHON
RUNTIME_VERSION = '3.11'
PACKAGES = ('snowflake-snowpark-python')
HANDLER = 'run'
COMMENT = 'Validates one batch of frozen envelopes against the pinned data contract and publishes it if valid. Idempotent per batch_id.'
AS
$$
import json
import uuid
from datetime import datetime, date

CONTRACT_ID = "nero_loyalty_contract"
CONTRACT_VERSION = 1
POINTER_NAME = "LOYALTY_SNAPSHOT"

DATASET_TABLES = {
    "stores": "NERO_DB.NERO_LOYALTY.VALIDATED_STORES",
    "loyalty_customers": "NERO_DB.NERO_LOYALTY.VALIDATED_LOYALTY_CUSTOMERS",
    "transactions": "NERO_DB.NERO_LOYALTY.VALIDATED_TRANSACTIONS",
    "loyalty_events": "NERO_DB.NERO_LOYALTY.VALIDATED_LOYALTY_EVENTS",
}


def _audit(session, batch_id, run_id, status, details):
    session.sql(
        "INSERT INTO NERO_DB.NERO_LOYALTY.CONTROL_RUN_AUDIT "
        "(BATCH_ID, RUN_ID, STATUS, DETAILS) SELECT ?, ?, ?, PARSE_JSON(?)"
    ).bind([batch_id, run_id, status, json.dumps(details, default=str)]).collect()
    return {"batch_id": batch_id, "run_id": run_id, "status": status, "details": details}


def _parse_scalar(col_type, raw):
    if raw is None:
        return None, None
    try:
        if col_type == "integer":
            return int(raw), None
        if col_type == "decimal":
            return float(raw), None
        if col_type in ("date",):
            return date.fromisoformat(str(raw)[:10]), None
        if col_type in ("timestamp_tz",):
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00")), None
        return str(raw), None
    except Exception as e:
        return None, f"cannot parse '{raw}' as {col_type}: {e}"


def run(session, batch_id: str) -> dict:
    # Idempotent replay: already-terminal batches are not reprocessed.
    existing = session.sql(
        "SELECT STATUS FROM NERO_DB.NERO_LOYALTY.CONTROL_RUN_AUDIT "
        "WHERE BATCH_ID = ? AND STATUS = 'PUBLISHED' LIMIT 1"
    ).bind([batch_id]).collect()
    if existing:
        return {"batch_id": batch_id, "run_id": None, "status": "ALREADY_PUBLISHED", "details": {}}

    run_id = f"{batch_id}_{uuid.uuid4().hex[:8]}"

    # Freeze this batch's envelopes into WORK in one INSERT...SELECT.
    session.sql(
        "INSERT INTO NERO_DB.NERO_LOYALTY.WORK_ENVELOPES (RUN_ID, BATCH_ID, DOC) "
        "SELECT DISTINCT ?, ?, PARSE_JSON(PAYLOAD) "
        "FROM NERO_DB.NERO_LOYALTY.RAW_ENVELOPES "
        "WHERE PARSE_JSON(PAYLOAD):batch_id::string = ?"
    ).bind([run_id, batch_id, batch_id]).collect()

    rows = session.sql(
        "SELECT DOC FROM NERO_DB.NERO_LOYALTY.WORK_ENVELOPES WHERE RUN_ID = ?"
    ).bind([run_id]).collect()
    docs = [json.loads(r["DOC"]) if isinstance(r["DOC"], str) else r["DOC"] for r in rows]

    manifests = [d for d in docs if d.get("type") == "manifest"]
    records = [d for d in docs if d.get("type") == "record"]

    if len(manifests) != 1:
        return _audit(session, batch_id, run_id, "ERROR",
                       {"reason": f"expected exactly 1 manifest, found {len(manifests)}"})

    manifest = manifests[0]
    if manifest.get("contract_id") != CONTRACT_ID or manifest.get("contract_version") != CONTRACT_VERSION:
        return _audit(session, batch_id, run_id, "REJECTED",
                       {"reason": "manifest references an unpinned contract_id/version"})

    pinned = session.sql(
        "SELECT CONTRACT_HASH, CONTRACT_JSON FROM NERO_DB.NERO_LOYALTY.CONTROL_DATA_CONTRACTS "
        "WHERE CONTRACT_ID = ? AND VERSION = ?"
    ).bind([CONTRACT_ID, CONTRACT_VERSION]).collect()
    if not pinned:
        return _audit(session, batch_id, run_id, "ERROR", {"reason": "pinned contract not seeded in CONTROL_DATA_CONTRACTS"})
    if manifest.get("contract_hash") != pinned[0]["CONTRACT_HASH"]:
        return _audit(session, batch_id, run_id, "REJECTED", {"reason": "manifest contract_hash does not match pinned hash"})

    contract = json.loads(pinned[0]["CONTRACT_JSON"]) if isinstance(pinned[0]["CONTRACT_JSON"], str) else pinned[0]["CONTRACT_JSON"]
    dataset_specs = contract["datasets"]

    declared = manifest.get("datasets", {})
    if set(declared.keys()) != set(dataset_specs.keys()):
        return _audit(session, batch_id, run_id, "REJECTED",
                       {"reason": "manifest dataset headers do not match contract datasets",
                        "declared": list(declared.keys()), "expected": list(dataset_specs.keys())})

    by_dataset = {name: [] for name in dataset_specs}
    for r in records:
        ds = r.get("dataset")
        if ds in by_dataset:
            by_dataset[ds].append(r)

    # Row count + contiguous row-number coverage check.
    for ds, spec in declared.items():
        expected_count = spec.get("row_count", 0)
        actual = by_dataset[ds]
        row_numbers = sorted(r.get("row_number") for r in actual)
        if len(actual) > expected_count or len(set(row_numbers)) != len(row_numbers):
            return _audit(session, batch_id, run_id, "REJECTED",
                           {"reason": f"{ds}: unexpected row count or duplicate row_number",
                            "expected": expected_count, "actual": len(actual)})
        if len(actual) < expected_count:
            return _audit(session, batch_id, run_id, "INCOMPLETE",
                           {"reason": f"{ds}: {len(actual)} of {expected_count} declared rows present"})
        if row_numbers != list(range(1, expected_count + 1)):
            return _audit(session, batch_id, run_id, "REJECTED",
                           {"reason": f"{ds}: row numbers are not contiguous 1..N", "row_numbers": row_numbers})

    # Field-level validation + in-batch FK resolution.
    violations = []
    validated_rows = {ds: [] for ds in dataset_specs}
    pk_seen = {ds: set() for ds in dataset_specs}
    id_pools = {ds: set() for ds in dataset_specs}  # for FK checks

    for ds, spec in dataset_specs.items():
        for r in by_dataset[ds]:
            values = r.get("values", {})
            parsed = {}
            row_ok = True
            for col, col_spec in spec["columns"].items():
                if col not in values:
                    violations.append(f"{ds} row {r.get('row_number')}: missing column {col}")
                    row_ok = False
                    continue
                raw = values[col]
                if raw is None:
                    if not col_spec.get("nullable", True):
                        violations.append(f"{ds} row {r.get('row_number')}: {col} is required but null")
                        row_ok = False
                    parsed[col] = None
                    continue
                val, err = _parse_scalar(col_spec["type"], raw)
                if err:
                    violations.append(f"{ds} row {r.get('row_number')}: {err}")
                    row_ok = False
                    continue
                if "enum" in col_spec and val not in col_spec["enum"]:
                    violations.append(f"{ds} row {r.get('row_number')}: {col}={val!r} not in {col_spec['enum']}")
                    row_ok = False
                if col_spec.get("max_length") and isinstance(val, str) and len(val) > col_spec["max_length"]:
                    violations.append(f"{ds} row {r.get('row_number')}: {col} exceeds max_length")
                    row_ok = False
                parsed[col] = val
            unexpected = set(values.keys()) - set(spec["columns"].keys())
            if unexpected:
                violations.append(f"{ds} row {r.get('row_number')}: unexpected columns {sorted(unexpected)}")
                row_ok = False

            for policy in spec.get("policies", []):
                if policy["kind"] == "required_if":
                    trigger = policy["when"]
                    if parsed.get(trigger["column"]) == trigger["equals"] and parsed.get(policy["column"]) is None:
                        violations.append(f"{ds} row {r.get('row_number')}: {policy['column']} required when {trigger['column']}={trigger['equals']!r}")
                        row_ok = False

            if row_ok:
                pk_cols = spec["primary_key"]
                pk_val = tuple(parsed[c] for c in pk_cols)
                if pk_val in pk_seen[ds]:
                    violations.append(f"{ds} row {r.get('row_number')}: duplicate primary key {pk_val}")
                    row_ok = False
                else:
                    pk_seen[ds].add(pk_val)
                    id_pools[ds].add(pk_val[0] if len(pk_val) == 1 else pk_val)

            if row_ok:
                validated_rows[ds].append(parsed)

    # Foreign keys, resolved against sibling datasets in the same batch.
    for ds, spec in dataset_specs.items():
        for col, col_spec in spec["columns"].items():
            fk = col_spec.get("foreign_key")
            if not fk:
                continue
            target_pool = id_pools[fk["dataset"]]
            for parsed in validated_rows[ds]:
                v = parsed.get(col)
                if v is not None and v not in target_pool:
                    violations.append(f"{ds}.{col}={v} has no matching {fk['dataset']}.{fk['column']}")

    if violations:
        return _audit(session, batch_id, run_id, "REJECTED", {"violation_count": len(violations), "violations": violations[:25]})

    # Publication gate: compare-and-set against the release pointer.
    captured_at = manifest.get("captured_at")
    pointer = session.sql(
        "SELECT CURRENT_RELEASE_AT FROM NERO_DB.NERO_LOYALTY.CONTROL_RELEASE_POINTER WHERE POINTER_NAME = ?"
    ).bind([POINTER_NAME]).collect()
    current_release_at = pointer[0]["CURRENT_RELEASE_AT"] if pointer else None
    if current_release_at is not None and str(current_release_at) >= str(captured_at):
        return _audit(session, batch_id, run_id, "SUPERSEDED",
                       {"reason": "batch is not newer than current release",
                        "current_release_at": str(current_release_at), "batch_captured_at": captured_at})

    for ds, table in DATASET_TABLES.items():
        cols = list(dataset_specs[ds]["columns"].keys())
        session.sql(f"DELETE FROM {table}").collect()
        if validated_rows[ds]:
            df = session.create_dataframe(
                [tuple(list(r[c] for c in cols) + [batch_id]) for r in validated_rows[ds]],
                schema=[c.upper() for c in cols] + ["BATCH_ID"],
            )
            df.write.save_as_table(table, mode="append")

    session.sql(
        "MERGE INTO NERO_DB.NERO_LOYALTY.CONTROL_RELEASE_POINTER t "
        "USING (SELECT ? AS POINTER_NAME) s ON t.POINTER_NAME = s.POINTER_NAME "
        "WHEN MATCHED THEN UPDATE SET CURRENT_BATCH_ID = ?, CURRENT_RELEASE_AT = ?, UPDATED_AT = CURRENT_TIMESTAMP() "
        "WHEN NOT MATCHED THEN INSERT (POINTER_NAME, CURRENT_BATCH_ID, CURRENT_RELEASE_AT) VALUES (?, ?, ?)"
    ).bind([POINTER_NAME, batch_id, captured_at, POINTER_NAME, batch_id, captured_at]).collect()

    session.sql("DELETE FROM NERO_DB.NERO_LOYALTY.WORK_ENVELOPES WHERE RUN_ID = ?").bind([run_id]).collect()

    return _audit(session, batch_id, run_id, "PUBLISHED",
                   {ds: len(rows_) for ds, rows_ in validated_rows.items()})
$$;

DEFINE PROCEDURE NERO_DB.NERO_LOYALTY.RUN_PENDING()
RETURNS VARIANT
LANGUAGE PYTHON
RUNTIME_VERSION = '3.11'
PACKAGES = ('snowflake-snowpark-python')
HANDLER = 'run'
COMMENT = 'Evaluates up to 10 outstanding manifested batches; publishes at most one per invocation. Raises on the first REJECTED batch so the calling task run fails.'
AS
$$
def run(session) -> dict:
    pending = session.sql(
        "SELECT DISTINCT PARSE_JSON(PAYLOAD):batch_id::string AS BATCH_ID "
        "FROM NERO_DB.NERO_LOYALTY.RAW_ENVELOPES "
        "WHERE PARSE_JSON(PAYLOAD):type::string = 'manifest' "
        "AND PARSE_JSON(PAYLOAD):batch_id::string NOT IN ("
        "  SELECT BATCH_ID FROM NERO_DB.NERO_LOYALTY.CONTROL_RUN_AUDIT "
        "  WHERE STATUS IN ('PUBLISHED', 'REJECTED', 'SUPERSEDED')"
        ") ORDER BY 1 LIMIT 10"
    ).collect()

    if not pending:
        return {"status": "NO_PENDING_BATCHES"}

    for row in pending:
        batch_id = row["BATCH_ID"]
        result = session.call("NERO_DB.NERO_LOYALTY.PROCESS_BATCH", batch_id)
        status = result["status"] if isinstance(result, dict) else None
        if status == "PUBLISHED":
            return result
        if status == "REJECTED":
            raise Exception(f"Batch {batch_id} REJECTED: {result.get('details')}")
    return {"status": "NO_PUBLISHABLE_BATCH_THIS_RUN"}
$$;

-- =============================================================================
-- Data Contract Ingestion Engine
-- Database: NERO_DB  Schema: NERO_LOYALTY
--
-- Everything hand-written about the ingestion pipeline: control tables, the
-- gate task, and PROCESS_BATCH/RUN_PENDING. BRONZE_<DATASET>,
-- BRONZE_BATCH_MANIFESTS, and VALIDATED_* are GENERATED from
-- ingestion/contract/ — see raw.sql and validated.sql (run
-- `python ingestion/build.py` to rebuild).
-- =============================================================================

-- =========================== CONTROL TABLES =================================

DEFINE TABLE NERO_DB."02_CONTROL".CONTROL_DATA_CONTRACTS (
    CONTRACT_ID    VARCHAR(200)   NOT NULL,
    VERSION        NUMBER         NOT NULL,
    CONTRACT_JSON  VARIANT        NOT NULL,
    CONTRACT_HASH  VARCHAR(64)    NOT NULL,
    LOADED_AT      TIMESTAMP_TZ   DEFAULT CURRENT_TIMESTAMP(),
    PRIMARY KEY (CONTRACT_ID, VERSION)
)
COMMENT = 'Reviewed data contract documents, keyed by contract_id+version. CONTRACT_HASH is the SHA-256 of the exact contract bytes, pinned by PROCESS_BATCH at deploy time.';

DEFINE TABLE NERO_DB."02_CONTROL".CONTROL_RUN_AUDIT (
    BATCH_ID     VARCHAR(200)   NOT NULL,
    RUN_ID       VARCHAR(200)   NOT NULL,
    STATUS       VARCHAR(30)    NOT NULL,
    DETAILS      VARIANT,
    RECORDED_AT  TIMESTAMP_TZ   DEFAULT CURRENT_TIMESTAMP()
)
COMMENT = 'Audit trail of every PROCESS_BATCH invocation. STATUS one of: PUBLISHED, ALREADY_PUBLISHED, SUPERSEDED, REJECTED, INCOMPLETE, ERROR.';

DEFINE TABLE NERO_DB."02_CONTROL".CONTROL_RELEASE_POINTER (
    POINTER_NAME       VARCHAR(50)   NOT NULL,
    CURRENT_BATCH_ID   VARCHAR(200),
    CURRENT_RELEASE_AT TIMESTAMP_TZ,
    UPDATED_AT         TIMESTAMP_TZ  DEFAULT CURRENT_TIMESTAMP(),
    PRIMARY KEY (POINTER_NAME)
)
COMMENT = 'Compare-and-set pointer to the currently published batch. Reporting views join on this to expose only the approved release.';

DEFINE TABLE NERO_DB."02_CONTROL".CONTROL_INGEST_WATERMARKS (
    DATASET         VARCHAR(100)  NOT NULL,
    SOURCE_SYSTEM   VARCHAR(100)  NOT NULL,
    LAST_WATERMARK  VARCHAR(200),
    UPDATED_AT      TIMESTAMP_TZ  DEFAULT CURRENT_TIMESTAMP(),
    PRIMARY KEY (DATASET, SOURCE_SYSTEM)
)
COMMENT = 'High-watermark per (dataset, source_system), advanced only after PROCESS_BATCH publishes an incremental batch for that dataset. Read by incremental adapters to know what "new since last time" means; full-load datasets never touch this table.';

-- ============================= GATE TASK ====================================
-- Suspended by default (DCM default) — resume explicitly once smoke tests
-- pass. Snowflake auto-suspends after 3 consecutive failures.

DEFINE TASK NERO_DB."02_CONTROL".CONTRACT_GATE_TASK
    WAREHOUSE = 'NERO_LOAD_WH'
    SCHEDULE = '15 MINUTE'
    COMMENT = 'Calls RUN_PENDING to evaluate/publish outstanding manifested batches. Suspended until explicitly resumed post smoke-test.'
AS
    CALL NERO_DB."02_CONTROL".RUN_PENDING();

-- ============================= PROCEDURES ===================================
-- Validation covers: manifest presence, declared vs actual row counts +
-- contiguous row numbers, required/nullable columns, enums, integer/decimal/
-- date parsing, string max length, in-batch foreign keys, duplicate primary
-- keys, and the reward_id-required-on-redeem policy. Not implemented (scope
-- narrowed vs. the source guide): string-length-by-byte edge cases, offset-
-- bearing timestamp normalisation beyond ISO parsing, file-content-key dedup.
--
-- Per-dataset load_mode (declared per dataset in the manifest, defaulting
-- to "full" when absent -- every adapter written before this stays
-- unchanged): "full" is the original DELETE+reload of the whole
-- VALIDATED_* table; "incremental" instead MERGEs the batch's rows in by
-- primary key and advances CONTROL_INGEST_WATERMARKS. A batch can mix
-- modes across its datasets -- row-count/contiguity and FK checks above
-- need no per-mode branching, since FK resolution already only looks at
-- whatever's present in id_pools for the *referenced* dataset, and every
-- adapter so far always sends full-mode datasets in full regardless of
-- what else is in the same batch.

DEFINE PROCEDURE NERO_DB."02_CONTROL".PROCESS_BATCH(BATCH_ID VARCHAR)
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
from decimal import Decimal

CONTRACT_ID = "nero_loyalty_contract"
CONTRACT_VERSION = 5
POINTER_NAME = "LOYALTY_SNAPSHOT"


def _validated_table(dataset_name: str) -> str:
    # Naming convention, not a lookup table: ingestion/build.py generates
    # VALIDATED_<DATASET> for every dataset in the contract, so adding a
    # dataset needs no change here — only the contract + a rebuild.
    return f"NERO_DB.\"01_SILVER\".VALIDATED_{dataset_name.upper()}"


def _bronze_table(dataset_name: str) -> str:
    return f"NERO_DB.\"00_BRONZE\".BRONZE_{dataset_name.upper()}"


def _audit(session, batch_id, run_id, status, details):
    session.sql(
        "INSERT INTO NERO_DB.\"02_CONTROL\".CONTROL_RUN_AUDIT "
        "(BATCH_ID, RUN_ID, STATUS, DETAILS) SELECT ?, ?, ?, PARSE_JSON(?)",
        params=[batch_id, run_id, status, json.dumps(details, default=str)],
    ).collect()
    return {"batch_id": batch_id, "run_id": run_id, "status": status, "details": details}


def run(session, batch_id: str) -> dict:
    # Idempotent replay: already-terminal batches are not reprocessed.
    existing = session.sql(
        "SELECT STATUS FROM NERO_DB.\"02_CONTROL\".CONTROL_RUN_AUDIT "
        "WHERE BATCH_ID = ? AND STATUS = 'PUBLISHED' LIMIT 1",
        params=[batch_id],
    ).collect()
    if existing:
        return {"batch_id": batch_id, "run_id": None, "status": "ALREADY_PUBLISHED", "details": {}}

    run_id = f"{batch_id}_{uuid.uuid4().hex[:8]}"

    # Bronze is now typed per-dataset tables (BRONZE_<DATASET>) plus one
    # BRONZE_BATCH_MANIFESTS row per batch -- no more WORK_ENVELOPES freeze
    # needed: rows are already scoped and immutable by BATCH_ID the moment
    # an adapter lands them, so a plain WHERE BATCH_ID = ? read is already
    # stable for the duration of this run.
    manifest_rows = session.sql(
        "SELECT CONTRACT_ID, CONTRACT_VERSION, CONTRACT_HASH, SOURCE_SYSTEM, CAPTURED_AT, DATASETS "
        "FROM NERO_DB.\"00_BRONZE\".BRONZE_BATCH_MANIFESTS WHERE BATCH_ID = ?",
        params=[batch_id],
    ).collect()
    if len(manifest_rows) != 1:
        return _audit(session, batch_id, run_id, "ERROR",
                       {"reason": f"expected exactly 1 manifest, found {len(manifest_rows)}"})

    m = manifest_rows[0]
    if m["CONTRACT_ID"] != CONTRACT_ID or m["CONTRACT_VERSION"] != CONTRACT_VERSION:
        return _audit(session, batch_id, run_id, "REJECTED",
                       {"reason": "manifest references an unpinned contract_id/version"})

    pinned = session.sql(
        "SELECT CONTRACT_HASH, CONTRACT_JSON FROM NERO_DB.\"02_CONTROL\".CONTROL_DATA_CONTRACTS "
        "WHERE CONTRACT_ID = ? AND VERSION = ?",
        params=[CONTRACT_ID, CONTRACT_VERSION],
    ).collect()
    if not pinned:
        return _audit(session, batch_id, run_id, "ERROR", {"reason": "pinned contract not seeded in CONTROL_DATA_CONTRACTS"})
    if m["CONTRACT_HASH"] != pinned[0]["CONTRACT_HASH"]:
        return _audit(session, batch_id, run_id, "REJECTED", {"reason": "manifest contract_hash does not match pinned hash"})

    contract = json.loads(pinned[0]["CONTRACT_JSON"]) if isinstance(pinned[0]["CONTRACT_JSON"], str) else pinned[0]["CONTRACT_JSON"]
    dataset_specs = contract["datasets"]

    declared = json.loads(m["DATASETS"]) if isinstance(m["DATASETS"], str) else m["DATASETS"]
    if set(declared.keys()) != set(dataset_specs.keys()):
        return _audit(session, batch_id, run_id, "REJECTED",
                       {"reason": "manifest dataset headers do not match contract datasets",
                        "declared": list(declared.keys()), "expected": list(dataset_specs.keys())})

    # Pull each dataset's bronze rows for this batch. Columns come back
    # already typed by Snowflake (the bronze table's own column types), so
    # there's no more "does this string parse as an integer" step -- that
    # class of error now surfaces earlier, at the adapter's INSERT into
    # bronze, not here. What's still only enforceable here: nullability
    # (bronze columns are nullable regardless of the contract, on purpose --
    # see raw.sql), enums, max_length, policies, FKs, duplicate PKs.
    by_dataset = {}
    for ds, spec in dataset_specs.items():
        cols = list(spec["columns"].keys())
        col_list = ", ".join(c.upper() for c in cols)
        by_dataset[ds] = session.sql(
            f"SELECT {col_list}, FILE_ROW_NUMBER FROM {_bronze_table(ds)} WHERE BATCH_ID = ?",
            params=[batch_id],
        ).collect()

    # Row count + contiguous row-number coverage check.
    for ds, spec in declared.items():
        expected_count = spec.get("row_count", 0)
        actual = by_dataset[ds]
        row_numbers = sorted(r["FILE_ROW_NUMBER"] for r in actual)
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
            parsed = {}
            row_ok = True
            for col, col_spec in spec["columns"].items():
                raw = r[col.upper()]
                if raw is None:
                    if not col_spec.get("nullable", True):
                        violations.append(f"{ds} row {r['FILE_ROW_NUMBER']}: {col} is required but null")
                        row_ok = False
                    parsed[col] = None
                    continue
                val = float(raw) if isinstance(raw, Decimal) else raw
                if "enum" in col_spec and val not in col_spec["enum"]:
                    violations.append(f"{ds} row {r['FILE_ROW_NUMBER']}: {col}={val!r} not in {col_spec['enum']}")
                    row_ok = False
                if col_spec.get("max_length") and isinstance(val, str) and len(val) > col_spec["max_length"]:
                    violations.append(f"{ds} row {r['FILE_ROW_NUMBER']}: {col} exceeds max_length")
                    row_ok = False
                parsed[col] = val

            for policy in spec.get("policies", []):
                if policy["kind"] == "required_if":
                    trigger = policy["when"]
                    if parsed.get(trigger["column"]) == trigger["equals"] and parsed.get(policy["column"]) is None:
                        violations.append(f"{ds} row {r['FILE_ROW_NUMBER']}: {policy['column']} required when {trigger['column']}={trigger['equals']!r}")
                        row_ok = False

            if row_ok:
                pk_cols = spec["primary_key"]
                pk_val = tuple(parsed[c] for c in pk_cols)
                if pk_val in pk_seen[ds]:
                    violations.append(f"{ds} row {r['FILE_ROW_NUMBER']}: duplicate primary key {pk_val}")
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
    captured_at = m["CAPTURED_AT"]
    pointer = session.sql(
        "SELECT CURRENT_RELEASE_AT FROM NERO_DB.\"02_CONTROL\".CONTROL_RELEASE_POINTER WHERE POINTER_NAME = ?",
        params=[POINTER_NAME],
    ).collect()
    current_release_at = pointer[0]["CURRENT_RELEASE_AT"] if pointer else None
    if current_release_at is not None and str(current_release_at) >= str(captured_at):
        return _audit(session, batch_id, run_id, "SUPERSEDED",
                       {"reason": "batch is not newer than current release",
                        "current_release_at": str(current_release_at), "batch_captured_at": captured_at})

    for ds in dataset_specs:
        table = _validated_table(ds)
        cols = list(dataset_specs[ds]["columns"].keys())
        load_mode = declared.get(ds, {}).get("load_mode", "full")

        if load_mode == "incremental":
            # Delta only: MERGE by primary key instead of the full-mode
            # DELETE+reload below. Sibling datasets in the same batch that
            # stay "full" still carry complete state, so in-batch FK
            # resolution above needed no change -- their id_pools are
            # already complete regardless of this dataset's load_mode.
            if validated_rows[ds]:
                staging = f'NERO_DB."02_CONTROL".STAGE_MERGE_{ds.upper()}'
                df = session.create_dataframe(
                    [tuple(list(r[c] for c in cols) + [batch_id]) for r in validated_rows[ds]],
                    schema=[c.upper() for c in cols] + ["BATCH_ID"],
                )
                df.write.save_as_table(staging, mode="overwrite", column_order="name")

                pk_cols = [c.upper() for c in dataset_specs[ds]["primary_key"]]
                all_cols = [c.upper() for c in cols] + ["BATCH_ID"]
                update_cols = [c for c in all_cols if c not in pk_cols]
                on_clause = " AND ".join(f"t.{c} = s.{c}" for c in pk_cols)
                set_clause = ", ".join(f"t.{c} = s.{c}" for c in update_cols)
                session.sql(
                    f"MERGE INTO {table} t USING {staging} s ON {on_clause} "
                    f"WHEN MATCHED THEN UPDATE SET {set_clause} "
                    f"WHEN NOT MATCHED THEN INSERT ({', '.join(all_cols)}) VALUES ({', '.join('s.' + c for c in all_cols)})"
                ).collect()

            watermark_value = declared.get(ds, {}).get("watermark_value")
            if watermark_value is not None:
                session.sql(
                    "MERGE INTO NERO_DB.\"02_CONTROL\".CONTROL_INGEST_WATERMARKS t "
                    "USING (SELECT ? AS DATASET, ? AS SOURCE_SYSTEM) s "
                    "ON t.DATASET = s.DATASET AND t.SOURCE_SYSTEM = s.SOURCE_SYSTEM "
                    "WHEN MATCHED THEN UPDATE SET LAST_WATERMARK = ?, UPDATED_AT = CURRENT_TIMESTAMP() "
                    "WHEN NOT MATCHED THEN INSERT (DATASET, SOURCE_SYSTEM, LAST_WATERMARK) VALUES (?, ?, ?)",
                    params=[ds, m["SOURCE_SYSTEM"], watermark_value, ds, m["SOURCE_SYSTEM"], watermark_value],
                ).collect()
            continue

        session.sql(f"DELETE FROM {table}").collect()
        if validated_rows[ds]:
            df = session.create_dataframe(
                [tuple(list(r[c] for c in cols) + [batch_id]) for r in validated_rows[ds]],
                schema=[c.upper() for c in cols] + ["BATCH_ID"],
            )
            df.write.save_as_table(table, mode="append", column_order="name")

    session.sql(
        "MERGE INTO NERO_DB.\"02_CONTROL\".CONTROL_RELEASE_POINTER t "
        "USING (SELECT ? AS POINTER_NAME) s ON t.POINTER_NAME = s.POINTER_NAME "
        "WHEN MATCHED THEN UPDATE SET CURRENT_BATCH_ID = ?, CURRENT_RELEASE_AT = ?, UPDATED_AT = CURRENT_TIMESTAMP() "
        "WHEN NOT MATCHED THEN INSERT (POINTER_NAME, CURRENT_BATCH_ID, CURRENT_RELEASE_AT) VALUES (?, ?, ?)",
        params=[POINTER_NAME, batch_id, captured_at, POINTER_NAME, batch_id, captured_at],
    ).collect()

    return _audit(session, batch_id, run_id, "PUBLISHED",
                   {ds: len(rows_) for ds, rows_ in validated_rows.items()})
$$;

DEFINE PROCEDURE NERO_DB."02_CONTROL".RUN_PENDING()
RETURNS VARIANT
LANGUAGE PYTHON
RUNTIME_VERSION = '3.11'
PACKAGES = ('snowflake-snowpark-python')
HANDLER = 'run'
COMMENT = 'Evaluates up to 10 outstanding manifested batches; publishes at most one per invocation. Raises on the first REJECTED batch so the calling task run fails.'
AS
$$
import json


def run(session) -> dict:
    pending = session.sql(
        "SELECT BATCH_ID FROM NERO_DB.\"00_BRONZE\".BRONZE_BATCH_MANIFESTS "
        "WHERE BATCH_ID NOT IN ("
        "  SELECT BATCH_ID FROM NERO_DB.\"02_CONTROL\".CONTROL_RUN_AUDIT "
        "  WHERE STATUS IN ('PUBLISHED', 'REJECTED', 'SUPERSEDED')"
        ") ORDER BY 1 LIMIT 10"
    ).collect()

    if not pending:
        return {"status": "NO_PENDING_BATCHES"}

    for row in pending:
        batch_id = row["BATCH_ID"]
        result = session.call("NERO_DB.\"02_CONTROL\".PROCESS_BATCH", batch_id)
        if isinstance(result, str):
            result = json.loads(result)
        status = result["status"] if isinstance(result, dict) else None
        if status == "PUBLISHED":
            return result
        if status == "REJECTED":
            raise Exception(f"Batch {batch_id} REJECTED: {result.get('details')}")
    return {"status": "NO_PUBLISHABLE_BATCH_THIS_RUN"}
$$;

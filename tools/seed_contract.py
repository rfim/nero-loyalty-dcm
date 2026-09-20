#!/usr/bin/env python3
"""Seeds contracts/loyalty.yaml into CONTROL_DATA_CONTRACTS, pinning its SHA-256.

Data-plane operation, deliberately outside DCM (which manages schema, not rows).
Hashes the exact YAML bytes (the reviewed document); stores a JSON-converted
copy of it in the VARIANT column for easy SQL/Python querying.
Usage: python tools/seed_contract.py -c <snow_connection_name>
"""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path

import yaml
CONTRACT_PATH = Path(__file__).resolve().parent.parent / "contracts" / "loyalty.yaml"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-c", "--connection", required=True)
    args = parser.parse_args()

    raw_bytes = CONTRACT_PATH.read_bytes()
    contract_hash = hashlib.sha256(raw_bytes).hexdigest()
    contract = yaml.safe_load(raw_bytes)
    contract_json_text = json.dumps(contract)
    sql = f"""
    INSERT INTO NERO_DB.NERO_LOYALTY.CONTROL_DATA_CONTRACTS (CONTRACT_ID, VERSION, CONTRACT_JSON, CONTRACT_HASH)
    SELECT '{contract["contract_id"]}', {contract["version"]}, PARSE_JSON($${contract_json_text}$$), '{contract_hash}'
    WHERE NOT EXISTS (
        SELECT 1 FROM NERO_DB.NERO_LOYALTY.CONTROL_DATA_CONTRACTS
        WHERE CONTRACT_ID = '{contract["contract_id"]}' AND VERSION = {contract["version"]}
    );
    """
    subprocess.run(["snow", "sql", "-c", args.connection, "-q", sql], check=True)
    print(f"Seeded {contract['contract_id']} v{contract['version']}, hash={contract_hash}")


if __name__ == "__main__":
    main()

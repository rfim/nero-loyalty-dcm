#!/usr/bin/env python3
"""Seeds contracts/loyalty.json into CONTROL_DATA_CONTRACTS, pinning its SHA-256.

Data-plane operation, deliberately outside DCM (which manages schema, not rows).
Usage: python tools/seed_contract.py -c <snow_connection_name>
"""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

CONTRACT_PATH = Path(__file__).resolve().parent.parent / "contracts" / "loyalty.json"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-c", "--connection", required=True)
    args = parser.parse_args()

    raw_bytes = CONTRACT_PATH.read_bytes()
    contract_hash = hashlib.sha256(raw_bytes).hexdigest()
    contract = json.loads(raw_bytes)

    tmp_path = Path("/tmp/loyalty_contract_payload.json")
    tmp_path.write_text(json.dumps({
        "contract_id": contract["contract_id"],
        "version": contract["version"],
        "json_text": raw_bytes.decode("utf-8"),
        "hash": contract_hash,
    }))

    sql = f"""
    INSERT INTO NERO_DB.NERO_LOYALTY.CONTROL_DATA_CONTRACTS (CONTRACT_ID, VERSION, CONTRACT_JSON, CONTRACT_HASH)
    SELECT '{contract["contract_id"]}', {contract["version"]}, PARSE_JSON($${raw_bytes.decode("utf-8")}$$), '{contract_hash}'
    WHERE NOT EXISTS (
        SELECT 1 FROM NERO_DB.NERO_LOYALTY.CONTROL_DATA_CONTRACTS
        WHERE CONTRACT_ID = '{contract["contract_id"]}' AND VERSION = {contract["version"]}
    );
    """
    subprocess.run(["snow", "sql", "-c", args.connection, "-q", sql], check=True)
    print(f"Seeded {contract['contract_id']} v{contract['version']}, hash={contract_hash}")


if __name__ == "__main__":
    main()

"""Shared contract loader for build.py and smoke_test.py.

The contract is split one-YAML-per-dataset under ingestion/contract/datasets/,
plus one ingestion/contract/meta.yaml for the shared bits (contract_id,
version, ingestion source/landing strategy, freshness). This module is the
one place that knows how those files combine into a single logical document
and a single pinned hash — both scripts import it so they can't drift out of
sync (see: the smoke-test bugs found earlier by actually running the code).
"""
import hashlib
import sys
from pathlib import Path

import yaml

CONTRACT_DIR = Path(__file__).resolve().parent / "contract"
META_PATH = CONTRACT_DIR / "meta.yaml"
DATASETS_DIR = CONTRACT_DIR / "datasets"

ALLOWED = {
    "meta": {"version", "contract_id", "wire_format", "ingestion", "freshness"},
    "ingestion": {"source", "landing"},
    "source": {"type", "file_pattern", "on_error", "internal_stage", "s3"},
    "landing": {"type", "manifest_table_name", "dataset_table_prefix", "iceberg"},
    "dataset": {"primary_key", "owners", "columns", "policies"},
    "column": {"type", "nullable", "max_length", "enum", "foreign_key", "precision", "scale",
               "pii", "pii_kind", "example"},
    "foreign_key": {"dataset", "column"},
    "policy": {"kind", "column", "when"},
}


def _check_keys(d, allowed_key, path):
    unknown = set(d.keys()) - ALLOWED[allowed_key]
    if unknown:
        sys.exit(f"contract: unknown key(s) {sorted(unknown)} at {path} — refusing to load")


def load_contract():
    """Returns (doc, contract_hash).

    doc has the same shape the contract had as one file: {version,
    contract_id, wire_format, ingestion, freshness, datasets: {name: {...}}}.

    contract_hash is SHA-256 over meta.yaml + every datasets/*.yaml,
    concatenated in sorted filename order — any byte change in any
    contributing file changes the hash, same "reviewed exact bytes" model
    as when the contract was one file.
    """
    meta_bytes = META_PATH.read_bytes()
    meta = yaml.safe_load(meta_bytes)
    _check_keys(meta, "meta", "meta.yaml")
    _check_keys(meta["ingestion"], "ingestion", "meta.yaml:ingestion")
    _check_keys(meta["ingestion"]["source"], "source", "meta.yaml:ingestion.source")
    _check_keys(meta["ingestion"]["landing"], "landing", "meta.yaml:ingestion.landing")

    dataset_files = sorted(DATASETS_DIR.glob("*.yaml"))
    if not dataset_files:
        sys.exit(f"contract: no dataset files found under {DATASETS_DIR}")

    hash_parts = [meta_bytes]
    datasets = {}
    for f in dataset_files:
        raw = f.read_bytes()
        hash_parts.append(raw)
        ds = yaml.safe_load(raw)
        _check_keys(ds, "dataset", f"datasets/{f.name}")
        for col_name, col in ds["columns"].items():
            _check_keys(col, "column", f"datasets/{f.name}:columns.{col_name}")
            if "foreign_key" in col:
                _check_keys(col["foreign_key"], "foreign_key",
                             f"datasets/{f.name}:columns.{col_name}.foreign_key")
        for policy in ds.get("policies", []):
            _check_keys(policy, "policy", f"datasets/{f.name}:policies")
        datasets[f.stem] = ds

    contract_hash = hashlib.sha256(b"\x00".join(hash_parts)).hexdigest()
    doc = {**meta, "datasets": datasets}
    return doc, contract_hash

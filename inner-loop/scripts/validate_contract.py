#!/usr/bin/env python3
"""Fail-closed validation for frozen DS4F inner-loop contracts."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path, PurePosixPath

HEX64 = re.compile(r"^[0-9a-f]{64}$")
SAFE_CONTAINER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
HASH_FIELDS = (
    "launcher_sha256",
    "bench_sha256",
    "runner_sha256",
    "dispatcher_sha256",
    "summarizer_sha256",
)


class ContractError(ValueError):
    pass


def require(mapping: dict, key: str, where: str):
    if key not in mapping:
        raise ContractError(f"{where}.{key} missing")
    return mapping[key]


def validate_contract(contract: dict) -> None:
    if contract.get("schema_version") != 1:
        raise ContractError("schema_version must equal 1")
    if contract.get("status") != "frozen":
        raise ContractError("status must equal frozen")
    if not isinstance(contract.get("iteration"), int) or contract["iteration"] < 0:
        raise ContractError("iteration must be a non-negative integer")
    for key in ("run_id", "slug"):
        value = contract.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ContractError(f"{key} must be a non-empty string")
    if contract.get("production_restore_authorized") is not False:
        raise ContractError("production_restore_authorized must be false")

    candidate = require(contract, "candidate", "contract")
    if not isinstance(candidate, dict):
        raise ContractError("candidate must be an object")
    ragged = require(candidate, "ragged_verify_mode", "candidate")
    if ragged not in {"static", "compact"}:
        raise ContractError("candidate.ragged_verify_mode must be static or compact")
    sps_mode = require(candidate, "sps_table", "candidate")
    if sps_mode not in {"none", "current", "calibrated"}:
        raise ContractError("candidate.sps_table must be none, current, or calibrated")
    profile_mode = candidate.get("profile_mode", "none")
    if profile_mode not in {"none", "sps", "sps-additive"}:
        raise ContractError(
            "candidate.profile_mode must be none, sps, or sps-additive"
        )
    if profile_mode == "sps" and ragged != "static":
        raise ContractError(
            "diagonal SPS profiling requires candidate.ragged_verify_mode=static"
        )
    if profile_mode == "sps-additive" and ragged != "compact":
        raise ContractError(
            "additive SPS profiling requires candidate.ragged_verify_mode=compact"
        )
    if profile_mode != "none" and sps_mode != "none":
        raise ContractError("SPS profiling requires candidate.sps_table=none")
    nextn_layers = candidate.get("num_nextn_predict_layers", "checkpoint")
    if nextn_layers != "checkpoint" and not (
        type(nextn_layers) is int and nextn_layers == 1
    ):
        raise ContractError(
            "candidate.num_nextn_predict_layers must be checkpoint or integer 1"
        )
    if profile_mode != "none" and nextn_layers != "checkpoint":
        raise ContractError("SPS profiling requires checkpoint NextN layers")

    target = require(contract, "target", "contract")
    if not isinstance(target, dict):
        raise ContractError("target must be an object")
    container = require(target, "container", "target")
    if not isinstance(container, str) or not SAFE_CONTAINER.fullmatch(container):
        raise ContractError("target.container is not a safe Docker name")
    for key in HASH_FIELDS:
        value = require(target, key, "target")
        if not isinstance(value, str) or not HEX64.fullmatch(value):
            raise ContractError(f"target.{key} must be a lowercase SHA-256")
    if "validator_sha256" in target and not HEX64.fullmatch(str(target["validator_sha256"])):
        raise ContractError("target.validator_sha256 must be a lowercase SHA-256")

    if profile_mode != "none":
        for key in ("profiler_sha256", "sps_module_sha256"):
            value = require(target, key, "target")
            if not isinstance(value, str) or not HEX64.fullmatch(value):
                raise ContractError(f"target.{key} must be a lowercase SHA-256")

    if sps_mode != "none":
        digest = require(target, "sps_table_sha256", "target")
        if not isinstance(digest, str) or not HEX64.fullmatch(digest):
            raise ContractError("target.sps_table_sha256 must be a lowercase SHA-256")
    if sps_mode == "calibrated":
        artifact = require(target, "sps_table_artifact", "target")
        if not isinstance(artifact, str) or not artifact:
            raise ContractError("target.sps_table_artifact must be a non-empty string")
        path = PurePosixPath(artifact)
        if path.is_absolute() or ".." in path.parts:
            raise ContractError("target.sps_table_artifact must stay inside the repository")
        if not artifact.startswith("inner-loop/artifacts/") or path.suffix != ".json":
            raise ContractError("target.sps_table_artifact must name an inner-loop JSON artifact")

    compare_to = contract.get("compare_to_run")
    if not isinstance(compare_to, str) or not compare_to:
        raise ContractError("compare_to_run must be a non-empty run id")

    if profile_mode == "none":
        expected_c = require(contract, "expected_concurrency", "contract")
        if expected_c != [1, 4, 8, 16, 32, 64]:
            raise ContractError(
                "expected_concurrency must equal [1, 4, 8, 16, 32, 64]"
            )
        expected_p = require(contract, "expected_prefill_targets", "contract")
        if expected_p != [8000, 32000, 64000, 128000, 256000]:
            raise ContractError(
                "expected_prefill_targets must equal "
                "[8000, 32000, 64000, 128000, 256000]"
            )
        winner_rule = require(contract, "winner_rule", "contract")
        if not isinstance(winner_rule, dict):
            raise ContractError("winner_rule must be an object")
        primary_rows = require(winner_rule, "primary_rows", "winner_rule")
        if (
            not isinstance(primary_rows, list)
            or not primary_rows
            or any(type(row) is not int for row in primary_rows)
            or len(set(primary_rows)) != len(primary_rows)
            or any(row not in expected_c for row in primary_rows)
        ):
            raise ContractError(
                "winner_rule.primary_rows must be unique integer rows from expected_concurrency"
            )


def load_and_validate(path: Path) -> dict:
    try:
        contract = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"cannot read contract: {exc}") from exc
    if not isinstance(contract, dict):
        raise ContractError("contract root must be an object")
    validate_contract(contract)
    return contract


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("contract", type=Path)
    args = parser.parse_args()
    try:
        contract = load_and_validate(args.contract)
    except ContractError as exc:
        print(f"CONTRACT_SCHEMA_RESULT BLOCKED reason={exc}")
        return 2
    print(
        "CONTRACT_SCHEMA_RESULT PASS "
        f"iteration={contract['iteration']} run_id={contract['run_id']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

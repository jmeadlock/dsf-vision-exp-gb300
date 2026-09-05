#!/usr/bin/env python3
"""Fail-closed validation for frozen DS4F inner-loop contracts."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

HEX64 = re.compile(r"^[0-9a-f]{64}$")
HEX40 = re.compile(r"^[0-9a-f]{40}$")
SAFE_CONTAINER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
CAMPAIGN_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,127}$")
HASH_FIELDS = (
    "launcher_sha256",
    "bench_sha256",
    "runner_sha256",
    "dispatcher_sha256",
    "summarizer_sha256",
)
FORBIDDEN_RUNTIME_KEYS = {
    "runtime_args",
    "extra_args",
    "shell",
    "command",
    "restore_command",
    "docker_args",
    "launch_args",
    "env",
}
FROZEN_IMAGE_DIGEST = "sha256:7ac467a50508b7029a23e846c150998fdd26d95c1cfd377ea7e74e28374486a6"
FROZEN_MODEL_REVISION = "6821d6ad3681a4b137b066b76094fa82ebd0a380"
FROZEN_SOURCE_REVISION = "40b3e15ddbd9a1067e181283d9900dd3f4d76ed7"
PINNED_PROFILER_SOURCE_SHA = "085a5e2a734ae5dc3f820dd50f2f490dbbdcb5e7"
PROFILE_SWEEP = {
    "batch_size_per_rank": [1, 2, 3, 4, 5, 6, 7, 8],
    "fracs": [0.2, 0.4, 0.6, 0.8, 1.0],
    "verify_num_draft_tokens": 6,
    "repeats": 3,
    "expected_m_min": 2,
    "expected_m_max": 48,
    "fit_mbin_widths": [64, 6, 1],
}
WORKLOAD_CLASSES = [
    "natural_prose",
    "code_oriented",
    "six_turn_replay",
    "warm_prefix_repeat",
    "staggered_mixed_load",
]


class ContractError(ValueError):
    pass


def require(mapping: dict, key: str, where: str):
    if key not in mapping:
        raise ContractError(f"{where}.{key} missing")
    return mapping[key]


def _require_sha256(mapping: dict, key: str, where: str) -> str:
    value = require(mapping, key, where)
    if not isinstance(value, str) or not HEX64.fullmatch(value):
        raise ContractError(f"{where}.{key} must be a lowercase SHA-256")
    return value


def _forbid_freeform_runtime_fields(value: Any, where: str = "contract") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in FORBIDDEN_RUNTIME_KEYS:
                raise ContractError(f"free-form runtime field is forbidden at {where}.{key}")
            _forbid_freeform_runtime_fields(child, f"{where}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _forbid_freeform_runtime_fields(child, f"{where}[{index}]")


def _validate_common(contract: dict) -> tuple[dict, dict]:
    if contract.get("status") != "frozen":
        raise ContractError("status must equal frozen")
    if contract.get("production_restore_authorized") is not False:
        raise ContractError("production_restore_authorized must be false")

    candidate = require(contract, "candidate", "contract")
    if not isinstance(candidate, dict):
        raise ContractError("candidate must be an object")
    target = require(contract, "target", "contract")
    if not isinstance(target, dict):
        raise ContractError("target must be an object")
    container = require(target, "container", "target")
    if not isinstance(container, str) or not SAFE_CONTAINER.fullmatch(container):
        raise ContractError("target.container is not a safe Docker name")
    for key in HASH_FIELDS:
        _require_sha256(target, key, "target")
    for optional in (
        "validator_sha256",
        "agent_workload_gate_sha256",
        "replay_gate_sha256",
        "sps_profile_gate_sha256",
    ):
        if optional in target and not HEX64.fullmatch(str(target[optional])):
            raise ContractError(f"target.{optional} must be a lowercase SHA-256")
    return candidate, target


def _validate_runtime_shape(contract: dict, candidate: dict, expected_c: list[int]) -> None:
    if candidate.get("profile_mode", "none") == "none":
        actual_c = require(contract, "expected_concurrency", "contract")
        if actual_c != expected_c:
            raise ContractError(f"expected_concurrency must equal {expected_c}")
        expected_p = require(contract, "expected_prefill_targets", "contract")
        if expected_p != [8000, 32000, 64000, 128000, 256000]:
            raise ContractError(
                "expected_prefill_targets must equal [8000, 32000, 64000, 128000, 256000]"
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


def _validate_schema_v1(contract: dict) -> None:
    if not isinstance(contract.get("iteration"), int) or contract["iteration"] < 0:
        raise ContractError("iteration must be a non-negative integer")
    for key in ("run_id", "slug"):
        value = contract.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ContractError(f"{key} must be a non-empty string")
    candidate, target = _validate_common(contract)

    ragged = require(candidate, "ragged_verify_mode", "candidate")
    if ragged not in {"static", "compact"}:
        raise ContractError("candidate.ragged_verify_mode must be static or compact")
    sps_mode = require(candidate, "sps_table", "candidate")
    if sps_mode not in {"none", "current", "calibrated"}:
        raise ContractError("candidate.sps_table must be none, current, or calibrated")
    profile_mode = candidate.get("profile_mode", "none")
    if profile_mode not in {"none", "sps", "sps-additive"}:
        raise ContractError("candidate.profile_mode must be none, sps, or sps-additive")
    if profile_mode == "sps" and ragged != "static":
        raise ContractError("diagonal SPS profiling requires candidate.ragged_verify_mode=static")
    if profile_mode == "sps-additive" and ragged != "compact":
        raise ContractError("additive SPS profiling requires candidate.ragged_verify_mode=compact")
    if profile_mode != "none" and sps_mode != "none":
        raise ContractError("SPS profiling requires candidate.sps_table=none")
    nextn_layers = candidate.get("num_nextn_predict_layers", "checkpoint")
    if nextn_layers != "checkpoint" and not (type(nextn_layers) is int and nextn_layers == 1):
        raise ContractError("candidate.num_nextn_predict_layers must be checkpoint or integer 1")
    if profile_mode != "none" and nextn_layers != "checkpoint":
        raise ContractError("SPS profiling requires checkpoint NextN layers")

    if profile_mode != "none":
        for key in ("profiler_sha256", "sps_module_sha256"):
            _require_sha256(target, key, "target")
    if sps_mode != "none":
        _require_sha256(target, "sps_table_sha256", "target")
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
    _validate_runtime_shape(contract, candidate, [1, 4, 8, 16, 32, 64])


def _validate_schema_v2(contract: dict) -> None:
    _forbid_freeform_runtime_fields(contract)
    campaign_id = require(contract, "campaign_id", "contract")
    if not isinstance(campaign_id, str) or not CAMPAIGN_ID.fullmatch(campaign_id):
        raise ContractError("campaign_id must be an immutable safe id")
    phase = require(contract, "phase", "contract")
    if phase not in {"profile", "baseline", "candidate", "control", "anchor"}:
        raise ContractError("phase must be profile, baseline, candidate, control, or anchor")
    sequence = require(contract, "sequence", "contract")
    if type(sequence) is not int or sequence < 0:
        raise ContractError("sequence must be a non-negative integer")
    for key in ("run_id", "slug", "release_state"):
        value = require(contract, key, "contract")
        if not isinstance(value, str) or not value.strip():
            raise ContractError(f"{key} must be a non-empty string")
    if contract["release_state"] != "prewritten_unreleased":
        raise ContractError("release_state must equal prewritten_unreleased")

    auth = require(contract, "authorization", "contract")
    if not isinstance(auth, dict):
        raise ContractError("authorization must be an object")
    if auth.get("run_authorized_by") != "James":
        raise ContractError("authorization.run_authorized_by must equal James")
    if auth.get("run_authorized_at_local") != "2026-09-04 18:02 CDT":
        raise ContractError("authorization.run_authorized_at_local must record 2026-09-04 18:02 CDT")
    if auth.get("production_observed_port_30003_listener") is not False:
        raise ContractError("authorization must record no listener on :30003")
    if auth.get("production_observed_running_containers") is not False:
        raise ContractError("authorization must record no running containers")
    if auth.get("restore_authorized") is not False:
        raise ContractError("authorization.restore_authorized must be false")

    candidate, target = _validate_common(contract)
    if target.get("image_digest") != FROZEN_IMAGE_DIGEST:
        raise ContractError("target.image_digest must stay frozen to Recipe v2 image")
    if target.get("model_revision") != FROZEN_MODEL_REVISION:
        raise ContractError("target.model_revision must stay frozen to Recipe v2 model")
    if target.get("sglang_source_revision") != FROZEN_SOURCE_REVISION:
        raise ContractError("target.sglang_source_revision must stay frozen to Recipe v2 source")

    ragged = require(candidate, "ragged_verify_mode", "candidate")
    if ragged not in {"static", "compact"}:
        raise ContractError("candidate.ragged_verify_mode must be static or compact")
    sps_mode = require(candidate, "sps_table", "candidate")
    if sps_mode not in {"none", "fine-grained"}:
        raise ContractError("candidate.sps_table must be none or fine-grained")
    profile_mode = candidate.get("profile_mode", "none")
    if profile_mode not in {"none", "sps-additive"}:
        raise ContractError("candidate.profile_mode must be none or sps-additive")
    if profile_mode == "sps-additive" and (phase != "profile" or ragged != "compact" or sps_mode != "none"):
        raise ContractError("schema v2 SPS profiling requires phase=profile compact no-table")
    if sps_mode == "fine-grained" and ragged != "compact":
        raise ContractError("fine-grained SPS requires compact ragged mode")
    if sps_mode == "none" and candidate.get("sps_fit_mbin_w") is not None:
        raise ContractError("candidate.sps_fit_mbin_w must be null without a table")
    if sps_mode == "none" and phase != "profile" and candidate.get("profiler_source_sha") is not None:
        raise ContractError("candidate.profiler_source_sha must be null without a table")
    nextn_layers = candidate.get("num_nextn_predict_layers", "checkpoint")
    if nextn_layers != "checkpoint":
        raise ContractError("schema v2 keeps checkpoint NextN layers")

    if phase == "profile":
        if candidate.get("profiler_source_sha") != PINNED_PROFILER_SOURCE_SHA:
            raise ContractError("profile candidate.profiler_source_sha must equal pinned PR #37815 head")
        sweep = require(contract, "profile_sweep", "contract")
        for key, expected in PROFILE_SWEEP.items():
            if sweep.get(key) != expected:
                raise ContractError(f"profile_sweep.{key} must equal {expected}")
        for key in (
            "profiler_sha256",
            "sps_module_sha256",
            "profiler_base_file_sha256",
            "profiler_pr37815_patch_sha256",
            "profiler_patched_file_sha256",
        ):
            _require_sha256(target, key, "target")
        if target["profiler_sha256"] != target["profiler_patched_file_sha256"]:
            raise ContractError("target.profiler_sha256 must equal profiler_patched_file_sha256")
    else:
        _validate_runtime_shape(contract, candidate, [1, 4, 8, 16, 32, 64])
        workload = require(contract, "expected_workload_classes", "contract")
        if workload != WORKLOAD_CLASSES:
            raise ContractError(f"expected_workload_classes must equal {WORKLOAD_CLASSES}")

    if sps_mode == "fine-grained":
        if candidate.get("sps_fit_mbin_w") not in {64, 6, 1}:
            raise ContractError("candidate.sps_fit_mbin_w must be one of 64, 6, or 1")
        profiler_source_sha = require(candidate, "profiler_source_sha", "candidate")
        if profiler_source_sha != PINNED_PROFILER_SOURCE_SHA or not HEX40.fullmatch(profiler_source_sha):
            raise ContractError("candidate.profiler_source_sha must equal pinned PR #37815 head")
        for key in (
            "profiler_base_file_sha256",
            "profiler_pr37815_patch_sha256",
            "profiler_patched_file_sha256",
            "raw_profile_a_sha256",
            "raw_profile_b_sha256",
            "fit_manifest_sha256",
            "sps_table_sha256",
            "sps_table_expected_mounted_sha256",
        ):
            _require_sha256(target, key, "target")
        if target["sps_table_expected_mounted_sha256"] != target["sps_table_sha256"]:
            raise ContractError("target mounted SHA must equal selected table SHA")
        artifact = require(target, "sps_table_artifact", "target")
        if not isinstance(artifact, str) or not artifact:
            raise ContractError("target.sps_table_artifact missing")
        path = PurePosixPath(artifact)
        wanted_prefix = f"inner-loop/campaigns/{campaign_id}/artifacts/"
        if (
            path.is_absolute()
            or ".." in path.parts
            or not artifact.startswith(wanted_prefix)
            or path.suffix != ".json"
        ):
            raise ContractError("target.sps_table_artifact must be a campaign-local JSON artifact")
    else:
        forbidden_lineage = [
            "raw_profile_a_sha256",
            "raw_profile_b_sha256",
            "fit_manifest_sha256",
            "sps_table_sha256",
            "sps_table_artifact",
            "sps_table_expected_mounted_sha256",
        ]
        if phase != "profile":
            forbidden_lineage.extend([
                "profiler_base_file_sha256",
                "profiler_pr37815_patch_sha256",
                "profiler_patched_file_sha256",
            ])
        present = [key for key in forbidden_lineage if key in target]
        if present:
            raise ContractError(f"no-table contract must not include table lineage: {present}")


def validate_contract(contract: dict) -> None:
    schema = contract.get("schema_version")
    if schema == 1:
        _validate_schema_v1(contract)
    elif schema == 2:
        _validate_schema_v2(contract)
    else:
        raise ContractError("schema_version must equal 1 or 2")


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
    if contract.get("schema_version") == 2:
        print(
            "CONTRACT_SCHEMA_RESULT PASS "
            f"campaign_id={contract['campaign_id']} phase={contract['phase']} "
            f"sequence={contract['sequence']} run_id={contract['run_id']}"
        )
    else:
        print(
            "CONTRACT_SCHEMA_RESULT PASS "
            f"iteration={contract['iteration']} run_id={contract['run_id']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

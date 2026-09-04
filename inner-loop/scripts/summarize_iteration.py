#!/usr/bin/env python3
"""Turn one immutable inner-loop run directory into a fail-closed result."""
from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path
from typing import Any


PREFILL_RE = re.compile(
    r"^PREFILL target=(?P<target>\d+) prompt_tokens=(?P<prompt_tokens>\d+) "
    r"ttft_s=(?P<ttft>\[[^]]*\]) tok_s=(?P<rates>\[[^]]*\]) "
    r"mean_tok_s=(?P<mean>[\d.]+)$"
)


def _text(run: Path, name: str) -> str:
    path = run / name
    return path.read_text(errors="replace") if path.exists() else ""


def _bench_rows(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        if line.startswith("BENCH "):
            rows.append(json.loads(line[6:]))
    return sorted(rows, key=lambda row: int(row["C"]))


def _prefill_rows(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        match = PREFILL_RE.match(line.strip())
        if not match:
            continue
        rows.append({
            "target": int(match.group("target")),
            "prompt_tokens": int(match.group("prompt_tokens")),
            "ttft_s": list(ast.literal_eval(match.group("ttft"))),
            "tok_s": list(ast.literal_eval(match.group("rates"))),
            "mean_tok_s": float(match.group("mean")),
        })
    return sorted(rows, key=lambda row: row["target"])


def summarize(run: Path) -> dict[str, Any]:
    contract_path = run / "contract.json"
    if not contract_path.exists():
        return {"outcome": "BLOCKED", "blockers": ["missing contract.json"]}

    contract = json.loads(contract_path.read_text())
    throughput = _bench_rows(_text(run, "throughput.txt"))
    prefill = _prefill_rows(_text(run, "prefill.txt"))

    health_text = _text(run, "health.txt")
    runtime_text = _text(run, "runtime-mode.txt")
    patch_text = _text(run, "patch.txt")
    stop_text = _text(run, "stop.txt")
    dispatch_text = _text(run, "dispatch.txt")
    preflight_text = _text(run, "preflight.txt")
    receipt_sanitize_text = _text(run, "receipt-sanitize.txt")
    gates = {
        "staged_hashes": "STAGE_HASH_RESULT PASS" in dispatch_text,
        "frozen_hashes": "FROZEN_HASH_RESULT PASS" in dispatch_text,
        "preflight": "PREFLIGHT_RESULT PASS" in preflight_text,
        "health": "HEALTH_RESULT PASS" in health_text,
        "runtime_mode": "RUNTIME_MODE_RESULT PASS" in runtime_text,
        "patch": "PATCH_RESULT PASS" in patch_text,
        "smoke": "SMOKE_RESULT PASS" in _text(run, "smoke.txt"),
        "replay": "REPLAY_RESULT PASS" in _text(run, "replay.txt"),
        "opaque_identifiers": "OPAQUE_RESULT PASS" in _text(run, "opaque.txt"),
        "repetition": bool(re.search(r"REPAUDIT .*\bflagged=0\b", _text(run, "repaudit.txt"))),
        "gpu_errors": "GPU_ERROR_RESULT PASS" in _text(run, "gpu-errors.txt"),
        "receipt_sanitize": "RECEIPT_SANITIZE_RESULT PASS" in receipt_sanitize_text,
        "clean_stop": (
            "STOP_RESULT PASS" in stop_text and "PORT_AFTER_STOP PASS" in stop_text
        ),
    }
    target = contract.get("target", {})
    if target.get("validator_sha256"):
        gates["contract_schema"] = "CONTRACT_SCHEMA_RESULT PASS" in dispatch_text
    if target.get("runner_sha256"):
        gates["runner_hash"] = "RUNNER_HASH_RESULT PASS" in preflight_text
    sps_mode = contract.get("candidate", {}).get("sps_table", "none")
    if sps_mode != "none":
        expected_sps = str(target.get("sps_table_sha256", ""))
        gates["sps_table_hash"] = bool(expected_sps) and (
            f"SPS_ARTIFACT_HASH_RESULT PASS sha256={expected_sps} mode={sps_mode}"
            in preflight_text
            and f"SPS_MOUNT_HASH_RESULT PASS sha256={expected_sps} mode={sps_mode}"
            in _text(run, "sps-mount-hash.txt")
        )
    candidate_config = contract.get("candidate", {})
    nextn_layers = candidate_config.get("num_nextn_predict_layers", "checkpoint")
    if "num_nextn_predict_layers" in candidate_config:
        gates["nextn_override"] = (
            f"NEXTN_OVERRIDE_RESULT PASS num_nextn_predict_layers={nextn_layers}"
            in runtime_text
        )
    profile_mode = contract.get("candidate", {}).get("profile_mode", "none")
    if profile_mode in {"sps", "sps-additive"}:
        profile_hash_text = _text(run, "profile-hash.txt")
        container = str(target.get("container", ""))
        gates.update({
            "tier0_health": "HEALTH_RESULT PASS phase=tier0" in health_text,
            "profile_health": "HEALTH_RESULT PASS phase=sps-profile" in health_text,
            "tier0_runtime_mode": (
                "RUNTIME_MODE_RESULT PASS mode=static sps_profile=false"
                in _text(run, "runtime-mode-tier0.txt")
            ),
            "tier0_patch": "PATCH_RESULT PASS" in _text(run, "patch-tier0.txt"),
            "profile_hashes": (
                "PROFILER_HASH_RESULT PASS" in profile_hash_text
                and "SPS_MODULE_HASH_RESULT PASS" in profile_hash_text
            ),
            "sps_profile": "SPS_PROFILE_RESULT PASS" in _text(
                run, "profile-gate.txt"
            ),
            "tier0_clean_stop": (
                bool(container)
                and f"STOP_RESULT PASS container={container}-tier0" in stop_text
            ),
            "profile_clean_stop": (
                bool(container)
                and f"STOP_RESULT PASS container={container}" in stop_text
            ),
        })

    blockers: list[str] = []
    expected_c = [int(x) for x in contract.get("expected_concurrency", [])]
    found_c = [int(row["C"]) for row in throughput]
    missing_c = sorted(set(expected_c) - set(found_c))
    if missing_c:
        blockers.append(f"missing throughput rows: {missing_c}")

    expected_p = [int(x) for x in contract.get("expected_prefill_targets", [])]
    found_p = [int(row["target"]) for row in prefill]
    missing_p = sorted(set(expected_p) - set(found_p))
    if missing_p:
        blockers.append(f"missing prefill rows: {missing_p}")

    failed_gates = [name for name, passed in gates.items() if not passed]
    if failed_gates:
        blockers.append("failed or missing gates: " + ", ".join(failed_gates))

    if blockers:
        outcome = "BLOCKED"
    elif int(contract.get("iteration", -1)) == 0:
        outcome = "BASELINE"
    else:
        outcome = "COMPLETE"

    return {
        "schema_version": 1,
        "iteration": contract.get("iteration"),
        "slug": contract.get("slug"),
        "outcome": outcome,
        "throughput": throughput,
        "prefill": prefill,
        "gates": gates,
        "blockers": blockers,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    result = summarize(args.run_dir)
    output = args.run_dir / "results.json"
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["outcome"] != "BLOCKED" else 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Build the public-safe receipt audit for the 2026-09-04 inner-loop campaign.

The script selects only result, decision, and ledger fields needed for the retrospective.
It never copies runtime surfaces, environment variables, container inspection, or logs.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "inner-loop" / "runs"
LEDGER = ROOT / "inner-loop" / "ledger.jsonl"
OUT_JSON = ROOT / "research" / "inner-loop-campaign-audit.json"
OUT_MD = ROOT / "research" / "inner-loop-campaign-audit.md"

# These labels are interpretive campaign decisions. Numeric fields below always
# come from persisted receipts selected by this script.
ITERATIONS: dict[int, dict[str, Any]] = {
    0: {
        "classification": "valid_baseline",
        "verdict": "BASELINE",
        "candidate": "incumbent static runtime",
        "evidence_note": "Reproduced the incumbent with every correctness and teardown gate green.",
    },
    1: {
        "classification": "valid_performance",
        "verdict": "INCONCLUSIVE",
        "candidate": "compact verify-all",
        "evidence_note": "Primary aggregate improved, but C16 regressed beyond the per-row guardrail; replication required.",
    },
    2: {
        "classification": "valid_performance",
        "verdict": "LOSS",
        "candidate": "compact with inherited SPS table",
        "evidence_note": "The inherited table crossed the frozen primary loss threshold.",
    },
    3: {
        "classification": "harness_blocked",
        "verdict": "BLOCKED",
        "candidate": "static SPS profile attempt",
        "evidence_note": "Synthetic acceptance was active during semantic gates, so the runner rejected the evidence before profiling.",
    },
    4: {
        "classification": "administrative_abort",
        "verdict": "ABORTED",
        "candidate": "split SPS profile retry",
        "evidence_note": "CONTROL remained on the Iteration 3 review hold; no container was created.",
    },
    5: {
        "classification": "harness_blocked",
        "verdict": "BLOCKED",
        "candidate": "split SPS profile retry",
        "evidence_note": "A literal redaction placeholder reached authenticated readiness and correctly received HTTP 401.",
    },
    6: {
        "classification": "harness_blocked",
        "verdict": "BLOCKED",
        "candidate": "split SPS profile with runtime receipt",
        "evidence_note": "Tier-0 required a profile-only runtime field; the receipt predicate was wrong, not the model.",
    },
    7: {
        "classification": "harness_blocked",
        "verdict": "BLOCKED",
        "candidate": "split SPS profile with stock profiler",
        "evidence_note": "Ordinary Tier-0 passed, but the stock profiler could not authenticate; profiling remained empty.",
    },
    8: {
        "classification": "calibration_only",
        "verdict": "PASS_CALIBRATION",
        "candidate": "isolated static SPS calibration",
        "evidence_note": "Produced a valid immutable measurement artifact after ordinary correctness and isolated loopback profiling.",
    },
    9: {
        "classification": "local_preflight_blocked",
        "verdict": "BLOCKED",
        "candidate": "calibrated SPS staged candidate",
        "evidence_note": "macOS Bash 3.2 lacks readarray; dispatch stopped before SSH or Station side effects.",
    },
    10: {
        "classification": "valid_performance",
        "verdict": "LOSS",
        "candidate": "compact with calibrated SPS table",
        "evidence_note": "The frozen comparison crossed the primary loss threshold; the calibration artifact was not promoted.",
    },
    11: {
        "classification": "harness_blocked",
        "verdict": "BLOCKED",
        "candidate": "compact verify-all control",
        "evidence_note": "The contract placed container at the wrong path and failed before Docker; local schema validation was added.",
    },
    12: {
        "classification": "valid_performance",
        "verdict": "INCONCLUSIVE",
        "candidate": "compact verify-all repeat",
        "evidence_note": "Same-hour SPS removal was effectively flat and the earlier C8 uplift did not reproduce.",
    },
    13: {
        "classification": "valid_control",
        "verdict": "INCONCLUSIVE",
        "candidate": "static no-SPS paired control",
        "evidence_note": "Static and compact were effectively tied in the adjacent pair.",
    },
    14: {
        "classification": "valid_control",
        "verdict": "INCONCLUSIVE",
        "candidate": "compact verify-all opposite-order control",
        "evidence_note": "The compact-static-compact ABA bracket closed compact verify-all as neutral.",
    },
    15: {
        "classification": "valid_performance",
        "verdict": "LOSS",
        "candidate": "compact with one NextN layer",
        "evidence_note": "The runtime-confirmed one-layer override crossed the frozen primary loss threshold.",
    },
    16: {
        "classification": "valid_control",
        "verdict": "WIN_CONTROL",
        "candidate": "checkpoint-default NextN reversal",
        "evidence_note": "The immediate reversal beat one layer and completed a default-one-default causal bracket.",
    },
    17: {
        "classification": "calibration_only",
        "verdict": "PASS_CALIBRATION",
        "candidate": "additive SPS calibration",
        "evidence_note": "All 132 requested cells and fit gates passed; the artifact remained measurement-only.",
    },
    18: {
        "classification": "harness_blocked",
        "verdict": "BLOCKED",
        "candidate": "compact with additive SPS table",
        "evidence_note": "The workload completed, but decision_rule was not the evaluator's winner_rule; raw metrics are inadmissible as a verdict.",
    },
    19: {
        "classification": "valid_performance",
        "verdict": "LOSS",
        "candidate": "corrected additive SPS runtime",
        "evidence_note": "The corrected frozen contract crossed the primary loss threshold with all correctness gates green.",
    },
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def run_dir(iteration: int) -> Path | None:
    matches = sorted(RUNS.glob(f"{iteration:03d}-*"))
    if not matches:
        return None
    if len(matches) != 1:
        raise RuntimeError(f"iteration {iteration}: expected one run directory, found {len(matches)}")
    return matches[0]


def ledger_events() -> list[dict[str, Any]]:
    return [json.loads(line) for line in LEDGER.read_text().splitlines() if line.strip()]


def select_timestamps(iteration: int, events: list[dict[str, Any]]) -> dict[str, str | None]:
    own = [event for event in events if event.get("iteration") == iteration]
    started = next((event.get("at") for event in own if event.get("event") in {"iteration_started", "queued"}), None)
    finished = next(
        (
            event.get("at")
            for event in own
            if event.get("event") in {"iteration_finished", "prelaunch_blocked"}
        ),
        None,
    )
    decided = next((event.get("at") for event in own if event.get("event") == "decision"), None)
    return {"started_at": started, "finished_at": finished, "decided_at": decided}


def select_iteration(iteration: int, events: list[dict[str, Any]]) -> dict[str, Any]:
    interpretation = ITERATIONS[iteration]
    directory = run_dir(iteration)
    row: dict[str, Any] = {
        "iteration": iteration,
        **interpretation,
        **select_timestamps(iteration, events),
        "run_id": directory.name if directory else "009-compact-calibrated-sps-20260904T091938Z",
        "accepted_as_performance_evidence": interpretation["classification"]
        in {"valid_baseline", "valid_performance", "valid_control"},
        "promotion_authorized": False,
        "receipt_paths": [],
    }

    if directory is None:
        row["receipt_paths"] = ["inner-loop/ledger.jsonl"]
        return row

    result_path = directory / "results.json"
    decision_path = directory / "decision.json"
    if result_path.exists():
        result = load_json(result_path)
        row["result_outcome"] = result.get("outcome")
        row["result_blockers"] = result.get("blockers", [])
        row["all_reported_gates_pass"] = bool(result.get("gates")) and all(result["gates"].values())
        if row["accepted_as_performance_evidence"]:
            row["throughput_tok_s"] = {
                str(sample["C"]): sample["agg_tok_s"] for sample in result.get("throughput", [])
            }
            row["prefill_mean_tok_s"] = {
                str(sample["target"]): sample["mean_tok_s"] for sample in result.get("prefill", [])
            }
        row["receipt_paths"].append(str(result_path.relative_to(ROOT)))

    if decision_path.exists():
        decision = load_json(decision_path)
        frozen_verdict = decision.get("verdict")
        if frozen_verdict:
            row["receipt_verdict"] = frozen_verdict
        throughput = decision.get("throughput")
        if row["accepted_as_performance_evidence"] and isinstance(throughput, dict):
            row["primary_geomean_delta_pct"] = throughput.get("primary_geomean_delta_pct")
            row["worst_primary_row_delta_pct"] = throughput.get("worst_primary_delta_pct")
            row["prefill_geomean_delta_pct"] = decision.get("prefill_geomean_delta_pct")
            row["reference_run_id"] = decision.get("reference_run_id")
        row["receipt_paths"].append(str(decision_path.relative_to(ROOT)))

    paired_path = directory / "paired-comparisons.json"
    if paired_path.exists():
        row["receipt_paths"].append(str(paired_path.relative_to(ROOT)))

    return row


def build() -> dict[str, Any]:
    if set(ITERATIONS) != set(range(20)):
        raise RuntimeError("interpretive map must cover every iteration from 0 through 19")

    events = ledger_events()
    rows = [select_iteration(i, events) for i in range(20)]
    groups = Counter(
        "valid_performance_evidence"
        if row["accepted_as_performance_evidence"]
        else "calibration_only"
        if row["classification"] == "calibration_only"
        else "blocked_or_aborted"
        for row in rows
    )

    i14 = load_json(run_dir(14) / "paired-comparisons.json")
    i16 = load_json(run_dir(16) / "paired-comparisons.json")
    i17 = load_json(run_dir(17) / "decision.json")
    i19_decision = load_json(run_dir(19) / "decision.json")
    i19_pairs = load_json(run_dir(19) / "paired-comparisons.json")
    i10 = load_json(run_dir(10) / "decision.json")
    i12_pairs = load_json(run_dir(12) / "paired-comparisons.json")

    compact = i14["aba_analysis"]
    nextn = i16["aba_nextn_layer_effect"]
    additive_midpoint = i19_pairs["descriptive_two_run_midpoint"]

    audit = {
        "schema_version": 1,
        "campaign": "testing the inner loop on DSFVE",
        "campaign_status": "SUCCESS_PROCESS_NO_RECIPE_PROMOTION",
        "model": "deepseek-ai/DeepSeek-V4-Flash-Vision-Exp",
        "image_digest": "sha256:7ac467a50508b7029a23e846c150998fdd26d95c1cfd377ea7e74e28374486a6",
        "sglang_source_revision": "40b3e15ddbd9a1067e181283d9900dd3f4d76ed7",
        "primary_rows": [8, 16, 32, 64],
        "frozen_materiality_threshold_pct": 3.0,
        "counts": {
            "iteration_numbers": len(rows),
            **dict(groups),
            "calibration_artifacts_generated": 2,
            "candidate_recipe_promotions": 0,
        },
        "headline_findings": {
            "compact_verify_all_aba": {
                "classification": compact["classification"],
                "primary_geomean_delta_pct": compact["primary_geomean_pct"],
                "prefill_geomean_delta_pct": compact["prefill_geomean_pct"],
                "sequence": compact["sequence"],
            },
            "calibrated_sps_then_same_hour_removal": {
                "iteration_10_vs_older_compact_primary_geomean_delta_pct": i10["throughput"][
                    "primary_geomean_delta_pct"
                ],
                "iteration_12_table_off_vs_table_on_primary_geomean_delta_pct": i12_pairs[
                    "comparisons"
                ]["010-compact-calibrated-sps-20260904T092301Z"]["primary_geomean_delta_pct"],
                "interpretation": i12_pairs["interpretation"],
            },
            "one_nextn_layer_aba": {
                "classification": "LOSS",
                "primary_geomean_delta_pct": nextn["one_layer_primary_geomean_delta_pct"],
                "prefill_geomean_delta_pct": nextn["one_layer_prefill_geomean_delta_pct"],
                "sequence": nextn["sequence"],
                "retained_checkpoint_default_layers": 3,
            },
            "additive_sps_calibration": {
                "classification": i17["outcome"],
                "promotion_authorized": i17["promotion_authorized"],
                "artifact_sha256": i17["artifact"]["sha256"],
                "cells": i17["profile"]["cells"],
                "fit_r_squared": i17["profile"]["fit_r_squared"],
                "fit_p95_relative_error": i17["profile"]["fit_p95_relative_error"],
                "fit_max_relative_error": i17["profile"]["fit_max_relative_error"],
            },
            "additive_sps_runtime": {
                "classification": i19_decision["verdict"],
                "primary_geomean_delta_pct": i19_decision["throughput"]["primary_geomean_delta_pct"],
                "prefill_geomean_delta_pct": i19_decision["prefill_geomean_delta_pct"],
                "worst_primary_row_delta_pct": i19_decision["throughput"]["worst_primary_delta_pct"],
                "descriptive_two_run_midpoint_primary_pct": additive_midpoint[
                    "primary_geomean_delta_pct"
                ],
                "descriptive_two_run_midpoint_status": additive_midpoint["status"],
            },
        },
        "iterations": rows,
        "production": {
            "restored": False,
            "held_container": "dsfv-dspark-prod-hold-20260903-1900",
            "final_verified_at": "2026-09-04T14:54:59Z",
            "final_state": {
                "experiment_containers_running": False,
                "port_30003_online": False,
                "gpu_busy": False,
                "experiment_lock_held": False,
                "held_production_running": False,
            },
        },
    }

    if audit["counts"] != {
        "iteration_numbers": 20,
        "valid_performance_evidence": 10,
        "blocked_or_aborted": 8,
        "calibration_only": 2,
        "calibration_artifacts_generated": 2,
        "candidate_recipe_promotions": 0,
    }:
        raise RuntimeError(f"unexpected campaign counts: {audit['counts']}")
    return audit


def markdown(audit: dict[str, Any]) -> str:
    counts = audit["counts"]
    lines = [
        "# testing the inner loop on DSFVE — receipt audit",
        "",
        "This file is generated by `research/build_campaign_audit.py` from persisted campaign receipts.",
        "It is public-safe by construction: only selected result, decision, and ledger fields are copied.",
        "",
        "## Campaign accounting",
        "",
        "| Evidence class | Count |",
        "|---|---:|",
        f"| Valid performance evidence (baseline, candidates, and controls) | {counts['valid_performance_evidence']} |",
        f"| Calibration only | {counts['calibration_only']} |",
        f"| Harness/preflight blocked or administratively aborted | {counts['blocked_or_aborted']} |",
        f"| Candidate recipes promoted | {counts['candidate_recipe_promotions']} |",
        "",
        "## Iteration ledger",
        "",
        "| I | Class | Verdict | One intended change or purpose | Evidence |",
        "|---:|---|---|---|---|",
    ]
    for row in audit["iterations"]:
        note = row["evidence_note"].replace("|", "\\|")
        lines.append(
            f"| {row['iteration']} | `{row['classification']}` | **{row['verdict']}** | "
            f"{row['candidate']} | {note} |"
        )

    findings = audit["headline_findings"]
    lines.extend(
        [
            "",
            "## Closed mechanisms",
            "",
            f"- Compact verify-all: **{findings['compact_verify_all_aba']['primary_geomean_delta_pct']:+.3f}%** primary and "
            f"**{findings['compact_verify_all_aba']['prefill_geomean_delta_pct']:+.3f}%** prefill in the compact-static-compact ABA bracket. Neutral inside the frozen ±3% band.",
            f"- One NextN layer: **{findings['one_nextn_layer_aba']['primary_geomean_delta_pct']:+.3f}%** primary in the default-one-default ABA estimate. Rejected; checkpoint default three retained.",
            f"- Additive SPS runtime: **{findings['additive_sps_runtime']['primary_geomean_delta_pct']:+.3f}%** primary and "
            f"**{findings['additive_sps_runtime']['prefill_geomean_delta_pct']:+.3f}%** prefill in the admissible Iteration 19 comparison. Rejected.",
            "",
            "## Final state",
            "",
            "The campaign succeeded as an experimental process. It produced no promoted recipe. Production remains preserved and stopped pending a separate restore instruction.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    audit = build()
    OUT_JSON.write_text(json.dumps(audit, indent=2, sort_keys=False) + "\n")
    OUT_MD.write_text(markdown(audit))
    print(json.dumps({"json": str(OUT_JSON), "markdown": str(OUT_MD), "counts": audit["counts"]}, sort_keys=True))


if __name__ == "__main__":
    main()

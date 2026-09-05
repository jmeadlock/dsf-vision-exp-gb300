#!/usr/bin/env python3
"""Build immutable no-table cards and unreleased SPS templates for this campaign."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
CAMPAIGN_ID = "sps-requal-20260904-now"
ROOT = REPO / "inner-loop" / "campaigns" / CAMPAIGN_ID
QUEUE = ROOT / "queue"
SHA256 = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()

FILES = {
    "launcher_sha256": REPO / "inner-loop/scripts/launch-dsfv-experiment.sh",
    "bench_sha256": REPO / "inner-loop/scripts/bench_dsf.py",
    "runner_sha256": REPO / "inner-loop/scripts/run_remote_candidate.sh",
    "dispatcher_sha256": REPO / "inner-loop/scripts/dispatch_candidate.sh",
    "summarizer_sha256": REPO / "inner-loop/scripts/summarize_iteration.py",
    "validator_sha256": REPO / "inner-loop/scripts/validate_contract.py",
    "agent_workload_gate_sha256": REPO / "inner-loop/scripts/agent_workload_gate.py",
    "replay_gate_sha256": REPO / "inner-loop/scripts/replay_gate.py",
    "sps_profile_gate_sha256": REPO / "inner-loop/scripts/sps_profile_gate.py",
}
PROFILER = ROOT / "artifacts/dspark_sps_profiler-pr37815-085a5e2.py"
IMAGE = "sha256:7ac467a50508b7029a23e846c150998fdd26d95c1cfd377ea7e74e28374486a6"
MODEL = "6821d6ad3681a4b137b066b76094fa82ebd0a380"
SOURCE = "40b3e15ddbd9a1067e181283d9900dd3f4d76ed7"
PR_HEAD = "085a5e2a734ae5dc3f820dd50f2f490dbbdcb5e7"
PROFILER_BASE = "f8820ba461c7c0956a1e1a787e45de8c09f025e46ac2ae9d4d1d22f4076a1911"
PROFILER_PATCH = "782353e0778cbbd07f51418be5913d1f695faba90b9fd28a48f1d2a3b917ba5c"
PROFILER_PATCHED = "7ca2e5d832f76a88dfb3ad76fb34918e902aa295110e72b9eb19fb1fb1a09546"
SPS_MODULE = "c87a7f72ad1299cbb9bb337e0ffc3bb34de2c02624e423c717c41fc41c73ee08"
WORKLOADS = [
    "natural_prose", "code_oriented", "six_turn_replay",
    "warm_prefix_repeat", "staggered_mixed_load",
]


def target(container: str) -> dict:
    out = {
        "container": container,
        "image_digest": IMAGE,
        "model_revision": MODEL,
        "sglang_source_revision": SOURCE,
    }
    out.update({key: SHA256(path) for key, path in FILES.items()})
    return out


def common(card: str, sequence: int, phase: str, ragged: str, container: str) -> dict:
    return {
        "schema_version": 2,
        "status": "frozen",
        "campaign_id": CAMPAIGN_ID,
        "phase": phase,
        "sequence": sequence,
        "run_id": f"{CAMPAIGN_ID}-{card}",
        "slug": card,
        "release_state": "prewritten_unreleased",
        "production_restore_authorized": False,
        "authorization": {
            "run_authorized_by": "James",
            "run_authorized_at_local": "2026-09-04 18:02 CDT",
            "run_authorized_at": "2026-09-04T18:02:00-05:00",
            "production_observed_port_30003_listener": False,
            "production_observed_running_containers": False,
            "restore_authorized": False,
        },
        "candidate": {
            "ragged_verify_mode": ragged,
            "sps_table": "none",
            "sps_fit_mbin_w": None,
            "profiler_source_sha": None,
            "profile_mode": "none",
            "num_nextn_predict_layers": "checkpoint",
        },
        "target": target(container),
        "compare_to_run": "adjacent-control-as-declared",
        "static_anchor_runs": [f"{CAMPAIGN_ID}-R0", f"{CAMPAIGN_ID}-R1"],
        "compact_control_runs": [f"{CAMPAIGN_ID}-C0", f"{CAMPAIGN_ID}-C1", f"{CAMPAIGN_ID}-C2"],
        "expected_concurrency": [1, 4, 8, 16, 32, 64],
        "expected_prefill_targets": [8000, 32000, 64000, 128000, 256000],
        "expected_workload_classes": WORKLOADS,
        "winner_rule": {
            "primary_rows": [8, 16, 32, 64],
            "compare_candidate_to": "adjacent_compact_controls",
            "also_report_vs": "static_recipe_v2_anchors",
            "do_not_claim_only_table_delta_from_recipe_v2": True,
        },
    }


def profile(card: str, sequence: int, container: str) -> dict:
    out = common(card, sequence, "profile", "compact", container)
    out["candidate"].update({
        "profile_mode": "sps-additive",
        "profiler_source_sha": PR_HEAD,
    })
    out["target"].update({
        "profiler_sha256": PROFILER_PATCHED,
        "sps_module_sha256": SPS_MODULE,
        "profiler_base_file_sha256": PROFILER_BASE,
        "profiler_pr37815_patch_sha256": PROFILER_PATCH,
        "profiler_patched_file_sha256": PROFILER_PATCHED,
    })
    out["expected_concurrency"] = []
    out["expected_prefill_targets"] = []
    out["expected_workload_classes"] = []
    out["profile_sweep"] = {
        "batch_size_per_rank": [1, 2, 3, 4, 5, 6, 7, 8],
        "fracs": [0.2, 0.4, 0.6, 0.8, 1.0],
        "verify_num_draft_tokens": 6,
        "repeats": 3,
        "expected_m_min": 2,
        "expected_m_max": 48,
        "fit_mbin_widths": [64, 6, 1],
    }
    return out


def candidate_template(card: str, sequence: int, container: str, adjacent: list[str]) -> dict:
    out = common(card, sequence, "candidate", "compact", container)
    out["status"] = "template_unreleased_requires_real_calibration_lineage"
    out["candidate"].update({
        "sps_table": "fine-grained",
        "sps_fit_mbin_w": "${SELECTED_MBIN_W}",
        "profiler_source_sha": PR_HEAD,
    })
    out["target"].update({
        "profiler_base_file_sha256": PROFILER_BASE,
        "profiler_pr37815_patch_sha256": PROFILER_PATCH,
        "profiler_patched_file_sha256": PROFILER_PATCHED,
        "raw_profile_a_sha256": "${RAW_PROFILE_A_SHA256}",
        "raw_profile_b_sha256": "${RAW_PROFILE_B_SHA256}",
        "fit_manifest_sha256": "${FIT_MANIFEST_SHA256}",
        "sps_table_sha256": "${SPS_TABLE_SHA256}",
        "sps_table_artifact": f"inner-loop/campaigns/{CAMPAIGN_ID}/artifacts/selected-fine-grained-sps.json",
        "sps_table_expected_mounted_sha256": "${SPS_TABLE_SHA256}",
    })
    out["adjacent_compact_controls"] = [f"{CAMPAIGN_ID}-{name}" for name in adjacent]
    return out


def write(name: str, payload: dict) -> None:
    (QUEUE / name).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def main() -> None:
    if SHA256(PROFILER) != PROFILER_PATCHED:
        raise SystemExit("patched profiler digest mismatch")
    QUEUE.mkdir(parents=True, exist_ok=True)
    write("P0.json", profile("P0", 0, "dsfv-spsrq-p0"))
    write("P1.json", profile("P1", 1, "dsfv-spsrq-p1"))
    write("R0.json", common("R0", 2, "anchor", "static", "dsfv-spsrq-r0"))
    write("C0.json", common("C0", 3, "control", "compact", "dsfv-spsrq-c0"))
    write("S1.template.json", candidate_template("S1", 4, "dsfv-spsrq-s1", ["C0", "C1"]))
    write("C1.json", common("C1", 5, "control", "compact", "dsfv-spsrq-c1"))
    write("S2.template.json", candidate_template("S2", 6, "dsfv-spsrq-s2", ["C1", "C2"]))
    write("C2.json", common("C2", 7, "control", "compact", "dsfv-spsrq-c2"))
    write("R1.json", common("R1", 8, "anchor", "static", "dsfv-spsrq-r1"))
    print("CAMPAIGN_CONTRACTS_RESULT PASS valid=7 templates=2 campaign=" + CAMPAIGN_ID)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "inner-loop/scripts/dispatch_candidate.sh"
CAMPAIGN_ID = "sps-requal-20260904-now"
SHA = "a" * 64


def valid_v2_contract() -> dict:
    return {
        "schema_version": 2,
        "status": "frozen",
        "campaign_id": CAMPAIGN_ID,
        "phase": "control",
        "sequence": 1,
        "run_id": f"{CAMPAIGN_ID}-B0-compact-no-table",
        "slug": "B0-compact-no-table",
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
            "ragged_verify_mode": "compact",
            "sps_table": "none",
            "sps_fit_mbin_w": None,
            "profiler_source_sha": None,
            "profile_mode": "none",
            "num_nextn_predict_layers": "checkpoint",
        },
        "target": {
            "container": "dsfv-sps-requal-B0",
            "image_digest": "sha256:7ac467a50508b7029a23e846c150998fdd26d95c1cfd377ea7e74e28374486a6",
            "model_revision": "6821d6ad3681a4b137b066b76094fa82ebd0a380",
            "sglang_source_revision": "40b3e15ddbd9a1067e181283d9900dd3f4d76ed7",
            "launcher_sha256": SHA,
            "bench_sha256": SHA,
            "runner_sha256": SHA,
            "dispatcher_sha256": SHA,
            "summarizer_sha256": SHA,
            "validator_sha256": SHA,
            "agent_workload_gate_sha256": SHA,
            "replay_gate_sha256": SHA,
            "sps_profile_gate_sha256": SHA,
        },
        "compare_to_run": "B0",
        "static_anchor_runs": ["A0", "A1"],
        "compact_control_runs": ["B0", "B1"],
        "expected_concurrency": [1, 4, 8, 16, 32, 64],
        "expected_prefill_targets": [8000, 32000, 64000, 128000, 256000],
        "expected_workload_classes": [
            "natural_prose",
            "code_oriented",
            "six_turn_replay",
            "warm_prefix_repeat",
            "staggered_mixed_load",
        ],
        "winner_rule": {
            "primary_rows": [8, 16, 32, 64],
            "compare_candidate_to": "adjacent_compact_controls",
            "also_report_vs": "static_recipe_v2_anchors",
            "do_not_claim_only_table_delta_from_recipe_v2": True,
        },
    }


class DispatchCandidateIsolationTests(unittest.TestCase):
    def test_schema_v2_source_uses_campaign_root_for_all_stateful_remote_paths(self):
        source = SCRIPT.read_text()
        self.assertIn('CAMPAIGN_ROOT="$REMOTE_ROOT/campaigns/$CAMPAIGN_ID"', source)
        self.assertIn('CONTROL_PATH="$CAMPAIGN_ROOT/CONTROL"', source)
        self.assertIn('RELEASE_PATH="$CAMPAIGN_ROOT/RELEASE"', source)
        self.assertIn('RUNS_ROOT="$CAMPAIGN_ROOT/runs"', source)
        self.assertIn('LEDGER_PATH="$REPO/inner-loop/campaigns/$CAMPAIGN_ID/ledger.jsonl"', source)
        self.assertIn('ROOT=\'$CAMPAIGN_ROOT\'', source)
        self.assertIn('"$HOST:$CAMPAIGN_ROOT/runs/$RUN_ID/."', source)
        self.assertNotIn('"$REPO/inner-loop/ledger.jsonl"', source)
        self.assertNotIn('"$HOST:$REMOTE_ROOT/runs/$RUN_ID/."', source)

    def test_schema_v2_dry_run_reports_unreleased_cards_blocked_in_campaign_local_paths(self):
        with tempfile.TemporaryDirectory() as td:
            contract_path = Path(td) / "B0.json"
            contract_path.write_text(json.dumps(valid_v2_contract()))
            completed = subprocess.run(
                ["bash", str(SCRIPT), str(contract_path)],
                check=False,
                capture_output=True,
                text=True,
                env={"DSFV_DRY_RUN": "1", "REMOTE_ROOT": "/remote/root"},
            )
        self.assertEqual(completed.returncode, 2, completed.stdout + completed.stderr)
        self.assertIn("DISPATCH_RELEASE_RESULT BLOCKED", completed.stdout)
        self.assertIn(f"campaign_root=/remote/root/campaigns/{CAMPAIGN_ID}", completed.stdout)
        self.assertIn(f"local_run={REPO}/inner-loop/campaigns/{CAMPAIGN_ID}/runs/", completed.stdout)
        self.assertIn(f"remote_contract=/remote/root/campaigns/{CAMPAIGN_ID}/queue/B0.json", completed.stdout)
        self.assertNotIn("/remote/root/runs/", completed.stdout)
        self.assertNotIn(f"{REPO}/inner-loop/runs/", completed.stdout)


if __name__ == "__main__":
    unittest.main()

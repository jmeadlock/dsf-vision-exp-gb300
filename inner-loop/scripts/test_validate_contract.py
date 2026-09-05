#!/usr/bin/env python3
from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
SPEC = importlib.util.spec_from_file_location("validate_contract", HERE / "validate_contract.py")
MOD = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MOD)

SHA256 = "a" * 64
PR_HEAD = "085a5e2a734ae5dc3f820dd50f2f490dbbdcb5e7"
CAMPAIGN_ID = "sps-requal-20260904-now"


def base_target() -> dict:
    return {
        "container": "dsfv-sps-requal-B0",
        "image_digest": "sha256:7ac467a50508b7029a23e846c150998fdd26d95c1cfd377ea7e74e28374486a6",
        "model_revision": "6821d6ad3681a4b137b066b76094fa82ebd0a380",
        "sglang_source_revision": "40b3e15ddbd9a1067e181283d9900dd3f4d76ed7",
        "launcher_sha256": SHA256,
        "bench_sha256": SHA256,
        "runner_sha256": SHA256,
        "dispatcher_sha256": SHA256,
        "summarizer_sha256": SHA256,
        "validator_sha256": SHA256,
        "agent_workload_gate_sha256": SHA256,
        "replay_gate_sha256": SHA256,
        "profiler_sha256": SHA256,
        "sps_module_sha256": SHA256,
    }


def v1_contract() -> dict:
    return {
        "schema_version": 1,
        "status": "frozen",
        "iteration": 10,
        "run_id": "010-compact-calibrated-sps",
        "slug": "compact-calibrated-sps",
        "production_restore_authorized": False,
        "candidate": {
            "ragged_verify_mode": "compact",
            "sps_table": "calibrated",
            "profile_mode": "none",
            "num_nextn_predict_layers": "checkpoint",
        },
        "target": {
            **base_target(),
            "sps_table_sha256": SHA256,
            "sps_table_artifact": "inner-loop/artifacts/calibrated.json",
        },
        "compare_to_run": "009-reference",
        "expected_concurrency": [1, 4, 8, 16, 32, 64],
        "expected_prefill_targets": [8000, 32000, 64000, 128000, 256000],
        "winner_rule": {"primary_rows": [8, 16, 32, 64]},
    }


def v2_contract(phase: str = "control", sps_table: str = "none") -> dict:
    ragged = "static" if phase == "anchor" else "compact"
    c = {
        "schema_version": 2,
        "status": "frozen",
        "campaign_id": CAMPAIGN_ID,
        "phase": phase,
        "sequence": 0,
        "run_id": f"{CAMPAIGN_ID}-{phase}-000",
        "slug": f"{phase}-fixture",
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
            "sps_table": sps_table,
            "sps_fit_mbin_w": None,
            "profiler_source_sha": None,
            "profile_mode": "sps-additive" if phase == "profile" else "none",
            "num_nextn_predict_layers": "checkpoint",
        },
        "target": base_target(),
        "compare_to_run": "compact-control-adjacent",
        "static_anchor_runs": ["A0-static-recipe-v2", "A1-static-recipe-v2"],
        "compact_control_runs": ["B0-compact-no-table", "B1-compact-no-table"],
        "expected_concurrency": [] if phase == "profile" else [1, 4, 8, 16, 32, 64],
        "expected_prefill_targets": [] if phase == "profile" else [8000, 32000, 64000, 128000, 256000],
        "expected_workload_classes": [] if phase == "profile" else [
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
    if phase == "profile":
        c["candidate"]["profiler_source_sha"] = PR_HEAD
        c["target"].update({
            "profiler_sha256": "2" * 64,
            "sps_module_sha256": "3" * 64,
            "profiler_base_file_sha256": "b" * 64,
            "profiler_pr37815_patch_sha256": "1" * 64,
            "profiler_patched_file_sha256": "2" * 64,
        })
        c["profile_sweep"] = {
            "batch_size_per_rank": [1, 2, 3, 4, 5, 6, 7, 8],
            "fracs": [0.2, 0.4, 0.6, 0.8, 1.0],
            "verify_num_draft_tokens": 6,
            "repeats": 3,
            "expected_m_min": 2,
            "expected_m_max": 48,
            "fit_mbin_widths": [64, 6, 1],
        }
    if sps_table == "fine-grained":
        c["candidate"].update({
            "ragged_verify_mode": "compact",
            "sps_fit_mbin_w": 6,
            "profiler_source_sha": PR_HEAD,
        })
        c["target"].update({
            "profiler_base_file_sha256": "b" * 64,
            "profiler_pr37815_patch_sha256": "1" * 64,
            "profiler_patched_file_sha256": "2" * 64,
            "raw_profile_a_sha256": "c" * 64,
            "raw_profile_b_sha256": "d" * 64,
            "fit_manifest_sha256": "e" * 64,
            "sps_table_sha256": "f" * 64,
            "sps_table_artifact": f"inner-loop/campaigns/{CAMPAIGN_ID}/artifacts/selected-fine-grained-sps.json",
            "sps_table_expected_mounted_sha256": "f" * 64,
        })
    return c


class ContractValidationTests(unittest.TestCase):
    def test_legacy_schema_v1_contract_still_passes(self):
        MOD.validate_contract(v1_contract())

    def test_schema_v2_compact_control_contract_passes_and_cli_reports_campaign(self):
        contract = v2_contract("control", "none")
        MOD.validate_contract(contract)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "B0.json"
            path.write_text(json.dumps(contract))
            completed = subprocess.run(
                ["python3", str(HERE / "validate_contract.py"), str(path)],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn(f"campaign_id={CAMPAIGN_ID}", completed.stdout)
        self.assertIn("phase=control", completed.stdout)

    def test_schema_v2_rejects_fine_grained_sps_under_static_ragged(self):
        malformed = v2_contract("candidate", "fine-grained")
        malformed["candidate"]["ragged_verify_mode"] = "static"
        with self.assertRaisesRegex(MOD.ContractError, "fine-grained SPS requires compact"):
            MOD.validate_contract(malformed)

    def test_schema_v2_fine_grained_table_requires_full_lineage(self):
        valid = v2_contract("candidate", "fine-grained")
        MOD.validate_contract(valid)
        required = [
            "profiler_base_file_sha256",
            "profiler_pr37815_patch_sha256",
            "profiler_patched_file_sha256",
            "raw_profile_a_sha256",
            "raw_profile_b_sha256",
            "fit_manifest_sha256",
            "sps_table_sha256",
            "sps_table_artifact",
            "sps_table_expected_mounted_sha256",
        ]
        for key in required:
            malformed = copy.deepcopy(valid)
            malformed["target"].pop(key)
            with self.subTest(key=key):
                with self.assertRaisesRegex(MOD.ContractError, key):
                    MOD.validate_contract(malformed)

    def test_schema_v2_table_path_is_campaign_local_and_digest_matches_mount(self):
        for bad_path in (
            "inner-loop/artifacts/selected.json",
            f"inner-loop/campaigns/{CAMPAIGN_ID}/artifacts/../selected.json",
            f"inner-loop/campaigns/other-campaign/artifacts/selected.json",
            f"inner-loop/campaigns/{CAMPAIGN_ID}/artifacts/selected.txt",
        ):
            malformed = v2_contract("candidate", "fine-grained")
            malformed["target"]["sps_table_artifact"] = bad_path
            with self.subTest(path=bad_path):
                with self.assertRaisesRegex(MOD.ContractError, "campaign-local"):
                    MOD.validate_contract(malformed)
        malformed = v2_contract("candidate", "fine-grained")
        malformed["target"]["sps_table_expected_mounted_sha256"] = "0" * 64
        with self.assertRaisesRegex(MOD.ContractError, "mounted SHA"):
            MOD.validate_contract(malformed)

    def test_schema_v2_profile_sweep_is_exact_and_predeclares_fit_widths(self):
        valid = v2_contract("profile", "none")
        MOD.validate_contract(valid)
        malformed = copy.deepcopy(valid)
        malformed["profile_sweep"]["batch_size_per_rank"] = [1, 2, 4, 8]
        with self.assertRaisesRegex(MOD.ContractError, "profile_sweep.batch_size_per_rank"):
            MOD.validate_contract(malformed)
        malformed = copy.deepcopy(valid)
        malformed["profile_sweep"]["fit_mbin_widths"] = [1, 6, 64]
        with self.assertRaisesRegex(MOD.ContractError, "fit_mbin_widths"):
            MOD.validate_contract(malformed)

    def test_schema_v2_rejects_runtime_shell_fragments_and_restore_contracts(self):
        for key in ("runtime_args", "extra_args", "shell", "command", "restore_command"):
            malformed = v2_contract("control", "none")
            malformed["target"][key] = "$(touch /tmp/nope)"
            with self.subTest(key=key):
                with self.assertRaisesRegex(MOD.ContractError, "free-form runtime"):
                    MOD.validate_contract(malformed)
        malformed = v2_contract("control", "none")
        malformed["production_restore_authorized"] = True
        with self.assertRaisesRegex(MOD.ContractError, "production_restore_authorized must be false"):
            MOD.validate_contract(malformed)
        malformed = v2_contract("restore", "none")
        with self.assertRaisesRegex(MOD.ContractError, "phase"):
            MOD.validate_contract(malformed)


if __name__ == "__main__":
    unittest.main()

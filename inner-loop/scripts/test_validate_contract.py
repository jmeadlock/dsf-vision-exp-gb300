#!/usr/bin/env python3
from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
SPEC = importlib.util.spec_from_file_location("validate_contract", HERE / "validate_contract.py")
MOD = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MOD)


class ContractValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.valid = json.loads(
            (REPO / "inner-loop/queue/010-compact-calibrated-sps.json").read_text()
        )
        # Historical Iteration 10 predates the explicit restore-authorization
        # schema field; enrich the test copy rather than mutating the receipt.
        cls.valid["production_restore_authorized"] = False
        MOD.validate_contract(cls.valid)

    def test_known_good_calibrated_contract_passes(self):
        MOD.validate_contract(copy.deepcopy(self.valid))

    def test_iteration_11_shape_fails_before_dispatch(self):
        malformed = copy.deepcopy(self.valid)
        malformed["target"].pop("container")
        malformed["container"] = "wrong-level"
        with self.assertRaisesRegex(MOD.ContractError, r"target\.container missing"):
            MOD.validate_contract(malformed)

    def test_iteration_11_cli_returns_blocked_receipt(self):
        completed = subprocess.run(
            [
                "python3",
                str(HERE / "validate_contract.py"),
                str(REPO / "inner-loop/queue/011-compact-verify-all-repeat.json"),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn(
            "CONTRACT_SCHEMA_RESULT BLOCKED reason=target.container missing",
            completed.stdout,
        )

    def test_calibrated_mode_requires_artifact(self):
        malformed = copy.deepcopy(self.valid)
        malformed["target"].pop("sps_table_artifact")
        with self.assertRaisesRegex(MOD.ContractError, "sps_table_artifact"):
            MOD.validate_contract(malformed)

    def test_artifact_cannot_escape_repository(self):
        malformed = copy.deepcopy(self.valid)
        malformed["target"]["sps_table_artifact"] = "inner-loop/artifacts/../../secret.json"
        with self.assertRaisesRegex(MOD.ContractError, "inside the repository"):
            MOD.validate_contract(malformed)

    def test_none_mode_does_not_require_sps_fields(self):
        valid = copy.deepcopy(self.valid)
        valid["candidate"]["sps_table"] = "none"
        valid["target"].pop("sps_table_artifact")
        valid["target"].pop("sps_table_sha256")
        MOD.validate_contract(valid)

    def test_nonprofile_contract_requires_root_benchmark_rows(self):
        malformed = copy.deepcopy(self.valid)
        malformed.pop("expected_concurrency")
        malformed["benchmark"] = {"throughput": {"concurrency": [1, 4, 8, 16, 32, 64]}}
        with self.assertRaisesRegex(MOD.ContractError, "expected_concurrency"):
            MOD.validate_contract(malformed)

    def test_profile_mode_uses_runner_field_name(self):
        malformed = copy.deepcopy(self.valid)
        malformed["candidate"]["profile_mode"] = "calibrate"
        with self.assertRaisesRegex(
            MOD.ContractError, "profile_mode must be none, sps, or sps-additive"
        ):
            MOD.validate_contract(malformed)

    def test_additive_profile_contract_requires_compact_mode(self):
        valid = copy.deepcopy(self.valid)
        valid["candidate"].update({
            "profile_mode": "sps-additive",
            "ragged_verify_mode": "compact",
            "sps_table": "none",
            "num_nextn_predict_layers": "checkpoint",
        })
        valid["target"].pop("sps_table_artifact", None)
        valid["target"].pop("sps_table_sha256", None)
        valid["target"].setdefault("profiler_sha256", "a" * 64)
        valid["target"].setdefault("sps_module_sha256", "b" * 64)
        MOD.validate_contract(valid)

        malformed = copy.deepcopy(valid)
        malformed["candidate"]["ragged_verify_mode"] = "static"
        with self.assertRaisesRegex(MOD.ContractError, "additive SPS profiling"):
            MOD.validate_contract(malformed)

    def test_profile_contract_requires_unmounted_table_and_source_hashes(self):
        malformed = copy.deepcopy(self.valid)
        malformed["candidate"].update({
            "profile_mode": "sps",
            "ragged_verify_mode": "static",
            "sps_table": "current",
        })
        with self.assertRaisesRegex(MOD.ContractError, "sps_table=none"):
            MOD.validate_contract(malformed)

        missing_hash = copy.deepcopy(self.valid)
        missing_hash["candidate"].update({
            "profile_mode": "sps",
            "ragged_verify_mode": "static",
            "sps_table": "none",
        })
        missing_hash["target"].pop("sps_table_artifact", None)
        missing_hash["target"].pop("sps_table_sha256", None)
        missing_hash["target"].pop("profiler_sha256", None)
        with self.assertRaisesRegex(MOD.ContractError, "profiler_sha256"):
            MOD.validate_contract(missing_hash)

    def test_nextn_one_is_valid_for_normal_runtime(self):
        valid = copy.deepcopy(self.valid)
        valid["candidate"]["profile_mode"] = "none"
        valid["candidate"]["num_nextn_predict_layers"] = 1
        MOD.validate_contract(valid)

    def test_nextn_checkpoint_is_valid(self):
        valid = copy.deepcopy(self.valid)
        valid["candidate"]["num_nextn_predict_layers"] = "checkpoint"
        MOD.validate_contract(valid)

    def test_nextn_rejects_noncanonical_values(self):
        for invalid in (True, 1.0, "1", 0, 2, None):
            with self.subTest(invalid=invalid):
                malformed = copy.deepcopy(self.valid)
                malformed["candidate"]["num_nextn_predict_layers"] = invalid
                with self.assertRaisesRegex(
                    MOD.ContractError, "num_nextn_predict_layers"
                ):
                    MOD.validate_contract(malformed)

    def test_nextn_override_is_forbidden_during_sps_profile(self):
        malformed = copy.deepcopy(self.valid)
        malformed["candidate"].update({
            "profile_mode": "sps",
            "ragged_verify_mode": "static",
            "sps_table": "none",
            "num_nextn_predict_layers": 1,
        })
        malformed["target"].pop("sps_table_artifact", None)
        malformed["target"].pop("sps_table_sha256", None)
        malformed["target"].setdefault("profiler_sha256", "a" * 64)
        malformed["target"].setdefault("sps_module_sha256", "b" * 64)
        with self.assertRaisesRegex(MOD.ContractError, "checkpoint NextN layers"):
            MOD.validate_contract(malformed)


    def test_missing_winner_rule_is_blocked_before_dispatch(self):
        malformed = copy.deepcopy(self.valid)
        malformed.pop("winner_rule")
        malformed["decision_rule"] = {
            "primary_rows": [8, 16, 32, 64]
        }
        with self.assertRaisesRegex(MOD.ContractError, "winner_rule missing"):
            MOD.validate_contract(malformed)

    def test_winner_primary_rows_must_be_unique_integer_benchmark_rows(self):
        for rows in ([8, 16, 16, 64], [8, True, 32, 64], [8, 16, 32, 128], []):
            with self.subTest(rows=rows):
                malformed = copy.deepcopy(self.valid)
                malformed["winner_rule"]["primary_rows"] = rows
                with self.assertRaisesRegex(MOD.ContractError, "winner_rule.primary_rows"):
                    MOD.validate_contract(malformed)


if __name__ == "__main__":
    unittest.main()

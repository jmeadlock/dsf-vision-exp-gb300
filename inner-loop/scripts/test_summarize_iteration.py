#!/usr/bin/env python3
import json
import tempfile
import unittest
from pathlib import Path

from summarize_iteration import summarize


class SummarizeIterationTests(unittest.TestCase):
    def test_complete_baseline(self):
        with tempfile.TemporaryDirectory() as td:
            run = Path(td)
            (run / "contract.json").write_text(json.dumps({
                "iteration": 0,
                "slug": "incumbent-null",
                "target": {"validator_sha256": "a" * 64},
                "expected_concurrency": [1, 4, 8, 16, 32, 64],
                "expected_prefill_targets": [8000, 32000, 64000, 128000, 256000],
            }))
            rows = [
                {"C": c, "n": 3 * c, "agg_tok_s": 100.0 * c,
                 "per_stream_tok_s": 100.0, "mean_ttft_s": 0.2,
                 "ptok": 7000, "ctok_total": 3072 * c,
                 "wall_s": 1.0, "accept_len": 3.7}
                for c in [1, 4, 8, 16, 32, 64]
            ]
            (run / "throughput.txt").write_text("\n".join("BENCH " + json.dumps(r) for r in rows))
            (run / "prefill.txt").write_text("\n".join(
                f"PREFILL target={n} prompt_tokens={n-10} ttft_s=[1.0, 1.1, 0.9] tok_s=[10000, 9090, 11100] mean_tok_s=10063"
                for n in [8000, 32000, 64000, 128000, 256000]
            ))
            (run / "smoke.txt").write_text("PASS models\nSMOKE_RESULT PASS\n")
            (run / "replay.txt").write_text("REPLAY_RESULT PASS turns=6\n")
            (run / "opaque.txt").write_text("OPAQUE_RESULT PASS n=64 mismatches=0\n")
            (run / "repaudit.txt").write_text("REPAUDIT C=64 N=128 flagged=0 rate=0.0% agg_out_tok_s=1800 mean_ptok=7000 mean_ctok=900 finish=Counter({'stop': 128}) worst_8gram=0.003\n")
            (run / "patch.txt").write_text("PATCH_RESULT PASS count=1\n")
            (run / "runtime-mode.txt").write_text("RUNTIME_MODE_RESULT PASS mode=static sps_effective=false\n")
            (run / "health.txt").write_text("HEALTH_RESULT PASS\n")
            (run / "gpu-errors.txt").write_text("GPU_ERROR_RESULT PASS\n")
            (run / "dispatch.txt").write_text(
                "CONTRACT_SCHEMA_RESULT PASS iteration=0 run_id=fixture\n"
                "STAGE_HASH_RESULT PASS files=10\n"
                "FROZEN_HASH_RESULT PASS\n"
            )
            (run / "preflight.txt").write_text("PREFLIGHT_RESULT PASS\n")
            (run / "receipt-sanitize.txt").write_text("RECEIPT_SANITIZE_RESULT PASS\n")
            (run / "stop.txt").write_text("STOP_RESULT PASS\nPORT_AFTER_STOP PASS\n")
            result = summarize(run)
            self.assertEqual(result["outcome"], "BASELINE")
            self.assertEqual([x["C"] for x in result["throughput"]], [1, 4, 8, 16, 32, 64])
            self.assertEqual(len(result["prefill"]), 5)
            self.assertTrue(all(result["gates"].values()))

            (run / "dispatch.txt").write_text(
                "STAGE_HASH_RESULT PASS files=10\nFROZEN_HASH_RESULT PASS\n"
            )
            result = summarize(run)
            self.assertEqual(result["outcome"], "BLOCKED")
            self.assertFalse(result["gates"]["contract_schema"])

    def test_complete_split_sps_calibration(self):
        with tempfile.TemporaryDirectory() as td:
            run = Path(td)
            (run / "contract.json").write_text(json.dumps({
                "iteration": 4,
                "slug": "sps-profile-static-split",
                "candidate": {"profile_mode": "sps"},
                "target": {
                    "container": "dsfv-iter004-profile",
                    "runner_sha256": "fixture",
                },
                "expected_concurrency": [],
                "expected_prefill_targets": [],
            }))
            receipts = {
                "dispatch.txt": "STAGE_HASH_RESULT PASS files=10\nFROZEN_HASH_RESULT PASS\n",
                "preflight.txt": "RUNNER_HASH_RESULT PASS\nPREFLIGHT_RESULT PASS\n",
                "smoke.txt": "SMOKE_RESULT PASS\n",
                "replay.txt": "REPLAY_RESULT PASS turns=6\n",
                "opaque.txt": "OPAQUE_RESULT PASS n=64 mismatches=0\n",
                "repaudit.txt": "REPAUDIT C=64 N=128 flagged=0 rate=0.0%\n",
                "patch.txt": "PATCH_RESULT PASS count=1\n",
                "patch-tier0.txt": "PATCH_RESULT PASS count=1\n",
                "runtime-mode-tier0.txt": "RUNTIME_MODE_RESULT PASS mode=static sps_profile=false\n",
                "runtime-mode.txt": "RUNTIME_MODE_RESULT PASS mode=static sps_profile=true\n",
                "health.txt": "HEALTH_RESULT PASS phase=tier0\nHEALTH_RESULT PASS phase=sps-profile\n",
                "profile-hash.txt": "PROFILER_HASH_RESULT PASS sha256=fixture\nSPS_MODULE_HASH_RESULT PASS sha256=fixture\n",
                "profile-gate.txt": "SPS_PROFILE_RESULT PASS probes=11 rounds=33 min_match_fraction=1.000\n",
                "gpu-errors.txt": "GPU_ERROR_RESULT PASS logs=2\n",
                "receipt-sanitize.txt": "RECEIPT_SANITIZE_RESULT PASS\n",
                "stop.txt": (
                    "STOP_RESULT PASS container=dsfv-iter004-profile-tier0\n"
                    "STOP_RESULT PASS container=dsfv-iter004-profile\n"
                    "PORT_AFTER_STOP PASS\n"
                ),
            }
            for name, text in receipts.items():
                (run / name).write_text(text)
            result = summarize(run)
            self.assertEqual(result["outcome"], "COMPLETE")
            self.assertTrue(result["gates"]["sps_profile"])
            self.assertTrue(all(result["gates"].values()))

            contract = json.loads((run / "contract.json").read_text())
            contract["candidate"]["profile_mode"] = "sps-additive"
            (run / "contract.json").write_text(json.dumps(contract))
            result = summarize(run)
            self.assertEqual(result["outcome"], "COMPLETE")
            self.assertTrue(result["gates"]["sps_profile"])

    def test_calibrated_table_requires_staged_and_mounted_hashes(self):
        with tempfile.TemporaryDirectory() as td:
            run = Path(td)
            digest = "6c5acc36f422fc95c425445f1253a7571407b22e7871571622bbfb427b99eb69"
            (run / "contract.json").write_text(json.dumps({
                "iteration": 9,
                "slug": "compact-calibrated-sps",
                "candidate": {"sps_table": "calibrated"},
                "target": {"sps_table_sha256": digest},
                "expected_concurrency": [],
                "expected_prefill_targets": [],
            }))
            receipts = {
                "dispatch.txt": "STAGE_HASH_RESULT PASS files=11\nFROZEN_HASH_RESULT PASS\n",
                "preflight.txt": (
                    f"SPS_ARTIFACT_HASH_RESULT PASS sha256={digest} mode=calibrated\n"
                    "PREFLIGHT_RESULT PASS\n"
                ),
                "sps-mount-hash.txt": (
                    f"SPS_MOUNT_HASH_RESULT PASS sha256={digest} mode=calibrated container=fixture\n"
                ),
                "smoke.txt": "SMOKE_RESULT PASS\n",
                "replay.txt": "REPLAY_RESULT PASS turns=6\n",
                "opaque.txt": "OPAQUE_RESULT PASS n=64 mismatches=0\n",
                "repaudit.txt": "REPAUDIT C=64 N=128 flagged=0 rate=0.0%\n",
                "patch.txt": "PATCH_RESULT PASS count=1\n",
                "runtime-mode.txt": "RUNTIME_MODE_RESULT PASS mode=compact sps=calibrated verify_all=false\n",
                "health.txt": "HEALTH_RESULT PASS\n",
                "gpu-errors.txt": "GPU_ERROR_RESULT PASS\n",
                "receipt-sanitize.txt": "RECEIPT_SANITIZE_RESULT PASS\n",
                "stop.txt": "STOP_RESULT PASS\nPORT_AFTER_STOP PASS\n",
            }
            for name, text in receipts.items():
                (run / name).write_text(text)

            result = summarize(run)
            self.assertEqual(result["outcome"], "COMPLETE")
            self.assertTrue(result["gates"]["sps_table_hash"])

            (run / "sps-mount-hash.txt").unlink()
            result = summarize(run)
            self.assertEqual(result["outcome"], "BLOCKED")
            self.assertFalse(result["gates"]["sps_table_hash"])

    def test_nextn_override_requires_explicit_runtime_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            run = Path(td)
            (run / "contract.json").write_text(json.dumps({
                "iteration": 15,
                "slug": "compact-nextn-one",
                "candidate": {
                    "sps_table": "none",
                    "num_nextn_predict_layers": 1,
                },
                "target": {},
                "expected_concurrency": [],
                "expected_prefill_targets": [],
            }))
            receipts = {
                "dispatch.txt": "STAGE_HASH_RESULT PASS files=10\nFROZEN_HASH_RESULT PASS\n",
                "preflight.txt": "PREFLIGHT_RESULT PASS\n",
                "smoke.txt": "SMOKE_RESULT PASS\n",
                "replay.txt": "REPLAY_RESULT PASS turns=6\n",
                "opaque.txt": "OPAQUE_RESULT PASS n=64 mismatches=0\n",
                "repaudit.txt": "REPAUDIT C=64 N=128 flagged=0 rate=0.0%\n",
                "patch.txt": "PATCH_RESULT PASS count=1\n",
                "runtime-mode.txt": (
                    "RUNTIME_MODE_RESULT PASS mode=compact sps=uninitialized verify_all=true\n"
                ),
                "health.txt": "HEALTH_RESULT PASS\n",
                "gpu-errors.txt": "GPU_ERROR_RESULT PASS\n",
                "receipt-sanitize.txt": "RECEIPT_SANITIZE_RESULT PASS\n",
                "stop.txt": "STOP_RESULT PASS\nPORT_AFTER_STOP PASS\n",
            }
            for name, text in receipts.items():
                (run / name).write_text(text)

            result = summarize(run)
            self.assertEqual(result["outcome"], "BLOCKED")
            self.assertFalse(result["gates"]["nextn_override"])

            with (run / "runtime-mode.txt").open("a") as handle:
                handle.write(
                    "NEXTN_OVERRIDE_RESULT PASS num_nextn_predict_layers=1\n"
                )
            result = summarize(run)
            self.assertEqual(result["outcome"], "COMPLETE")
            self.assertTrue(result["gates"]["nextn_override"])

            contract = json.loads((run / "contract.json").read_text())
            contract["candidate"]["num_nextn_predict_layers"] = "checkpoint"
            (run / "contract.json").write_text(json.dumps(contract))
            result = summarize(run)
            self.assertEqual(result["outcome"], "BLOCKED")
            self.assertFalse(result["gates"]["nextn_override"])

            with (run / "runtime-mode.txt").open("a") as handle:
                handle.write(
                    "NEXTN_OVERRIDE_RESULT PASS num_nextn_predict_layers=checkpoint\n"
                )
            result = summarize(run)
            self.assertEqual(result["outcome"], "COMPLETE")
            self.assertTrue(result["gates"]["nextn_override"])

    def test_missing_row_is_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            run = Path(td)
            (run / "contract.json").write_text(json.dumps({
                "iteration": 0,
                "slug": "incumbent-null",
                "expected_concurrency": [1, 4],
                "expected_prefill_targets": [],
            }))
            (run / "throughput.txt").write_text('BENCH {"C": 1, "agg_tok_s": 100}\n')
            result = summarize(run)
            self.assertEqual(result["outcome"], "BLOCKED")
            self.assertIn("missing throughput rows: [4]", result["blockers"])


if __name__ == "__main__":
    unittest.main()

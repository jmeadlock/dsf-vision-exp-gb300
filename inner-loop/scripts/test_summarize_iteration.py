#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from summarize_iteration import summarize

WORKLOAD_NAMES = [
    "natural_prose",
    "code_oriented",
    "six_turn_replay",
    "warm_prefix_repeat",
    "staggered_mixed_load",
]


def write_common_run(run: Path, contract: dict) -> None:
    run.mkdir(parents=True, exist_ok=True)
    (run / "contract.json").write_text(json.dumps(contract))
    rows = [
        {"C": c, "n": 3 * c, "agg_tok_s": 100.0 * c, "per_stream_tok_s": 100.0,
         "mean_ttft_s": 0.2, "ptok": 7000, "ctok_total": 3072 * c, "wall_s": 1.0,
         "accept_len_counter": 3.7, "accept_rate_gauge": 0.7}
        for c in [1, 4, 8, 16, 32, 64]
    ]
    (run / "throughput.txt").write_text("\n".join("BENCH " + json.dumps(r) for r in rows))
    (run / "prefill.txt").write_text("\n".join(
        f"PREFILL target={n} prompt_tokens={n-10} ttft_s=[1.0, 1.1, 0.9] tok_s=[10000, 9090, 11100] mean_tok_s=10063"
        for n in [8000, 32000, 64000, 128000, 256000]
    ))
    workload_rows = [
        {
            "prompt_class": name,
            "max_tokens": 128,
            "temperature": 0,
            "reasoning_effort": "low",
            "ttft_s": 0.2,
            "wall_s": 2.0,
            "decode_tok_s": 100.0,
            "output_tokens": 200,
            "finish_reason": "stop",
            "speculative_acceptance": {"accept_len": 3.2, "accept_rate": 0.72},
            "cache_hit_tokens": 0,
            "repetition_ok": True,
            "tool_fidelity_ok": True,
        }
        for name in WORKLOAD_NAMES
    ]
    (run / "agent-workload.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in workload_rows)
    )
    receipts = {
        "smoke.txt": "PASS models\nSMOKE_RESULT PASS\n",
        "replay.txt": "REPLAY_TURN PASS turn=1 ttft_s=0.1 wall_s=0.2\nREPLAY_RESULT PASS turns=6\n",
        "opaque.txt": "OPAQUE_RESULT PASS n=64 mismatches=0\n",
        "repaudit.txt": "REPAUDIT C=64 N=128 flagged=0 rate=0.0%\n",
        "patch.txt": "PATCH_RESULT PASS count=1\n",
        "runtime-mode.txt": "RUNTIME_MODE_RESULT PASS mode=compact sps=none verify_all=true\nNEXTN_OVERRIDE_RESULT PASS num_nextn_predict_layers=checkpoint\n",
        "health.txt": "HEALTH_RESULT PASS\n",
        "gpu-errors.txt": "GPU_ERROR_RESULT PASS\n",
        "dispatch.txt": "CONTRACT_SCHEMA_RESULT PASS campaign_id=sps-requal-20260904-now phase=baseline run_id=fixture\nSTAGE_HASH_RESULT PASS files=11\nFROZEN_HASH_RESULT PASS\n",
        "preflight.txt": "PREFLIGHT_RESULT PASS\n",
        "receipt-sanitize.txt": "RECEIPT_SANITIZE_RESULT PASS\n",
        "stop.txt": "STOP_RESULT PASS\nPORT_AFTER_STOP PASS\n",
    }
    if contract.get("candidate", {}).get("sps_table") == "fine-grained":
        digest = contract["target"]["sps_table_sha256"]
        receipts["runtime-mode.txt"] = "RUNTIME_MODE_RESULT PASS mode=compact sps=fine-grained verify_all=false\nNEXTN_OVERRIDE_RESULT PASS num_nextn_predict_layers=checkpoint\n"
        receipts["preflight.txt"] = f"SPS_ARTIFACT_HASH_RESULT PASS sha256={digest} mode=fine-grained\nPREFLIGHT_RESULT PASS\n"
        receipts["sps-mount-hash.txt"] = f"SPS_MOUNT_HASH_RESULT PASS sha256={digest} mode=fine-grained container=fixture\n"
    for name, text in receipts.items():
        (run / name).write_text(text)


class SummarizeIterationTests(unittest.TestCase):
    def test_schema_v2_complete_run_requires_workload_receipts(self):
        with tempfile.TemporaryDirectory() as td:
            run = Path(td) / "B0"
            contract = {
                "schema_version": 2,
                "campaign_id": "sps-requal-20260904-now",
                "phase": "baseline",
                "sequence": 1,
                "slug": "compact-no-table",
                "candidate": {"sps_table": "none", "num_nextn_predict_layers": "checkpoint"},
                "target": {"validator_sha256": "a" * 64},
                "expected_concurrency": [1, 4, 8, 16, 32, 64],
                "expected_prefill_targets": [8000, 32000, 64000, 128000, 256000],
                "expected_workload_classes": WORKLOAD_NAMES,
            }
            write_common_run(run, contract)
            result = summarize(run)
        self.assertEqual(result["schema_version"], 2)
        self.assertEqual(result["campaign_id"], "sps-requal-20260904-now")
        self.assertEqual(result["outcome"], "COMPLETE")
        self.assertTrue(result["gates"]["agent_workload"])
        self.assertEqual([row["prompt_class"] for row in result["agent_workload"]], WORKLOAD_NAMES)

    def test_schema_v2_missing_workload_receipt_blocks_run(self):
        with tempfile.TemporaryDirectory() as td:
            run = Path(td) / "B0"
            contract = {
                "schema_version": 2,
                "campaign_id": "sps-requal-20260904-now",
                "phase": "baseline",
                "sequence": 1,
                "slug": "compact-no-table",
                "candidate": {"sps_table": "none"},
                "target": {},
                "expected_concurrency": [],
                "expected_prefill_targets": [],
                "expected_workload_classes": WORKLOAD_NAMES,
            }
            write_common_run(run, contract)
            (run / "agent-workload.jsonl").unlink()
            result = summarize(run)
        self.assertEqual(result["outcome"], "BLOCKED")
        self.assertFalse(result["gates"]["agent_workload"])

    def test_schema_v2_fine_grained_table_requires_mount_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            run = Path(td) / "S1"
            contract = {
                "schema_version": 2,
                "campaign_id": "sps-requal-20260904-now",
                "phase": "candidate",
                "sequence": 2,
                "slug": "compact-fine-grained-sps",
                "candidate": {"sps_table": "fine-grained", "num_nextn_predict_layers": "checkpoint"},
                "target": {"sps_table_sha256": "f" * 64},
                "expected_concurrency": [],
                "expected_prefill_targets": [],
                "expected_workload_classes": WORKLOAD_NAMES,
            }
            write_common_run(run, contract)
            self.assertEqual(summarize(run)["outcome"], "COMPLETE")
            (run / "sps-mount-hash.txt").unlink()
            result = summarize(run)
        self.assertEqual(result["outcome"], "BLOCKED")
        self.assertFalse(result["gates"]["sps_table_hash"])


if __name__ == "__main__":
    unittest.main()

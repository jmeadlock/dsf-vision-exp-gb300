#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from evaluate_candidate import evaluate

WORKLOAD_NAMES = [
    "natural_prose",
    "code_oriented",
    "six_turn_replay",
    "warm_prefix_repeat",
    "staggered_mixed_load",
]


def results(name: str, multiplier: float = 1.0, *, sps: str = "none", gates: bool = True) -> dict:
    return {
        "schema_version": 2,
        "campaign_id": "sps-requal-20260904-now",
        "phase": "candidate" if sps == "fine-grained" else "control",
        "run_id": name,
        "outcome": "COMPLETE",
        "candidate": {"ragged_verify_mode": "compact", "sps_table": sps},
        "throughput": [
            {
                "C": c,
                "agg_tok_s": 1000.0 * multiplier,
                "mean_ttft_s": 1.0 / multiplier,
                "accept_len_counter": 4.0 * multiplier,
                "accept_rate_gauge": 0.75 * multiplier,
            }
            for c in [1, 4, 8, 16, 32, 64]
        ],
        "prefill": [
            {"target": n, "mean_tok_s": 10000.0 * multiplier}
            for n in [8000, 32000, 64000, 128000, 256000]
        ],
        "agent_workload": [
            {
                "prompt_class": w,
                "decode_tok_s": 100.0 * multiplier,
                "ttft_s": 1.0 / multiplier,
                "repetition_ok": True,
                "tool_fidelity_ok": True,
                "finish_reason": "stop",
            }
            for w in WORKLOAD_NAMES
        ],
        "gates": {"smoke": gates, "agent_workload": gates},
        "blockers": [] if gates else ["failed gates"],
    }


class EvaluateCandidateTests(unittest.TestCase):
    def test_candidate_uses_adjacent_compact_controls_and_reports_static_anchor_delta(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for run_id, data in {
                "A0": results("A0", 1.00),
                "A1": results("A1", 1.00),
                "B0": results("B0", 1.00),
                "B1": results("B1", 1.00),
                "S1": results("S1", 1.04, sps="fine-grained"),
            }.items():
                d = root / run_id
                d.mkdir()
                (d / "results.json").write_text(json.dumps(data))
            contract = {
                "schema_version": 2,
                "campaign_id": "sps-requal-20260904-now",
                "phase": "candidate",
                "run_id": "S1",
                "candidate": {"ragged_verify_mode": "compact", "sps_table": "fine-grained"},
                "adjacent_compact_controls": ["B0", "B1"],
                "static_anchor_runs": ["A0", "A1"],
                "winner_rule": {
                    "primary_rows": [8, 16, 32, 64],
                    "compare_candidate_to": "adjacent_compact_controls",
                    "also_report_vs": "static_recipe_v2_anchors",
                    "do_not_claim_only_table_delta_from_recipe_v2": True,
                },
            }
            run = root / "S1"
            (run / "contract.json").write_text(json.dumps(contract))
            decision = evaluate(run)
        self.assertEqual(decision["verdict"], "WIN")
        self.assertEqual(decision["comparison_basis"], "adjacent_compact_controls")
        self.assertEqual(decision["static_anchor_comparison"]["claim_boundary"], "combined_compact_plus_table_vs_static_recipe_v2")
        self.assertFalse(decision["claims_only_table_delta_from_recipe_v2"])
        self.assertAlmostEqual(decision["throughput"]["primary_geomean_delta_pct"], 4.0, places=3)

    def test_static_candidate_is_rejected_because_sps_would_be_noop(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run = root / "S1"
            run.mkdir()
            (run / "results.json").write_text(json.dumps(results("S1", 1.04, sps="fine-grained")))
            contract = {
                "schema_version": 2,
                "campaign_id": "sps-requal-20260904-now",
                "phase": "candidate",
                "candidate": {"ragged_verify_mode": "static", "sps_table": "fine-grained"},
                "adjacent_compact_controls": ["B0", "B1"],
                "winner_rule": {"primary_rows": [8, 16, 32, 64]},
            }
            (run / "contract.json").write_text(json.dumps(contract))
            with self.assertRaisesRegex(ValueError, "fine-grained SPS requires compact"):
                evaluate(run)

    def test_release_gates_block_acceptance_or_workload_regression(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            control = results("B0", 1.0)
            candidate = results("S1", 1.04, sps="fine-grained")
            for row in candidate["throughput"]:
                row["accept_rate_gauge"] = 0.60
            for run_id, data in {"B0": control, "B1": control, "S1": candidate}.items():
                d = root / run_id
                d.mkdir()
                (d / "results.json").write_text(json.dumps(data))
            contract = {
                "schema_version": 2,
                "campaign_id": "sps-requal-20260904-now",
                "phase": "candidate",
                "candidate": {"ragged_verify_mode": "compact", "sps_table": "fine-grained"},
                "adjacent_compact_controls": ["B0", "B1"],
                "winner_rule": {"primary_rows": [8, 16, 32, 64]},
            }
            (root / "S1" / "contract.json").write_text(json.dumps(contract))
            decision = evaluate(root / "S1")
        self.assertEqual(decision["verdict"], "LOSS")
        self.assertIn("acceptance", " ".join(decision["release_gate_failures"]))


if __name__ == "__main__":
    unittest.main()

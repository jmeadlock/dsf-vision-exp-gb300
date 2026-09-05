#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_workload_gate import WORKLOAD_CLASSES, parse_workload_receipts, summarize_repetition


class AgentWorkloadGateTests(unittest.TestCase):
    def test_workload_fixture_classes_are_fixed_and_complete(self):
        self.assertEqual(
            [case["prompt_class"] for case in WORKLOAD_CLASSES],
            [
                "natural_prose",
                "code_oriented",
                "six_turn_replay",
                "warm_prefix_repeat",
                "staggered_mixed_load",
            ],
        )
        for case in WORKLOAD_CLASSES:
            self.assertIn("max_tokens", case)
            self.assertIn("temperature", case)
            self.assertIn("reasoning_effort", case)
            self.assertNotIn("private", json.dumps(case).lower())

    def test_receipt_parser_requires_release_gate_fields(self):
        rows = [
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
            for name in [case["prompt_class"] for case in WORKLOAD_CLASSES]
        ]
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "agent-workload.jsonl"
            path.write_text("".join(json.dumps(row) + "\n" for row in rows))
            parsed = parse_workload_receipts(path)
        self.assertEqual(len(parsed), 5)

        bad = rows[:-1]
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "agent-workload.jsonl"
            path.write_text("".join(json.dumps(row) + "\n" for row in bad))
            with self.assertRaisesRegex(ValueError, "missing workload classes"):
                parse_workload_receipts(path)

    def test_repetition_summary_flags_repeated_eightgrams(self):
        ok = summarize_repetition("one two three four five six seven eight nine ten")
        self.assertTrue(ok["repetition_ok"])
        repeated = summarize_repetition("a b c d e f g h " * 6)
        self.assertFalse(repeated["repetition_ok"])
        self.assertGreaterEqual(repeated["worst_8gram_count"], 6)


if __name__ == "__main__":
    unittest.main()

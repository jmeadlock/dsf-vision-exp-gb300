#!/usr/bin/env python3
"""Tests for standalone SPS profile and held-out selection gates."""
from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path

from sps_profile_gate import (
    ProfileGateError,
    gate_profile_run,
    select_held_out_width,
)


EXPECTED_BS = [1, 2, 3, 4, 5, 6, 7, 8]
EXPECTED_FRACS = [0.2, 0.4, 0.6, 0.8, 1.0]
EXPECTED_WIDTHS = [64, 6, 1]


def write_profile_run(run: Path, *, drop_last: bool = False, duplicate_first: bool = False) -> None:
    rows = []
    for repeat in range(3):
        for bs in EXPECTED_BS:
            for frac in EXPECTED_FRACS:
                rows.append({
                    "repeat": repeat,
                    "batch_size_per_rank": bs,
                    "frac": frac,
                    "batch_tokens": bs + int(frac * bs * 5),
                    "steps_per_sec": 1000.0 / (1 + bs + frac),
                    "match_fraction": 1.0,
                })
    if drop_last:
        rows = rows[:-1]
    if duplicate_first:
        rows.append(dict(rows[0]))
    table = {
        "bias_seconds": 0.001,
        "bs_probes": EXPECTED_BS,
        "alpha_seconds": [0.0001 * bs for bs in EXPECTED_BS],
        "m_probes": list(range(2, 49)),
        "theta_seconds": [0.00001 * m for m in range(2, 49)],
    }
    manifest = {
        "batch_size_per_rank_sweep": EXPECTED_BS,
        "fracs": EXPECTED_FRACS,
        "repeats": 3,
        "simulate_acc_len": 1.0,
        "verify_num_draft_tokens": 6,
        "settings": {
            "input_len": 16,
            "temperature": 1.0,
            "min_steady_steps": 32,
            "min_steady_seconds": 10.0,
        },
    }
    (run / "dsfv-sps-profile.json.manifest.json").write_text(json.dumps(manifest))
    (run / "dsfv-sps-profile.rounds.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows)
    )


class SpsProfileGateTests(unittest.TestCase):
    def test_profile_gate_requires_corrected_deterministic_sweep_and_m_range(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            run = Path(td)
            write_profile_run(run)
            result = gate_profile_run(run)
        self.assertEqual(result["cells"], 120)
        self.assertEqual(result["batch_size_per_rank"], EXPECTED_BS)
        self.assertEqual(result["fracs"], EXPECTED_FRACS)
        self.assertEqual(result["m_min"], 2)
        self.assertEqual(result["m_max"], 48)
        self.assertEqual(result["fit_mbin_widths"], EXPECTED_WIDTHS)

    def test_profile_gate_rejects_missing_or_duplicate_sweep_cells(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            run = Path(td)
            write_profile_run(run, drop_last=True)
            with self.assertRaisesRegex(ProfileGateError, "coverage"):
                gate_profile_run(run)
        with tempfile.TemporaryDirectory() as td:
            run = Path(td)
            write_profile_run(run, duplicate_first=True)
            with self.assertRaisesRegex(ProfileGateError, "coverage"):
                gate_profile_run(run)

    def test_held_out_selection_requires_widths_and_improvement_gates(self) -> None:
        metrics = {
            "64": {"mae_ms": 1.0, "rmse_ms": 1.5, "max_error_ms": 3.0, "mean_bias_ms": 0.3},
            "6": {"mae_ms": 0.42, "rmse_ms": 1.2, "max_error_ms": 2.5, "mean_bias_ms": 0.1},
            "1": {"mae_ms": 0.41, "rmse_ms": 1.1, "max_error_ms": 2.4, "mean_bias_ms": 0.2},
        }
        selected = select_held_out_width(metrics, noise_mae_ms=0.05)
        self.assertEqual(selected["selected_mbin_w"], 6)
        self.assertEqual(selected["reason"], "width_1_and_6_tied_inside_noise_choose_6")

        failing = dict(metrics)
        failing["6"] = {"mae_ms": 0.8, "rmse_ms": 1.0, "max_error_ms": 2.0, "mean_bias_ms": 0.1}
        failing["1"] = {"mae_ms": 0.75, "rmse_ms": 1.0, "max_error_ms": 2.0, "mean_bias_ms": 0.1}
        with self.assertRaisesRegex(ProfileGateError, "50%"):
            select_held_out_width(failing)


if __name__ == "__main__":
    unittest.main()

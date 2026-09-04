#!/usr/bin/env python3
"""Exercise the runner's embedded SPS artifact gate."""
from __future__ import annotations

import json
import math
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


INNER_LOOP = Path(__file__).resolve().parents[1]
RUNNER = INNER_LOOP / "scripts" / "run_remote_candidate.sh"
RUNS = INNER_LOOP / "runs"
MARKER = (
    '  python3 - "$RUN_DIR" "$PROFILE_MODE" '
    '>"$RUN_DIR/profile-gate.txt" <<\'PY\''
)


def embedded_program() -> str:
    source = RUNNER.read_text()
    pattern = re.compile(re.escape(MARKER) + r"\n(?P<program>.*?)\nPY", re.DOTALL)
    match = pattern.search(source)
    if not match:
        raise AssertionError("embedded SPS profile gate not found")
    return match.group("program")


def run_gate(run: Path, mode: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-", str(run), mode],
        input=embedded_program(),
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )


class SpsProfileGateTests(unittest.TestCase):
    def test_existing_diagonal_artifact_still_passes(self) -> None:
        run = RUNS / "008-sps-profile-static-20260904T033459Z"
        completed = run_gate(run, "sps")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("SPS_PROFILE_RESULT PASS kind=diagonal", completed.stdout)

    def test_additive_gate_requires_exact_cells_and_good_fit(self) -> None:
        expected_bs = [1, 2, 4, 8, 16, 24, 32, 40, 48, 56, 64]
        fracs = [0.25, 0.5, 0.75, 1.0]
        expected_m = sorted(
            {
                round((bs + int(frac * bs * 5)) / 64) * 64
                for bs in expected_bs
                for frac in fracs
            }
        )
        bias = 0.001
        alpha = {bs: (bs - 1) * 0.00001 for bs in expected_bs}
        theta = {m: index * 0.0001 for index, m in enumerate(expected_m)}
        rounds = []
        for repeat in range(3):
            for bs in expected_bs:
                for frac in fracs:
                    batch_tokens = bs + int(frac * bs * 5)
                    m_bin = round(batch_tokens / 64) * 64
                    step_time = bias + alpha[bs] + theta[m_bin]
                    rounds.append(
                        {
                            "repeat": repeat,
                            "batch_size_per_rank": bs,
                            "frac": frac,
                            "batch_tokens": batch_tokens,
                            "steps_per_sec": 1.0 / step_time,
                            "match_fraction": 1.0,
                        }
                    )
        table = {
            "bias_seconds": bias,
            "bs_probes": expected_bs,
            "alpha_seconds": [alpha[bs] for bs in expected_bs],
            "m_probes": expected_m,
            "theta_seconds": [theta[m] for m in expected_m],
        }
        manifest = {
            "batch_size_per_rank_sweep": expected_bs,
            "fracs": fracs,
            "repeats": 3,
            "simulate_acc_len": 1.0,
            "verify_num_draft_tokens": 6,
        }

        with tempfile.TemporaryDirectory() as td:
            run = Path(td)
            (run / "dsfv-sps-profile.json").write_text(json.dumps(table))
            (run / "dsfv-sps-profile.json.manifest.json").write_text(
                json.dumps(manifest)
            )
            rounds_path = run / "dsfv-sps-profile.rounds.jsonl"
            rounds_path.write_text(
                "".join(json.dumps(row) + "\n" for row in rounds)
            )

            completed = run_gate(run, "sps-additive")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("SPS_PROFILE_RESULT PASS kind=additive", completed.stdout)
            self.assertIn("cells=132", completed.stdout)

            rounds_path.write_text(
                "".join(json.dumps(row) + "\n" for row in rounds[:-1])
            )
            rejected = run_gate(run, "sps-additive")
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("cell coverage/duplication mismatch", rejected.stderr)


if __name__ == "__main__":
    unittest.main()

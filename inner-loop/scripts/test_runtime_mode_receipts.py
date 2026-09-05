#!/usr/bin/env python3
"""Source-level safety checks for runtime-mode gates without historical receipt fixtures."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

INNER_LOOP = Path(__file__).resolve().parents[1]
RUNNER = INNER_LOOP / "scripts" / "run_remote_candidate.sh"
LAUNCHER = INNER_LOOP / "scripts" / "launch-dsfv-experiment.sh"


class RuntimeModeReceiptTests(unittest.TestCase):
    def test_launcher_rejects_fine_grained_sps_unless_ragged_is_compact(self) -> None:
        source = LAUNCHER.read_text()
        self.assertIn('fine-grained)', source)
        self.assertIn('fine-grained SPS requires compact ragged mode', source)
        self.assertIn('--speculative-dspark-sps-table-path /dspark_sps_table.json', source)

    def test_launcher_keeps_profile_server_loopback_and_unauthenticated(self) -> None:
        source = LAUNCHER.read_text()
        self.assertIn('none)\n    AUTH_ARGS=(--api-key "$(</home/milo/.glm_api_key)")', source)
        self.assertIn('sps|sps-additive)\n    HOST=127.0.0.1', source)
        self.assertIn('--host "$HOST" --port 30003', source)
        self.assertEqual(source.count("--api-key"), 1)

    def test_runner_uses_corrected_profile_sweep(self) -> None:
        source = RUNNER.read_text()
        self.assertIn('--batch-size 1 2 3 4 5 6 7 8', source)
        self.assertIn('--fracs 0.2 0.4 0.6 0.8 1.0', source)
        self.assertIn('--repeats 3', source)
        self.assertIn('sps_profile_gate.py', source)

    def test_profile_uses_pinned_patched_profiler_and_campaign_artifacts(self) -> None:
        runner = RUNNER.read_text()
        launcher = LAUNCHER.read_text()
        self.assertIn('PROFILER_HOST_PATH="$ROOT/artifacts/dspark_sps_profiler-pr37815-085a5e2.py"', runner)
        self.assertIn('export PROFILER_HOST_PATH', runner)
        self.assertIn('PROFILER_MOUNTS=(-v "$PROFILER_HOST_PATH":/sgl-workspace/sglang/python/sglang/benchmark/dspark_sps_profiler.py:ro)', launcher)
        self.assertNotIn('$ROOT/campaigns/$CAMPAIGN_ID/artifacts/$EXPECTED_SPS_DIGEST.json', runner)

    def test_runner_accepts_fine_grained_runtime_and_enforces_release(self) -> None:
        source = RUNNER.read_text()
        self.assertIn('current|calibrated|fine-grained', source)
        self.assertIn('RELEASE="$ROOT/RELEASE"', source)
        self.assertIn('RELEASE_RESULT PASS', source)

    def test_runner_invokes_workload_gate_for_nonprofile_runs(self) -> None:
        source = RUNNER.read_text()
        self.assertRegex(source, re.compile(r'PHASE agent-workload.*agent_workload_gate\.py', re.DOTALL))

    def test_runner_has_no_restore_path(self) -> None:
        source = RUNNER.read_text().lower()
        launcher = LAUNCHER.read_text().lower()
        self.assertNotIn('restore', source)
        self.assertNotIn('restore', launcher)


if __name__ == "__main__":
    unittest.main()

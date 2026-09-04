#!/usr/bin/env python3
"""Execute the runner's embedded runtime-mode gates on real receipts."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


INNER_LOOP = Path(__file__).resolve().parents[1]
RUNNER = INNER_LOOP / "scripts" / "run_remote_candidate.sh"
LAUNCHER = INNER_LOOP / "scripts" / "launch-dsfv-experiment.sh"
RUNS = INNER_LOOP / "runs"


def embedded_program(marker: str) -> str:
    source = RUNNER.read_text()
    pattern = re.compile(re.escape(marker) + r"\n(?P<program>.*?)\nPY", re.DOTALL)
    match = pattern.search(source)
    if not match:
        raise AssertionError(f"embedded Python marker not found: {marker}")
    return match.group("program")


def run_gate(
    program: str,
    server_info: Path,
    container_inspect: Path,
    *extra_args: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-",
            str(server_info),
            str(container_inspect),
            *extra_args,
        ],
        input=program,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )


class RuntimeModeReceiptTests(unittest.TestCase):
    def test_tier0_gate_accepts_real_static_nonprofile_receipt(self) -> None:
        run = RUNS / "006-sps-profile-static-20260904T030807Z"
        program = embedded_program(
            '  python3 - "$RUN_DIR/server-info-tier0.json" "$RUN_DIR/container-inspect-tier0.json" >"$RUN_DIR/runtime-mode-tier0.txt" <<\'PY\''
        )
        completed = run_gate(
            program,
            run / "server-info-tier0.json",
            run / "container-inspect-tier0.json",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn(
            "RUNTIME_MODE_RESULT PASS mode=static sps_profile=false",
            completed.stdout,
        )

    def test_static_control_gate_accepts_real_nonprofile_receipt(self) -> None:
        run = RUNS / "006-sps-profile-static-20260904T030807Z"
        program = embedded_program(
            '  python3 - "$RUN_DIR/server-info.json" "$RUN_DIR/container-inspect.json" "static-control" >"$RUN_DIR/runtime-mode.txt" <<\'PY\''
        )
        completed = run_gate(
            program,
            run / "server-info-tier0.json",
            run / "container-inspect-tier0.json",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn(
            "RUNTIME_MODE_RESULT PASS mode=static sps=none sps_effective=false",
            completed.stdout,
        )

    def test_profile_gate_accepts_loopback_simulated_recording_receipt(self) -> None:
        source_run = RUNS / "003-sps-profile-static-20260904T020558Z"
        program = embedded_program(
            '  python3 - "$RUN_DIR/server-info.json" "$RUN_DIR/container-inspect.json" "$PROFILE_MODE" >"$RUN_DIR/runtime-mode.txt" <<\'PY\''
        )
        with tempfile.TemporaryDirectory() as td:
            fixture = Path(td)
            info = json.loads((source_run / "server-info.json").read_text())
            info["host"] = "127.0.0.1"
            info["api_key"] = None
            for state in info.get("internal_states") or []:
                state["host"] = "127.0.0.1"
                state["api_key"] = None
            info_path = fixture / "server-info.json"
            info_path.write_text(json.dumps(info))

            inspect = json.loads((source_run / "container-inspect.json").read_text())
            cmd = list(inspect[0].get("Config", {}).get("Cmd") or [])
            while "--api-key" in cmd:
                index = cmd.index("--api-key")
                del cmd[index : index + 2]
            inspect[0]["Config"]["Cmd"] = cmd
            inspect_path = fixture / "container-inspect.json"
            inspect_path.write_text(json.dumps(inspect))

            completed = run_gate(program, info_path, inspect_path, "sps")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn(
            "RUNTIME_MODE_RESULT PASS mode=static profile=sps sps_profile=true",
            completed.stdout,
        )

    def test_additive_profile_gate_accepts_compact_recording_receipt(self) -> None:
        source_run = RUNS / "003-sps-profile-static-20260904T020558Z"
        program = embedded_program(
            '  python3 - "$RUN_DIR/server-info.json" "$RUN_DIR/container-inspect.json" "$PROFILE_MODE" >"$RUN_DIR/runtime-mode.txt" <<\'PY\''
        )
        with tempfile.TemporaryDirectory() as td:
            fixture = Path(td)
            info = json.loads((source_run / "server-info.json").read_text())
            info["host"] = "127.0.0.1"
            info["api_key"] = None
            for state in info.get("internal_states") or []:
                state["host"] = "127.0.0.1"
                state["api_key"] = None
                state["dspark_info_record"]["mode"] = "compact"
            info_path = fixture / "server-info.json"
            info_path.write_text(json.dumps(info))

            inspect = json.loads((source_run / "container-inspect.json").read_text())
            env = list(inspect[0]["Config"].get("Env") or [])
            env = [
                "SGLANG_RAGGED_VERIFY_MODE=compact"
                if item.startswith("SGLANG_RAGGED_VERIFY_MODE=")
                else item
                for item in env
            ]
            inspect[0]["Config"]["Env"] = env
            cmd = list(inspect[0]["Config"].get("Cmd") or [])
            while "--api-key" in cmd:
                index = cmd.index("--api-key")
                del cmd[index : index + 2]
            inspect[0]["Config"]["Cmd"] = cmd
            inspect_path = fixture / "container-inspect.json"
            inspect_path.write_text(json.dumps(inspect))

            completed = run_gate(
                program, info_path, inspect_path, "sps-additive"
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn(
            "RUNTIME_MODE_RESULT PASS mode=compact profile=sps-additive",
            completed.stdout,
        )

    def test_nextn_checkpoint_gate_accepts_real_default_receipt(self) -> None:
        run = RUNS / "014-compact-verify-all-opposite-order-20260904T104943Z"
        program = embedded_program(
            'python3 - "$RUN_DIR/server-info.json" "$RUN_DIR/container-inspect.json" "$NEXTN_LAYERS" >>"$RUN_DIR/runtime-mode.txt" <<\'PY\''
        )
        completed = run_gate(
            program,
            run / "server-info.json",
            run / "container-inspect.json",
            "checkpoint",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn(
            "NEXTN_OVERRIDE_RESULT PASS num_nextn_predict_layers=checkpoint",
            completed.stdout,
        )

    def test_nextn_one_gate_requires_matching_server_and_command_receipts(self) -> None:
        source_run = RUNS / "014-compact-verify-all-opposite-order-20260904T104943Z"
        program = embedded_program(
            'python3 - "$RUN_DIR/server-info.json" "$RUN_DIR/container-inspect.json" "$NEXTN_LAYERS" >>"$RUN_DIR/runtime-mode.txt" <<\'PY\''
        )
        with tempfile.TemporaryDirectory() as td:
            fixture = Path(td)
            info = json.loads((source_run / "server-info.json").read_text())
            info["json_model_override_args"] = '{"num_nextn_predict_layers":1}'
            info_path = fixture / "server-info.json"
            info_path.write_text(json.dumps(info))

            inspect = json.loads((source_run / "container-inspect.json").read_text())
            cmd = list(inspect[0]["Config"].get("Cmd") or [])
            cmd.extend(
                [
                    "--json-model-override-args",
                    '{"num_nextn_predict_layers":1}',
                ]
            )
            inspect[0]["Config"]["Cmd"] = cmd
            inspect_path = fixture / "container-inspect.json"
            inspect_path.write_text(json.dumps(inspect))

            completed = run_gate(program, info_path, inspect_path, "1")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn(
                "NEXTN_OVERRIDE_RESULT PASS num_nextn_predict_layers=1",
                completed.stdout,
            )

            info["json_model_override_args"] = "{}"
            info_path.write_text(json.dumps(info))
            rejected = run_gate(program, info_path, inspect_path, "1")
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("NEXTN_OVERRIDE_RESULT FAIL", rejected.stderr)

    def test_launcher_scopes_nextn_override_to_explicit_one(self) -> None:
        source = LAUNCHER.read_text()
        self.assertIn('NEXTN_LAYERS="${5:-checkpoint}"', source)
        self.assertIn(
            "MODEL_OVERRIDE_ARGS=(--json-model-override-args "
            "'{\"num_nextn_predict_layers\":1}')",
            source,
        )
        self.assertIn('"${MODEL_OVERRIDE_ARGS[@]}"', source)

    def test_additive_profile_uses_two_named_launches(self) -> None:
        source = RUNNER.read_text()
        self.assertIn(
            'if [[ "$PROFILE_MODE" == "sps" || "$PROFILE_MODE" == "sps-additive" ]]; then\n  GATES_CONTAINER="${CONTAINER}-tier0"',
            source,
        )
        self.assertIn(
            'launch_named "$GATES_CONTAINER" "static" "none" "none"', source
        )
        self.assertIn(
            'launch_named "$CONTAINER" "$RAGGED_MODE" "$SPS_MODE" "$PROFILE_MODE"',
            source,
        )

    def test_launcher_keeps_profile_server_loopback_and_unauthenticated(self) -> None:
        source = LAUNCHER.read_text()
        self.assertIn('none)\n    AUTH_ARGS=(--api-key "$(</home/milo/.glm_api_key)")', source)
        self.assertIn('sps|sps-additive)\n    HOST=127.0.0.1', source)
        self.assertIn('--host "$HOST" --port 30003', source)
        self.assertIn('--served-model-name dsf-vision-exp "${AUTH_ARGS[@]}"', source)
        self.assertEqual(source.count("--api-key"), 1)

    def test_tier0_gate_rejects_profile_environment(self) -> None:
        tier0_program = embedded_program(
            '  python3 - "$RUN_DIR/server-info-tier0.json" "$RUN_DIR/container-inspect-tier0.json" >"$RUN_DIR/runtime-mode-tier0.txt" <<\'PY\''
        )
        profile_run = RUNS / "003-sps-profile-static-20260904T020558Z"
        completed = run_gate(
            tier0_program,
            profile_run / "server-info.json",
            profile_run / "container-inspect.json",
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("RUNTIME_MODE_RESULT FAIL", completed.stderr)


if __name__ == "__main__":
    unittest.main()

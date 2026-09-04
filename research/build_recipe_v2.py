#!/usr/bin/env python3
"""Build and validate the public-safe DSFVE Recipe v2 artifacts.

The raw inner-loop receipts are intentionally gitignored. When they are present,
this script derives `recipe-v2.json` and `research/recipe-v2-evidence.json`
from the selected receipts and checks the committed launcher defaults. When the
receipts are absent, `--check` still validates the committed public artifacts and
launcher for internal consistency.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "inner-loop" / "runs"
RECIPE_JSON = ROOT / "recipe-v2.json"
EVIDENCE_JSON = ROOT / "research" / "recipe-v2-evidence.json"
LAUNCHER = ROOT / "launch-dsfv.sh"
DO_NOT_RETRY_MD = ROOT / "research" / "do-not-retry.md"
AUDIT_JSON = ROOT / "research" / "inner-loop-campaign-audit.json"

SELECTED_RUN_ID = "013-static-no-sps-paired-control-20260904T103545Z"
REVERSAL_RUN_ID = "016-compact-nextn-checkpoint-reversal-20260904T120854Z"
BASELINE_RUN_ID = "000-incumbent-null-20260904T004736Z"
MODEL_ID = "deepseek-ai/DeepSeek-V4-Flash-Vision-Exp"
MODEL_REVISION = "6821d6ad3681a4b137b066b76094fa82ebd0a380"
IMAGE_DIGEST = "sha256:7ac467a50508b7029a23e846c150998fdd26d95c1cfd377ea7e74e28374486a6"
IMAGE_REF = f"lmsysorg/sglang@{IMAGE_DIGEST}"
SGLANG_SOURCE_REVISION = "40b3e15ddbd9a1067e181283d9900dd3f4d76ed7"
PRIMARY_ROWS = [8, 16, 32, 64]
EXPECTED_CONCURRENCY = [1, 4, 8, 16, 32, 64]
EXPECTED_PREFILL_TARGETS = [8000, 32000, 64000, 128000, 256000]
MATERIALITY_BAND_PCT = 3.0

REQUIRED_GATE_MAP = {
    "contract_schema": "contract_schema",
    "staged_hashes": "staged_hashes",
    "frozen_hashes": "frozen_hashes",
    "runner_hash": "runner_hash",
    "preflight": "preflight",
    "health": "health",
    "runtime_mode_static_no_sps": "runtime_mode",
    "encoding_patch_present": "patch",
    "protocol_and_real_vision_smoke": "smoke",
    "six_replayed_tool_turns": "replay",
    "concurrent_opaque_identifier_fidelity": "opaque_identifiers",
    "c64_repetition_audit": "repetition",
    "gpu_error_scan": "gpu_errors",
    "receipt_sanitization": "receipt_sanitize",
    "clean_stop": "clean_stop",
}

CLOSED_MECHANISMS: list[dict[str, str]] = [
    {
        "mechanism": "compact_verify_all_as_speedup",
        "result": "Closed as neutral: compact-static-compact ABA was +0.901% primary C8-C64 and +0.217% cold prefill, inside the frozen +/-3% materiality band.",
        "scope": "Pinned DSFVE model/image/source on one GB300 TP1 with DSpark gamma 5, 1M context, memory 0.90, no SPS table, and the 7K-in/1K-out temperature-0 workload.",
        "reopen_condition": "Only reopen for a new image/source revision, hardware/workload change, or a new mechanism; use another frozen alternating control sequence before claiming a speedup.",
    },
    {
        "mechanism": "dspark_sps_tables",
        "result": "Not promoted: inherited SPS lost -3.561% primary in Iteration 2, calibrated SPS lost -4.327% against its frozen older compact reference in Iteration 10, same-hour table-off changed only +0.464%, and the corrected additive SPS runtime lost -7.145% primary with a -21.170% C8 row in Iteration 19.",
        "scope": "DSpark SPS tables on the pinned SGLang digest/source and single-GB300 DSFVE workload, including inherited, diagonal calibrated, and additive calibrated artifacts.",
        "reopen_condition": "Only reopen if SGLang changes SPS semantics/profiling or a new profiler supplies a new artifact; test it against a same-hour no-table control with mounted-artifact digest proof.",
    },
    {
        "mechanism": "one_nextn_predict_layer",
        "result": "Rejected: forcing num_nextn_predict_layers=1 lost in Iteration 15, and the default-one-default ABA estimate was -4.093% primary and -1.538% prefill for one layer.",
        "scope": "Pinned DSFVE checkpoint and SGLang digest/source on one GB300 TP1 with DSpark gamma 5 and no SPS table.",
        "reopen_condition": "Only reopen for a new checkpoint or runtime implementation; verify the live NextN value and run an immediate reversal control.",
    },
    {
        "mechanism": "dspark_gamma_3",
        "result": "Parked from the September 2-3 baseline: gamma 3 did not beat the gamma 5/default path on this box and had lower measured acceptance; Recipe v2 keeps the checkpoint/runtime default gamma 5 with no block-size override.",
        "scope": "Single GB300 TP1 DSpark serving for DSFVE. The old gamma-3 measurement was taken before the inner-loop harness and was not a promotable isolated v2 delta.",
        "reopen_condition": "Only reopen as a fresh one-delta card against Recipe v2, without chunked-prefill or SPS confounds, if a new runtime or workload makes gamma smaller plausible.",
    },
    {
        "mechanism": "chunked_prefill_4096",
        "result": "Rejected before Recipe v2: the September 2-3 ledger measured 4096 chunked prefill as slower on one GB300, with TTFT roughly doubling and C8/C16 about 20% lower than the no-4096 path.",
        "scope": "Pinned DSFVE one-GB300 serving with the 1M-context SGLang preview image; Recipe v2 keeps chunked-prefill size 8192.",
        "reopen_condition": "Only reopen after a chunked-prefill implementation change or a workload that directly values a different prefill/decode tradeoff; rerun throughput and cold-prefill gates together.",
    },
]


class MissingReceipts(RuntimeError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def json_text(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, sort_keys=False) + "\n"


def geomean(values: list[float]) -> float:
    return math.prod(values) ** (1.0 / len(values))


def pct(candidate: float, reference: float) -> float:
    return (candidate / reference - 1.0) * 100.0


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def receipt_paths() -> list[Path]:
    selected = RUNS / SELECTED_RUN_ID
    reversal = RUNS / REVERSAL_RUN_ID
    return [
        selected / "contract.json",
        selected / "results.json",
        selected / "decision.json",
        selected / "paired-comparisons.json",
        selected / "runtime-mode.txt",
        reversal / "contract.json",
        reversal / "decision.json",
        reversal / "paired-comparisons.json",
        AUDIT_JSON,
    ]


def receipts_available() -> bool:
    return all(path.exists() for path in receipt_paths())


def load_receipts() -> dict[str, Any]:
    missing = [str(path.relative_to(ROOT)) for path in receipt_paths() if not path.exists()]
    if missing:
        raise MissingReceipts("missing gitignored receipts: " + ", ".join(missing))
    selected = RUNS / SELECTED_RUN_ID
    reversal = RUNS / REVERSAL_RUN_ID
    return {
        "contract": load_json(selected / "contract.json"),
        "results": load_json(selected / "results.json"),
        "decision": load_json(selected / "decision.json"),
        "pairs": load_json(selected / "paired-comparisons.json"),
        "runtime_mode_text": (selected / "runtime-mode.txt").read_text().strip(),
        "reversal_contract": load_json(reversal / "contract.json"),
        "reversal_decision": load_json(reversal / "decision.json"),
        "reversal_pairs": load_json(reversal / "paired-comparisons.json"),
        "audit": load_json(AUDIT_JSON),
    }


def audit_iteration(audit: dict[str, Any], iteration: int) -> dict[str, Any]:
    matches = [row for row in audit["iterations"] if row["iteration"] == iteration]
    require(len(matches) == 1, f"audit must contain exactly one Iteration {iteration} row")
    return matches[0]


def selected_configuration(contract: dict[str, Any], reversal_contract: dict[str, Any]) -> dict[str, Any]:
    runtime = contract["constant_runtime"]
    return {
        "model": MODEL_ID,
        "model_revision": contract["target"]["model_revision"],
        "image": IMAGE_REF,
        "image_digest": contract["target"]["image_digest"],
        "sglang_source_revision": contract["target"]["source_revision"],
        "hardware": "one NVIDIA GB300",
        "tensor_parallelism": 1,
        "context_length": runtime["context_length"],
        "mem_fraction_static": runtime["mem_fraction_static"],
        "speculative_algorithm": runtime["speculative_algorithm"],
        "dspark_block_size": runtime["speculative_block_size"],
        "dspark_block_size_source": "checkpoint/runtime default; no --speculative-dspark-block-size override",
        "num_nextn_predict_layers": reversal_contract["constant_runtime"][
            "checkpoint_num_nextn_predict_layers"
        ],
        "num_nextn_predict_layers_source": "checkpoint default; no --json-model-override-args",
        "ragged_verify_mode": contract["candidate"]["ragged_verify_mode"],
        "sps_table": contract["candidate"]["sps_table"],
        "swa_full_tokens_ratio": runtime["swa_full_tokens_ratio"],
        "chunked_prefill_size": runtime["chunked_prefill_size"],
        "cuda_graph_max_bs_decode": 64,
        "cuda_graph_bs_decode": [1, 2, 4, 8, 16, 32, 64],
        "max_running_requests": runtime["max_running_requests"],
        "tool_call_parser": "deepseekv4",
        "reasoning_parser": "deepseek-v4",
        "served_model_name": contract["target"]["served_model"],
        "port": contract["target"]["port"],
        "api_authentication": "required; secret value is read at runtime and is not part of public artifacts",
        "dsml_encoding_patch": {
            "required": True,
            "container_path": "/sgl-workspace/sglang/python/sglang/srt/entrypoints/openai/encoding_dsv4.py",
            "repo_path": "patches/encoding_dsv4.py",
        },
    }


def validate_closed_mechanisms(entries: list[dict[str, str]]) -> None:
    require(len(entries) == 5, "expected five closed mechanisms")
    required = {"mechanism", "result", "scope", "reopen_condition"}
    seen: set[str] = set()
    for entry in entries:
        require(required <= set(entry), f"closed mechanism entry missing required fields: {entry}")
        require(all(entry[field].strip() for field in required), f"empty closed mechanism field: {entry}")
        require(entry["mechanism"] not in seen, f"duplicate closed mechanism {entry['mechanism']}")
        seen.add(entry["mechanism"])


def build_from_receipts() -> tuple[dict[str, Any], dict[str, Any]]:
    receipts = load_receipts()
    contract = receipts["contract"]
    results = receipts["results"]
    decision = receipts["decision"]
    pairs = receipts["pairs"]
    audit = receipts["audit"]
    reversal_contract = receipts["reversal_contract"]
    reversal_decision = receipts["reversal_decision"]
    reversal_pairs = receipts["reversal_pairs"]

    require(contract["iteration"] == 13, "selected contract is not Iteration 13")
    require(results["iteration"] == 13, "selected results are not Iteration 13")
    require(decision["iteration"] == 13, "selected decision is not Iteration 13")
    require(contract["run_id"] == SELECTED_RUN_ID, "selected contract run_id mismatch")
    require(decision["run_id"] == SELECTED_RUN_ID, "selected decision run_id mismatch")
    require(results["outcome"] == "COMPLETE", "selected run did not complete")
    require(not results["blockers"], "selected run has blockers")
    require(decision["gates_pass"] is True, "selected decision gates did not pass")

    gates = results["gates"]
    for contract_gate, result_gate in REQUIRED_GATE_MAP.items():
        require(gates.get(result_gate) is True, f"required gate {contract_gate}/{result_gate} did not pass")
    require(all(gates.values()), "not every reported gate is true")

    require(contract["candidate"]["ragged_verify_mode"] == "static", "recipe run is not static")
    require(contract["candidate"]["sps_table"] == "none", "recipe run configured an SPS table")
    require("num_nextn_predict_layers" not in contract["candidate"], "recipe run contains a NextN override")
    require("json_model_override" not in json.dumps(contract), "recipe run contains a JSON model override")
    require(
        receipts["runtime_mode_text"] == "RUNTIME_MODE_RESULT PASS mode=static sps=none sps_effective=false",
        "runtime-mode receipt did not prove static/no-SPS",
    )

    require(contract["target"]["model_revision"] == MODEL_REVISION, "model revision mismatch")
    require(contract["target"]["image_digest"] == IMAGE_DIGEST, "image digest mismatch")
    require(contract["target"]["source_revision"] == SGLANG_SOURCE_REVISION, "source revision mismatch")
    require(contract["constant_runtime"]["speculative_algorithm"] == "DSPARK", "not DSpark")
    require(contract["constant_runtime"]["speculative_block_size"] == 5, "DSpark block/gamma is not 5")
    require(contract["constant_runtime"]["context_length"] == 1048576, "context length mismatch")
    require(contract["constant_runtime"]["mem_fraction_static"] == 0.9, "mem fraction mismatch")
    require(contract["constant_runtime"]["swa_full_tokens_ratio"] == 0.1, "SWA ratio mismatch")
    require(contract["constant_runtime"]["chunked_prefill_size"] == 8192, "chunked prefill mismatch")
    require(contract["constant_runtime"]["max_running_requests"] == 64, "max-running mismatch")
    require(reversal_contract["constant_runtime"]["checkpoint_num_nextn_predict_layers"] == 3, "checkpoint NextN default is not 3")
    require(reversal_decision["verdict"] == "WIN", "Iteration 16 reversal did not win against one layer")

    throughput = sorted(results["throughput"], key=lambda row: row["C"])
    prefill = sorted(results["prefill"], key=lambda row: row["target"])
    require([row["C"] for row in throughput] == EXPECTED_CONCURRENCY, "throughput rows missing or out of order")
    require([row["target"] for row in prefill] == EXPECTED_PREFILL_TARGETS, "prefill rows missing or out of order")

    throughput_rows = [
        {
            "concurrency": row["C"],
            "aggregate_output_tok_s": row["agg_tok_s"],
            "mean_ttft_s": row["mean_ttft_s"],
            "samples": row["n"],
        }
        for row in throughput
    ]
    prefill_rows = [
        {
            "target_tokens": row["target"],
            "prompt_tokens": row["prompt_tokens"],
            "mean_tok_s": row["mean_tok_s"],
            "ttft_s": row["ttft_s"],
            "tok_s": row["tok_s"],
        }
        for row in prefill
    ]

    primary_values = [row["agg_tok_s"] for row in throughput if row["C"] in PRIMARY_ROWS]
    primary_geomean = geomean(primary_values)
    i0 = audit_iteration(audit, 0)
    i13 = audit_iteration(audit, 13)
    baseline_values = [i0["throughput_tok_s"][str(c)] for c in PRIMARY_ROWS]
    vs_i0_delta = pct(primary_geomean, geomean(baseline_values))
    require(round(primary_geomean, 3) == 2000.701, "Iteration 13 C8-C64 geomean mismatch")
    require(round(vs_i0_delta, 3) == 1.859, "Iteration 13 vs Iteration 0 delta mismatch")
    require(abs(vs_i0_delta) < MATERIALITY_BAND_PCT, "Iteration 13 should be inside the materiality band")
    require(i13["throughput_tok_s"] == {str(row["C"]): row["agg_tok_s"] for row in throughput}, "audit I13 throughput disagrees")
    require(i13["prefill_mean_tok_s"] == {str(row["target"]): row["mean_tok_s"] for row in prefill}, "audit I13 prefill disagrees")

    validate_closed_mechanisms(CLOSED_MECHANISMS)

    compact = audit["headline_findings"]["compact_verify_all_aba"]
    nextn = audit["headline_findings"]["one_nextn_layer_aba"]
    additive = audit["headline_findings"]["additive_sps_runtime"]
    require(compact["primary_geomean_delta_pct"] == 0.901, "compact ABA value mismatch")
    require(nextn["primary_geomean_delta_pct"] == -4.093, "one-layer ABA value mismatch")
    require(additive["primary_geomean_delta_pct"] == -7.145, "additive SPS value mismatch")

    configuration = selected_configuration(contract, reversal_contract)
    claim_boundary = {
        "classification": "equivalent_performance_inside_frozen_band_not_speed_promotion",
        "primary_rows": PRIMARY_ROWS,
        "primary_c8_c64_geomean_tok_s": round(primary_geomean, 3),
        "vs_iteration_0_delta_pct": round(vs_i0_delta, 3),
        "materiality_band_pct": MATERIALITY_BAND_PCT,
        "plain_language": "Recipe v2 is operationally simpler and better proven; it is not a measured speed promotion.",
    }

    evidence = {
        "schema_version": 1,
        "artifact": "dsfve-recipe-v2-evidence",
        "generated_from": "local gitignored receipts by research/build_recipe_v2.py",
        "public_safety": {
            "includes_runtime_host": False,
            "includes_secret_values": False,
            "receipt_fields_are_selected": True,
        },
        "selected_recipe_run": {
            "iteration": 13,
            "run_id": SELECTED_RUN_ID,
            "run_type": contract["run_type"],
            "classification": i13["classification"],
            "verdict": decision["verdict"],
            "reference_run_id": decision["reference_run_id"],
            "configuration": configuration,
            "runtime_receipt": {
                "ragged_verify_mode": "static",
                "sps_table": "none",
                "sps_effective": False,
                "nextn_override": "none",
                "raw_line": receipts["runtime_mode_text"],
            },
            "gates": gates,
            "throughput": throughput_rows,
            "prefill": prefill_rows,
            "claim_boundary": claim_boundary,
            "comparisons": {
                "same_hour_compact_reference": {
                    "reference_run_id": decision["reference_run_id"],
                    "primary_geomean_delta_pct": decision["throughput"]["primary_geomean_delta_pct"],
                    "prefill_geomean_delta_pct": decision["prefill_geomean_delta_pct"],
                    "verdict": decision["verdict"],
                },
                "iteration_0_static_baseline": {
                    "reference_run_id": BASELINE_RUN_ID,
                    "primary_geomean_delta_pct": pairs["comparisons"][BASELINE_RUN_ID]["primary_geomean_delta_pct"],
                    "computed_primary_geomean_delta_pct": round(vs_i0_delta, 3),
                    "prefill_geomean_delta_pct": pairs["comparisons"][BASELINE_RUN_ID]["prefill_geomean_delta_pct"],
                },
            },
            "source_receipts": [
                f"inner-loop/runs/{SELECTED_RUN_ID}/contract.json",
                f"inner-loop/runs/{SELECTED_RUN_ID}/results.json",
                f"inner-loop/runs/{SELECTED_RUN_ID}/decision.json",
                f"inner-loop/runs/{SELECTED_RUN_ID}/paired-comparisons.json",
                f"inner-loop/runs/{SELECTED_RUN_ID}/runtime-mode.txt",
            ],
        },
        "supporting_reversal": {
            "iteration": 16,
            "run_id": REVERSAL_RUN_ID,
            "purpose": "checkpoint-default NextN reversal after the one-layer candidate",
            "checkpoint_num_nextn_predict_layers": 3,
            "decision_verdict": reversal_decision["verdict"],
            "reversal_vs_one_layer_primary_geomean_delta_pct": reversal_decision["throughput"]["primary_geomean_delta_pct"],
            "aba_one_layer_primary_geomean_delta_pct": reversal_pairs["aba_nextn_layer_effect"][
                "one_layer_primary_geomean_delta_pct"
            ],
            "aba_one_layer_prefill_geomean_delta_pct": reversal_pairs["aba_nextn_layer_effect"][
                "one_layer_prefill_geomean_delta_pct"
            ],
            "source_receipts": [
                f"inner-loop/runs/{REVERSAL_RUN_ID}/contract.json",
                f"inner-loop/runs/{REVERSAL_RUN_ID}/decision.json",
                f"inner-loop/runs/{REVERSAL_RUN_ID}/paired-comparisons.json",
            ],
        },
        "campaign_accounting": {
            "audit_path": "research/inner-loop-campaign-audit.json",
            "counts": audit["counts"],
            "headline_findings": audit["headline_findings"],
        },
        "closed_mechanisms": CLOSED_MECHANISMS,
    }

    recipe = {
        "schema_version": 1,
        "recipe_version": "v2",
        "name": "DSFVE single-GB300 Recipe v2",
        "status": "current",
        "derived_from_run_id": SELECTED_RUN_ID,
        "supporting_reversal_run_id": REVERSAL_RUN_ID,
        "configuration": configuration,
        "launch_contract": {
            "script": "launch-dsfv.sh",
            "default_invocation": "./launch-dsfv.sh dspark",
            "do_not_launch_from_script_during_validation": True,
            "environment": {"SGLANG_RAGGED_VERIFY_MODE": "static"},
            "required_flags": {
                "--trust-remote-code": True,
                "--model-path": "/model",
                "--tp": 1,
                "--context-length": 1048576,
                "--mem-fraction-static": 0.90,
                "--speculative-algorithm": "DSPARK",
                "--swa-full-tokens-ratio": 0.1,
                "--chunked-prefill-size": 8192,
                "--cuda-graph-max-bs-decode": 64,
                "--cuda-graph-bs-decode": [1, 2, 4, 8, 16, 32, 64],
                "--max-running-requests": 64,
                "--tool-call-parser": "deepseekv4",
                "--reasoning-parser": "deepseek-v4",
                "--served-model-name": "dsf-vision-exp",
            },
            "forbidden_default_flags_or_mounts": [
                "--speculative-dspark-sps-table-path",
                "--json-model-override-args",
                "/dspark_sps_tp1.json",
            ],
        },
        "measurements": {
            "workload": {
                "prompt_tokens_approx": contract["throughput"]["prompt_tokens_approx"],
                "output_tokens": contract["throughput"]["output_tokens"],
                "temperature": contract["throughput"]["temperature"],
                "reasoning_effort": contract["throughput"]["reasoning_effort"],
            },
            "throughput": throughput_rows,
            "prefill": prefill_rows,
            "claim_boundary": claim_boundary,
        },
        "evidence": {
            "public_safe_evidence": "research/recipe-v2-evidence.json",
            "campaign_audit": "research/inner-loop-campaign-audit.json",
            "do_not_retry": "research/do-not-retry.md",
        },
    }

    validate_public_safety(recipe, "recipe-v2.json")
    validate_public_safety(evidence, "recipe-v2-evidence.json")
    return recipe, evidence


def validate_public_safety(data: dict[str, Any], label: str) -> None:
    text = json_text(data)
    private_lan_probe = ".".join(["192", "168", "1", "9"])
    forbidden = [
        private_lan_probe,
        str(Path.home()),
        "BEGIN " + "PRIVATE KEY",
        "ghp" + "_",
        "sk" + "-",
    ]
    for token in forbidden:
        require(token not in text, f"{label} contains forbidden token {token}")


def validate_public_artifacts(recipe: dict[str, Any], evidence: dict[str, Any]) -> None:
    require(recipe["recipe_version"] == "v2", "recipe version mismatch")
    require(recipe["status"] == "current", "recipe must be current")
    require(recipe["derived_from_run_id"] == evidence["selected_recipe_run"]["run_id"], "recipe/evidence run mismatch")
    require(recipe["configuration"] == evidence["selected_recipe_run"]["configuration"], "recipe/evidence config mismatch")
    require(recipe["measurements"]["throughput"] == evidence["selected_recipe_run"]["throughput"], "throughput mismatch")
    require(recipe["measurements"]["prefill"] == evidence["selected_recipe_run"]["prefill"], "prefill mismatch")
    claim = recipe["measurements"]["claim_boundary"]
    require(claim == evidence["selected_recipe_run"]["claim_boundary"], "claim boundary mismatch")
    require(claim["classification"] == "equivalent_performance_inside_frozen_band_not_speed_promotion", "unsupported claim boundary")
    require("not a measured speed promotion" in claim["plain_language"], "claim boundary must deny speed promotion")
    config = recipe["configuration"]
    require(config["image"] == IMAGE_REF, "recipe image ref mismatch")
    require(config["model_revision"] == MODEL_REVISION, "recipe model revision mismatch")
    require(config["ragged_verify_mode"] == "static", "recipe not static")
    require(config["sps_table"] == "none", "recipe uses SPS")
    require(config["num_nextn_predict_layers"] == 3, "recipe not checkpoint NextN 3")
    require(config["chunked_prefill_size"] == 8192, "recipe prefill mismatch")
    require(config["max_running_requests"] == 64, "recipe max-running mismatch")
    validate_closed_mechanisms(evidence["closed_mechanisms"])
    validate_public_safety(recipe, "recipe-v2.json")
    validate_public_safety(evidence, "recipe-v2-evidence.json")


def check_launcher(recipe: dict[str, Any]) -> None:
    source = LAUNCHER.read_text()
    config = recipe["configuration"]
    require(f'IMAGE="${{IMAGE:-{IMAGE_REF}}}"' in source, "launcher default image is not the tested digest ref")
    require("-e SGLANG_RAGGED_VERIFY_MODE=static" in source, "launcher does not set static ragged mode")
    require('CTX="${2:-1048576}"' in source, "launcher context default mismatch")
    require('MEM="${3:-0.90}"' in source, "launcher mem default mismatch")
    require('--chunked-prefill-size 8192' in source, "launcher chunked prefill mismatch")
    require('--cuda-graph-max-bs-decode 64' in source, "launcher graph max mismatch")
    require('--cuda-graph-bs-decode 1 2 4 8 16 32 64' in source, "launcher graph batch list mismatch")
    require('--max-running-requests 64' in source, "launcher max running mismatch")
    require('--speculative-algorithm DSPARK' in source, "launcher missing DSpark mode")
    require('--swa-full-tokens-ratio 0.1' in source, "launcher missing SWA 0.1 default")
    require('--tool-call-parser deepseekv4' in source, "launcher missing tool-call parser")
    require('--reasoning-parser deepseek-v4' in source, "launcher missing reasoning parser")
    require('patches/encoding_dsv4.py' in source, "launcher missing encoding patch mount")
    for forbidden in recipe["launch_contract"]["forbidden_default_flags_or_mounts"]:
        require(forbidden not in source, f"launcher still contains forbidden default {forbidden}")
    require("--json-model-override-args" not in source, "launcher contains NextN JSON override")
    require(str(config["context_length"]) in source, "launcher/source lacks recipe context")
    require(str(config["chunked_prefill_size"]) in source, "launcher/source lacks recipe prefill size")


def check_do_not_retry_markdown(evidence: dict[str, Any]) -> None:
    require(DO_NOT_RETRY_MD.exists(), "research/do-not-retry.md is missing")
    text = DO_NOT_RETRY_MD.read_text()
    for entry in evidence["closed_mechanisms"]:
        title = entry["mechanism"].replace("_", " ")
        require(title in text, f"do-not-retry.md missing {title}")
    require("reopen" in text.lower(), "do-not-retry.md must include reopen conditions")


def compare_or_write(path: Path, generated: dict[str, Any], check: bool) -> None:
    text = json_text(generated)
    if check:
        require(path.exists(), f"{path.relative_to(ROOT)} is missing")
        actual = path.read_text()
        require(actual == text, f"{path.relative_to(ROOT)} is not reproducible from receipts")
    else:
        path.write_text(text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="compare generated output to committed files")
    args = parser.parse_args()

    try:
        if receipts_available():
            recipe, evidence = build_from_receipts()
            compare_or_write(RECIPE_JSON, recipe, args.check)
            compare_or_write(EVIDENCE_JSON, evidence, args.check)
            receipt_status = "receipts=verified"
        elif args.check:
            recipe = load_json(RECIPE_JSON)
            evidence = load_json(EVIDENCE_JSON)
            receipt_status = "receipts=absent-public-artifact-check-only"
        else:
            load_receipts()
            raise AssertionError("unreachable")

        validate_public_artifacts(recipe, evidence)
        check_launcher(recipe)
        check_do_not_retry_markdown(evidence)
        action = "checked" if args.check else "wrote"
        print(
            "RECIPE_V2_CHECK PASS "
            f"action={action} {receipt_status} "
            f"recipe={RECIPE_JSON.relative_to(ROOT)} evidence={EVIDENCE_JSON.relative_to(ROOT)}"
        )
        return 0
    except (AssertionError, MissingReceipts, KeyError, ValueError) as exc:
        print(f"RECIPE_V2_CHECK FAIL {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

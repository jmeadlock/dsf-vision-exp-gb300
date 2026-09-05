#!/usr/bin/env python3
"""Apply bracket-aware DSFVE SPS requalification decisions."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

PRIMARY_ROWS_DEFAULT = [8, 16, 32, 64]


def geometric_mean(values: list[float]) -> float:
    if not values:
        raise ValueError("cannot compute geometric mean of no values")
    if any(value <= 0 for value in values):
        raise ValueError("geometric mean inputs must be positive")
    return math.prod(values) ** (1.0 / len(values))


def _load_results(root: Path, run_id: str) -> dict[str, Any]:
    return json.loads((root / run_id / "results.json").read_text())


def _rows_by_key(result: dict[str, Any], section: str, key: str) -> dict[Any, dict[str, Any]]:
    return {row[key]: row for row in result.get(section, [])}


def _adjacent_control_row(control_a: dict[str, Any], control_b: dict[str, Any], section: str, key: str, metric: str) -> dict[Any, float]:
    a_rows = _rows_by_key(control_a, section, key)
    b_rows = _rows_by_key(control_b, section, key)
    common = sorted(set(a_rows) & set(b_rows))
    return {item: geometric_mean([float(a_rows[item][metric]), float(b_rows[item][metric])]) for item in common}


def _throughput_decision(candidate: dict[str, Any], control_a: dict[str, Any], control_b: dict[str, Any], primary_rows: list[int]) -> tuple[dict[str, Any], list[float]]:
    reference = _adjacent_control_row(control_a, control_b, "throughput", "C", "agg_tok_s")
    candidate_t = _rows_by_key(candidate, "throughput", "C")
    ratios = {c: float(candidate_t[c]["agg_tok_s"]) / reference[c] for c in sorted(reference) if c in candidate_t}
    primary_ratios = [ratios[c] for c in primary_rows]
    primary_gm = geometric_mean(primary_ratios)
    return {
        "rows": [
            {
                "C": c,
                "adjacent_compact_control_tok_s": round(reference[c], 6),
                "candidate_tok_s": float(candidate_t[c]["agg_tok_s"]),
                "delta_pct": round((ratios[c] - 1.0) * 100.0, 3),
            }
            for c in sorted(ratios)
        ],
        "primary_rows": primary_rows,
        "primary_geomean_delta_pct": round((primary_gm - 1.0) * 100.0, 3),
        "primary_nonregressed": sum(value >= 1.0 for value in primary_ratios),
        "worst_primary_delta_pct": round((min(primary_ratios) - 1.0) * 100.0, 3),
    }, primary_ratios


def _prefill_gm(candidate: dict[str, Any], control_a: dict[str, Any], control_b: dict[str, Any]) -> float:
    reference = _adjacent_control_row(control_a, control_b, "prefill", "target", "mean_tok_s")
    candidate_p = _rows_by_key(candidate, "prefill", "target")
    ratios = [float(candidate_p[target]["mean_tok_s"]) / reference[target] for target in sorted(reference)]
    return geometric_mean(ratios)


def _static_anchor_delta(candidate: dict[str, Any], anchors: list[dict[str, Any]], primary_rows: list[int]) -> dict[str, Any]:
    if len(anchors) < 1:
        return {"available": False}
    if len(anchors) == 1:
        anchor_a = anchor_b = anchors[0]
    else:
        anchor_a, anchor_b = anchors[:2]
    reference = _adjacent_control_row(anchor_a, anchor_b, "throughput", "C", "agg_tok_s")
    candidate_t = _rows_by_key(candidate, "throughput", "C")
    ratios = [float(candidate_t[c]["agg_tok_s"]) / reference[c] for c in primary_rows]
    return {
        "available": True,
        "claim_boundary": "combined_compact_plus_table_vs_static_recipe_v2",
        "primary_geomean_delta_pct": round((geometric_mean(ratios) - 1.0) * 100.0, 3),
    }


def _release_gate_failures(candidate: dict[str, Any], control_a: dict[str, Any], control_b: dict[str, Any], primary_rows: list[int]) -> list[str]:
    failures: list[str] = []
    cand_t = _rows_by_key(candidate, "throughput", "C")
    ctrl_accept = _adjacent_control_row(control_a, control_b, "throughput", "C", "accept_rate_gauge")
    ctrl_ttft = _adjacent_control_row(control_a, control_b, "throughput", "C", "mean_ttft_s")
    accept_ratios = [float(cand_t[c]["accept_rate_gauge"]) / ctrl_accept[c] for c in primary_rows]
    ttft_ratios = [float(cand_t[c]["mean_ttft_s"]) / ctrl_ttft[c] for c in primary_rows]
    if geometric_mean(accept_ratios) < 0.95 or min(accept_ratios) < 0.90:
        failures.append("acceptance regressed beyond SPS release gate")
    if geometric_mean(ttft_ratios) > 1.10:
        failures.append("primary-row TTFT regressed beyond SPS release gate")

    cand_work = _rows_by_key(candidate, "agent_workload", "prompt_class")
    ctrl_a_work = _rows_by_key(control_a, "agent_workload", "prompt_class")
    ctrl_b_work = _rows_by_key(control_b, "agent_workload", "prompt_class")
    for name, row in cand_work.items():
        if not row.get("repetition_ok", True):
            failures.append(f"workload {name} repetition failed")
        if not row.get("tool_fidelity_ok", True):
            failures.append(f"workload {name} tool fidelity failed")
        if row.get("finish_reason") not in {"stop", "length", "unknown", None}:
            failures.append(f"workload {name} abnormal finish_reason={row.get('finish_reason')}")
        if name in ctrl_a_work and name in ctrl_b_work:
            ctrl_decode = geometric_mean([float(ctrl_a_work[name]["decode_tok_s"]), float(ctrl_b_work[name]["decode_tok_s"])])
            ctrl_ttft_w = geometric_mean([float(ctrl_a_work[name]["ttft_s"]), float(ctrl_b_work[name]["ttft_s"])])
            if float(row["decode_tok_s"]) / ctrl_decode < 0.90:
                failures.append(f"workload {name} decode regressed beyond 10%")
            if float(row["ttft_s"]) / ctrl_ttft_w > 1.10:
                failures.append(f"workload {name} TTFT regressed beyond 10%")
    if candidate.get("outcome") != "COMPLETE" or not all(candidate.get("gates", {}).values()):
        failures.append("candidate completion/correctness gates failed")
    return failures


def evaluate(run_dir: Path) -> dict[str, Any]:
    run_dir = Path(run_dir).resolve()
    root = run_dir.parent
    contract = json.loads((run_dir / "contract.json").read_text())
    candidate_cfg = contract.get("candidate", {})
    if candidate_cfg.get("sps_table") == "fine-grained" and candidate_cfg.get("ragged_verify_mode") != "compact":
        raise ValueError("fine-grained SPS requires compact ragged mode; static ragged makes the table a no-op")
    candidate = json.loads((run_dir / "results.json").read_text())
    primary_rows = contract.get("winner_rule", {}).get("primary_rows", PRIMARY_ROWS_DEFAULT)
    control_ids = contract.get("adjacent_compact_controls") or contract.get("compact_control_runs") or []
    if len(control_ids) != 2:
        raise ValueError("candidate evaluation requires exactly two adjacent compact/no-table controls")
    control_a, control_b = [_load_results(root, run_id) for run_id in control_ids]
    for label, control in zip(control_ids, (control_a, control_b)):
        ccfg = control.get("candidate", {})
        if ccfg.get("ragged_verify_mode") != "compact" or ccfg.get("sps_table") != "none":
            raise ValueError(f"adjacent control {label} is not compact/no-table")

    throughput, primary_ratios = _throughput_decision(candidate, control_a, control_b, primary_rows)
    primary_gm = geometric_mean(primary_ratios)
    prefill_gm = _prefill_gm(candidate, control_a, control_b)
    release_failures = _release_gate_failures(candidate, control_a, control_b, primary_rows)
    gates_pass = not release_failures

    if release_failures:
        verdict = "LOSS"
        reason = "At least one correctness, acceptance, TTFT, or workload-shaped release gate failed."
    elif min(primary_ratios) <= 0.90 or primary_gm <= 0.97:
        verdict = "LOSS"
        reason = "The frozen loss threshold was crossed against adjacent compact controls."
    elif primary_gm >= 1.03 and sum(v >= 1.0 for v in primary_ratios) >= 3 and min(primary_ratios) >= 0.95 and prefill_gm >= 0.95:
        verdict = "WIN"
        reason = "Every frozen win threshold was met against adjacent compact controls."
    else:
        verdict = "INCONCLUSIVE"
        reason = "The result lies between frozen win and loss thresholds or needs confirmation."

    anchors = [_load_results(root, run_id) for run_id in contract.get("static_anchor_runs", [])]
    decision = {
        "schema_version": 2,
        "campaign_id": contract.get("campaign_id"),
        "run_id": run_dir.name,
        "comparison_basis": "adjacent_compact_controls",
        "adjacent_compact_controls": control_ids,
        "claims_only_table_delta_from_recipe_v2": False,
        "static_anchor_comparison": _static_anchor_delta(candidate, anchors, primary_rows),
        "verdict": verdict,
        "reason": reason,
        "gates_pass": gates_pass,
        "release_gate_failures": release_failures,
        "throughput": throughput,
        "prefill_geomean_delta_pct": round((prefill_gm - 1.0) * 100.0, 3),
    }
    path = run_dir / "decision.json"
    path.write_text(json.dumps(decision, indent=2, sort_keys=True) + "\n")
    return decision


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    try:
        decision = evaluate(args.run_dir)
    except Exception as exc:
        print(f"DECISION_RESULT BLOCKED reason={exc}")
        return 2
    print(json.dumps(decision, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

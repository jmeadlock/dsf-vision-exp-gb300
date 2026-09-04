#!/usr/bin/env python3
"""Apply a frozen candidate-vs-reference decision rule and write decision.json."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def geometric_mean(values):
    return math.prod(values) ** (1.0 / len(values))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    contract = json.loads((run_dir / "contract.json").read_text())
    candidate = json.loads((run_dir / "results.json").read_text())
    reference_id = contract["compare_to_run"]
    reference_dir = run_dir.parent / reference_id
    reference = json.loads((reference_dir / "results.json").read_text())

    reference_t = {row["C"]: row for row in reference["throughput"]}
    candidate_t = {row["C"]: row for row in candidate["throughput"]}
    primary_rows = contract["winner_rule"]["primary_rows"]
    ratios = {
        c: candidate_t[c]["agg_tok_s"] / reference_t[c]["agg_tok_s"]
        for c in sorted(candidate_t)
    }
    primary_ratios = [ratios[c] for c in primary_rows]
    primary_gm = geometric_mean(primary_ratios)
    nonregressed = sum(value >= 1.0 for value in primary_ratios)

    reference_p = {row["target"]: row for row in reference["prefill"]}
    candidate_p = {row["target"]: row for row in candidate["prefill"]}
    prefill_ratios = [
        candidate_p[target]["mean_tok_s"] / reference_p[target]["mean_tok_s"]
        for target in sorted(reference_p)
    ]
    prefill_gm = geometric_mean(prefill_ratios)
    gates_pass = candidate["outcome"] == "COMPLETE" and all(candidate["gates"].values())

    if not gates_pass:
        verdict = "LOSS"
        reason = "At least one completion or correctness gate failed."
    elif min(primary_ratios) <= 0.90 or primary_gm <= 0.97:
        verdict = "LOSS"
        reason = "The frozen loss threshold was crossed."
    elif (
        primary_gm >= 1.03
        and nonregressed >= 3
        and min(primary_ratios) >= 0.95
        and prefill_gm >= 0.95
    ):
        verdict = "WIN"
        reason = "Every frozen win threshold was met."
    else:
        verdict = "INCONCLUSIVE"
        reason = "The result lies between the frozen win and loss thresholds; paired replication is required."

    decision = {
        "schema_version": 1,
        "iteration": candidate["iteration"],
        "run_id": run_dir.name,
        "reference_run_id": reference_id,
        "verdict": verdict,
        "reason": reason,
        "gates_pass": gates_pass,
        "throughput": {
            "rows": [
                {
                    "C": c,
                    "reference_tok_s": reference_t[c]["agg_tok_s"],
                    "candidate_tok_s": candidate_t[c]["agg_tok_s"],
                    "delta_pct": round((ratios[c] - 1.0) * 100.0, 3),
                }
                for c in sorted(ratios)
            ],
            "primary_rows": primary_rows,
            "primary_geomean_delta_pct": round((primary_gm - 1.0) * 100.0, 3),
            "primary_nonregressed": nonregressed,
            "worst_primary_delta_pct": round((min(primary_ratios) - 1.0) * 100.0, 3),
        },
        "prefill_geomean_delta_pct": round((prefill_gm - 1.0) * 100.0, 3),
    }
    path = run_dir / "decision.json"
    path.write_text(json.dumps(decision, indent=2, sort_keys=True) + "\n")
    print(json.dumps(decision, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

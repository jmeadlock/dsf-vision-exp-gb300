#!/usr/bin/env python3
"""Offline A-fit / B-validate for the sps-requal-20260904-now campaign.

Fits the pinned PR #37815 profiler on profile A (P0) at mbin_w in {64,6,1},
scores each table on held-out profile B (P1), and writes:
  <out>/table-w{64,6,1}.json, <out>/held-out-metrics.json, <out>/fit-manifest.json
Then runs the campaign gate's select_held_out_width for the decision.
Read-only against the box; no serving.
"""
from __future__ import annotations

import hashlib
import json
import math
import statistics
import sys
from pathlib import Path

REPO = Path("/Users/jamesmeadlock/hermes/dsfve-sps-requalification-20260904")
CAMP = REPO / "inner-loop/campaigns/sps-requal-20260904-now"
PROFILER_DIR = Path("/tmp/spsfit/benchmark")
sys.path.insert(0, str(PROFILER_DIR))
sys.path.insert(0, str(REPO / "inner-loop/scripts"))
import dspark_sps_profiler as prof  # noqa: E402
from sps_profile_gate import select_held_out_width, EXPECTED_WIDTHS  # noqa: E402

A = CAMP / "runs/sps-requal-20260904-now-P0"
B = CAMP / "runs/sps-requal-20260904-now-P1"
OUT = CAMP / "artifacts/fit-r1"
OUT.mkdir(parents=True, exist_ok=True)
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()


def cells(run: Path) -> list[dict]:
    rounds = prof.load_round_summaries(rounds_path=run / "dsfv-sps-profile.rounds.jsonl")
    return prof.summaries_to_cells(summaries=rounds)


def score(table, held: list[dict]) -> dict:
    errs = []
    for c in held:
        pred = prof.fitted_step_time(table=table, bs=c["bs"], m=c["M"])
        if not math.isfinite(pred) or pred < 0:
            raise SystemExit(f"non-finite/negative prediction bs={c['bs']} M={c['M']} pred={pred}")
        errs.append((pred - c["T"]) * 1000.0)  # ms, signed
    absd = [abs(e) for e in errs]
    return {
        "n": len(errs),
        "mae_ms": statistics.fmean(absd),
        "rmse_ms": math.sqrt(statistics.fmean(e * e for e in errs)),
        "max_error_ms": max(absd),
        "mean_bias_ms": statistics.fmean(errs),
    }


def repeat_noise_ms(run: Path) -> float:
    """Collection noise: mean over cells of the spread (max-min) of T across the 3 repeats, in ms."""
    by = {}
    for c in cells(run):
        by.setdefault((c["bs"], c["M"]), []).append(c["T"] * 1000.0)
    return statistics.fmean(max(v) - min(v) for v in by.values())


def main() -> None:
    a_cells, b_cells = cells(A), cells(B)
    assert {(c["bs"], c["M"]) for c in a_cells} == {(c["bs"], c["M"]) for c in b_cells}, "A/B cell coverage differs"
    metrics, tables = {}, {}
    for w in EXPECTED_WIDTHS:
        table = prof.build_additive_table_from_cells(cells=a_cells, mbin_w=w)
        path = OUT / f"table-w{w}.json"
        path.write_text(table.to_json() if hasattr(table, "to_json") else json.dumps(prof.msgspec.to_builtins(table)), encoding="utf-8")
        tables[w] = path
        metrics[str(w)] = {**score(table, b_cells), "self_fit_mae_ms": score(table, a_cells)["mae_ms"],
                           "m_probes": len(table.m_probes), "table_sha256": sha(path)}
    noise = repeat_noise_ms(B)
    (OUT / "held-out-metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")
    try:
        sel = select_held_out_width(metrics, noise_mae_ms=noise)
        decision = {"result": "PASS", **{k: v for k, v in sel.items() if k != "metrics"}}
    except Exception as exc:  # gate raises ProfileGateError
        decision = {"result": "FAIL", "reason": str(exc)}
    manifest = {
        "campaign_id": "sps-requal-20260904-now",
        "fit_source": "profile A only (P0); validated on held-out profile B (P1); no pooled refit",
        "profiler_sha256": sha(PROFILER_DIR / "dspark_sps_profiler.py"),
        "sps_module_sha256": sha("/tmp/spsfit/srt/speculative/dspark_components/dspark_sps.py"),
        "raw_profile_a_sha256": sha(A / "dsfv-sps-profile.rounds.jsonl"),
        "raw_profile_a_records_sha256": sha(A / "dsfv-sps-profile.records.jsonl"),
        "raw_profile_b_sha256": sha(B / "dsfv-sps-profile.rounds.jsonl"),
        "raw_profile_b_records_sha256": sha(B / "dsfv-sps-profile.records.jsonl"),
        "widths": EXPECTED_WIDTHS,
        "tables": {str(w): {"path": str(p), "sha256": sha(p)} for w, p in tables.items()},
        "held_out_metrics": metrics,
        "held_out_noise_mae_ms": noise,
        "selection": decision,
    }
    (OUT / "fit-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"FIT_MANIFEST sha256={sha(OUT / 'fit-manifest.json')}")
    print(f"{'w':>3} {'heldout_mae':>11} {'rmse':>8} {'max':>8} {'bias':>8} {'selffit':>8} m_probes")
    for w in EXPECTED_WIDTHS:
        m = metrics[str(w)]
        print(f"{w:>3} {m['mae_ms']:>11.4f} {m['rmse_ms']:>8.4f} {m['max_error_ms']:>8.4f} {m['mean_bias_ms']:>8.4f} {m['self_fit_mae_ms']:>8.4f} {m['m_probes']}")
    print(f"noise(B repeat spread) = {noise:.4f} ms")
    print("SELECTION", json.dumps(decision))


if __name__ == "__main__":
    main()

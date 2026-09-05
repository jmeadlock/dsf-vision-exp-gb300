#!/usr/bin/env python3
"""Gate DSFVE additive SPS profile receipts and held-out width selection."""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

EXPECTED_BS = [1, 2, 3, 4, 5, 6, 7, 8]
EXPECTED_FRACS = [0.2, 0.4, 0.6, 0.8, 1.0]
EXPECTED_REPEATS = 3
EXPECTED_VERIFY_NUM_DRAFT_TOKENS = 6
EXPECTED_WIDTHS = [64, 6, 1]
EXPECTED_M_MIN = 2
EXPECTED_M_MAX = 48


class ProfileGateError(ValueError):
    pass


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ProfileGateError(f"cannot read {path.name}: {exc}") from exc


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    except (OSError, json.JSONDecodeError) as exc:
        raise ProfileGateError(f"cannot read {path.name}: {exc}") from exc


def _derived_m(bs: int, frac: float) -> int:
    # Mirrors the pinned PR #37815 profiler's off-diagonal budget pin:
    # M = bs + int(frac * bs * (verify_num_draft_tokens - 1)).
    # (r1 fix: the earlier ceil(bs * gamma * frac) form disagreed with the
    # profiler at e.g. bs=2 frac=0.2 -> 4, not 3, and blocked a clean P0.)
    budget = int(frac * bs * (EXPECTED_VERIFY_NUM_DRAFT_TOKENS - 1))
    return bs + budget


def gate_profile_run(run_dir: Path) -> dict[str, Any]:
    run = Path(run_dir)
    manifest = _load_json(run / "dsfv-sps-profile.json.manifest.json")
    rounds = _load_jsonl(run / "dsfv-sps-profile.rounds.jsonl")

    if manifest.get("batch_size_per_rank_sweep") != EXPECTED_BS:
        raise ProfileGateError("manifest batch_size_per_rank_sweep mismatch")
    if manifest.get("fracs") != EXPECTED_FRACS:
        raise ProfileGateError("manifest fracs mismatch")
    if manifest.get("repeats") != EXPECTED_REPEATS:
        raise ProfileGateError("manifest repeats mismatch")
    if manifest.get("verify_num_draft_tokens") != EXPECTED_VERIFY_NUM_DRAFT_TOKENS:
        raise ProfileGateError("manifest verify_num_draft_tokens mismatch")
    settings = manifest.get("settings") or {}
    if settings.get("input_len") != 16 or float(settings.get("temperature", -1)) != 1.0:
        raise ProfileGateError("manifest profile input/temperature mismatch")
    if settings.get("min_steady_steps") != 32 or float(settings.get("min_steady_seconds", -1)) != 10.0:
        raise ProfileGateError("manifest steady-state bounds mismatch")

    expected_cells = {
        (repeat, bs, frac)
        for repeat in range(EXPECTED_REPEATS)
        for bs in EXPECTED_BS
        for frac in EXPECTED_FRACS
    }
    actual_cells = Counter(
        (int(row["repeat"]), int(row["batch_size_per_rank"]), float(row["frac"]))
        for row in rounds
    )
    if set(actual_cells) != expected_cells or any(count != 1 for count in actual_cells.values()):
        raise ProfileGateError("profile sweep coverage/duplication mismatch")

    derived_ms = []
    for row in rounds:
        bs = int(row["batch_size_per_rank"])
        frac = float(row["frac"])
        expected_m = _derived_m(bs, frac)
        observed_m = int(row["batch_tokens"])
        if observed_m != expected_m:
            raise ProfileGateError(
                f"derived M mismatch repeat={row['repeat']} bs={bs} frac={frac} expected={expected_m} got={observed_m}"
            )
        if not math.isfinite(float(row["steps_per_sec"])) or float(row["steps_per_sec"]) <= 0:
            raise ProfileGateError("non-positive steps_per_sec")
        if float(row.get("match_fraction", 0.0)) < 0.9:
            raise ProfileGateError("match_fraction below 0.9")
        derived_ms.append(observed_m)

    if min(derived_ms) != EXPECTED_M_MIN or max(derived_ms) != EXPECTED_M_MAX:
        raise ProfileGateError("derived M range must be 2 through 48")

    return {
        "result": "PASS",
        "cells": len(rounds),
        "batch_size_per_rank": EXPECTED_BS,
        "fracs": EXPECTED_FRACS,
        "m_min": min(derived_ms),
        "m_max": max(derived_ms),
        "fit_mbin_widths": EXPECTED_WIDTHS,
    }


def _metric(metrics: dict[str, Any], width: int, name: str) -> float:
    row = metrics.get(str(width)) or metrics.get(width)
    if not isinstance(row, dict) or name not in row:
        raise ProfileGateError(f"missing held-out metric width={width} field={name}")
    value = float(row[name])
    # r2 fix: mean_bias_ms is signed by definition; only magnitude metrics must be >= 0.
    if not math.isfinite(value) or (name != "mean_bias_ms" and value < 0):
        raise ProfileGateError(f"non-finite/nonnegative metric width={width} field={name}")
    return value


def select_held_out_width(metrics: dict[str, Any], *, noise_mae_ms: float = 0.0) -> dict[str, Any]:
    for width in EXPECTED_WIDTHS:
        for field in ("mae_ms", "rmse_ms", "max_error_ms", "mean_bias_ms"):
            _metric(metrics, width, field)
    base_mae = _metric(metrics, 64, "mae_ms")
    base_rmse = _metric(metrics, 64, "rmse_ms")
    base_max = _metric(metrics, 64, "max_error_ms")
    base_bias = abs(_metric(metrics, 64, "mean_bias_ms"))

    candidates: list[tuple[int, float]] = []
    for width in (6, 1):
        mae = _metric(metrics, width, "mae_ms")
        if mae > base_mae * 0.5:
            continue
        if _metric(metrics, width, "rmse_ms") > base_rmse:
            continue
        if _metric(metrics, width, "max_error_ms") > base_max:
            continue
        if abs(_metric(metrics, width, "mean_bias_ms")) > base_bias:
            continue
        candidates.append((width, mae))
    if not candidates:
        raise ProfileGateError("no width improves held-out MAE by at least 50% without worsening secondary gates")

    by_width = {width: mae for width, mae in candidates}
    if 1 in by_width and 6 in by_width and abs(by_width[1] - by_width[6]) <= noise_mae_ms:
        selected = 6
        reason = "width_1_and_6_tied_inside_noise_choose_6"
    else:
        selected = min(candidates, key=lambda item: item[1])[0]
        reason = "lowest_held_out_mae"
    return {
        "selected_mbin_w": selected,
        "reason": reason,
        "metrics": metrics,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--held-out-metrics", type=Path)
    args = parser.parse_args()
    try:
        result = gate_profile_run(args.run_dir)
        if args.held_out_metrics:
            result["held_out_selection"] = select_held_out_width(_load_json(args.held_out_metrics))
    except ProfileGateError as exc:
        print(f"SPS_PROFILE_RESULT FAIL reason={exc}")
        return 2
    print(
        "SPS_PROFILE_RESULT PASS "
        f"cells={result['cells']} bs={result['batch_size_per_rank']} fracs={result['fracs']} "
        f"m_range={result['m_min']}..{result['m_max']} widths={result['fit_mbin_widths']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

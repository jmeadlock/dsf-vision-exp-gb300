# Campaign `sps-requal-20260904-now` — fine-grained SPS requalification

**Outcome: NO_WIDTH_PASSES → CONTROL=STOP. No serving candidate. Recipe v2 stands.**

## Question

SGLang PR #37815 (`085a5e2a`) added fine-grained M-bin fitting to the DSpark additive-SPS profiler and reported large held-out prediction-error gains. That satisfied the do-not-retry reopen condition for SPS tables. Does a finer table predict verify-step cost better on this box, and if so does it beat Recipe v2?

## Design

Fit on profile A only, validate on independent profile B, then bracket. Gate: selected width must improve held-out MAE by ≥50% versus width 64 without worsening RMSE, max error, or |bias|. If no width passes, stop at calibration.

| card | phase | released | outcome |
|---|---|---|---|
| P0 | additive-SPS profile A (compact, SIMULATE_ACC_LEN=1.0) | yes (attempt 2) | COMPLETE, 120/120 cells |
| P1 | additive-SPS profile B, cold relaunch | yes | COMPLETE, 120/120 cells |
| R0/C0/S1/C1/S2/C2/R1 | anchor / control / candidate bracket | **never released** | — |

Both profile runs passed tier-0 correctness first: multimodal smoke, six-turn tool replay, 64 opaque identifiers, C64 repetition audit with 0 flags.

## Held-out result (fit P0 → score P1)

| width | MAE ms | RMSE ms | max ms | bias ms | M-bins |
|---|---|---|---|---|---|
| 64 | 0.1372 | 0.1812 | 0.6404 | +0.0038 | 2 |
| 6 | 0.1440 | 0.1877 | 0.7080 | +0.0097 | 9 |
| 1 | 0.1460 | 0.1969 | 0.8411 | +0.0098 | 25 |

Within-run repeat spread (P1): 0.2955 ms. A↔B per-cell median disagreement: 0.138 ms mean, 0.405 ms max.

Finer widths lose on every metric. The self-fit on P0 looked marginally better at width 1 (0.1146 vs 0.1215 ms) — that was the finer bins fitting noise, which held-out validation exposed.

## Why

The verify step is flat in M on TP1/GB300 for this model. Per-cell median step time, ms (P0/P1):

- bs=1: M2 8.65/8.57 … M6 8.66/8.64
- bs=4: M8 13.09/13.03 … M24 13.21/13.11
- bs=8: M16 16.34/15.94 … M48 16.22/16.40

The whole M range moves step time by less than run-to-run noise. The additive table models T = bias + α(bs) + θ(M); here θ is ~10⁻⁵–10⁻⁴ s and there is nothing for finer bins to resolve. This is consistent with every prior SPS loss on this surface (−3.6%, −4.3%, −7.1%): the per-request α term dominates, and a scheduler that budgets by M is budgeting a variable that barely matters.

## Harness defects found and fixed (contract revisions r1, r2)

| defect | fix |
|---|---|
| Dispatcher was a child of the agent session; session death → ssh SIGPIPE → remote cleanup trap (P0 attempt 1 aborted at 36/120) | Dispatch under launchd (`caffeinate`), log is authoritative |
| `systemctl --user` hard-stop timer died with the user manager (`Linger=no`) | Two system-scope transient timers: 06:00 STOP latch, 06:15 container enforcement |
| `sps_profile_gate` derived M as `ceil(bs·γ·frac)`; profiler uses `bs + int(frac·bs·(γ−1))` | Formula matched to pinned source; test fixture corrected |
| Profile branch of the runner exited before the NextN-override receipt | Receipt emitted inside the profile branch |
| `_metric` rejected signed `mean_bias_ms` | Only magnitude metrics must be ≥0 |

P0 attempt 1 is archived as `runs/…-P0.attempt1-aborted/` with salvaged partial records; it is evidence, not a run. The released P0 card is byte-identical across r0→r2; `queue.r0-snapshot/` holds the original hashes.

## Artifacts

- `artifacts/fit-r1/fit-manifest.json` — all hashes, metrics, selection decision
- `artifacts/fit-r1/table-w{64,6,1}.json` — fitted tables (none promoted)
- `artifacts/dspark_sps_profiler-pr37815-085a5e2.py` — pinned profiler
- `ledger.jsonl` — iteration, contract-revision, and selection events
- `runs/…-P0/`, `runs/…-P1/` — full receipts (gitignored raw logs excluded from public copy)

## Not run tonight, deliberately

No R0 re-anchor, no compact control, no S-card. Running a bracket whose candidate cannot be calibrated would have produced a number without a mechanism. Also not run: NextN=1 retry, image upgrade, session radix cache — each is a separate card family.

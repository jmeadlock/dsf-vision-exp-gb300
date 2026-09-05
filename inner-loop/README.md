# DSFVE inner loop

A no-Kanban, fail-closed experiment loop for one GB300. The filesystem is the handoff and audit trail.

## Campaign status

The September 3-4 campaign is closed with `CONTROL=STOP`. It used 20 iteration numbers: 10 valid performance/baseline/control runs, 2 calibration-only runs, and 8 blocked or aborted harness/preflight runs. No candidate earned a measured speed promotion.

The outer-loop repository update selected **Recipe v2** from Iteration 13: static ragged verification, no SPS table, checkpoint-default NextN, DSpark gamma/default block size 5, 1M context, memory 0.90, SWA 0.1, chunked prefill 8192, and the DSML encoding patch. Iteration 16 supplies the default-NextN reversal evidence. Recipe v2 is operationally simpler and better proven, with equivalent throughput inside the frozen ±3% band.

- Current recipe: [`../RECIPE_V2.md`](../RECIPE_V2.md)
- Machine-readable recipe: [`../recipe-v2.json`](../recipe-v2.json)
- Recipe v2 evidence: [`../research/recipe-v2-evidence.json`](../research/recipe-v2-evidence.json)
- Do-not-retry ledger: [`../research/do-not-retry.md`](../research/do-not-retry.md)
- Narrative retrospective: [`../INNER_LOOP_RETROSPECTIVE.md`](../INNER_LOOP_RETROSPECTIVE.md)
- Generated receipt audit: [`../research/inner-loop-campaign-audit.md`](../research/inner-loop-campaign-audit.md)
- Audit source: [`../research/inner-loop-campaign-audit.json`](../research/inner-loop-campaign-audit.json)

## September 4 SPS requalification (campaign `sps-requal-20260904-now`)

Closed with `CONTROL=STOP` at the calibration gate. Two clean additive-SPS profiles were collected (P0, P1) and the fine-grained PR #37815 fit was validated held-out; no width cleared the 50% MAE gate, so no serving candidate ran and Recipe v2 stands. Details, hashes, and the revised reopen condition: [`../research/do-not-retry.md`](../research/do-not-retry.md) and [`campaigns/sps-requal-20260904-now/README.md`](campaigns/sps-requal-20260904-now/README.md).

Harness changes landed by this campaign (schema v2, campaign-local state): campaign-scoped `CONTROL`/`RELEASE`/`queue/`/`runs/`, `sps_profile_gate.py` (derived-M formula matches the pinned profiler; signed bias), the profile-branch NextN receipt, and `fit_select_sps.py` for offline A-fit/B-validate.

## Control

- `CONTROL` contains `RUN` only while another phase may start; the closed campaign now contains `STOP`.
- Change it to `STOP` to prevent the next phase or iteration.
- A Station-side `flock` permits one experiment at a time.
- Every run stops its experiment container from an exit trap.
- This loop never restores production. Restoration is a separate, explicit operation.

## Contract

Each iteration freezes its JSON contract into `runs/<run-id>/contract.json` before launch. A run is complete only when the parser finds every expected benchmark row, every prefill row, and every named gate. Missing evidence yields `BLOCKED`.

## Layout

- `queue/` — frozen current contract and proposed later deltas.
- `runs/` — immutable local receipts copied back from the Station; gitignored.
- `ledger.jsonl` — append-only lifecycle and decision events; gitignored.
- `scripts/run_remote_iteration.sh` — Station executor under `flock`.
- `scripts/summarize_iteration.py` — fail-closed result parser.
- `scripts/replay_gate.py` — six replayed DSML tool turns.
- `scripts/opaque_identifier_gate.py` — concurrent exact-ID fidelity gate.

## Iteration zero

Starts the exact held production container, verifies its immutable receipt, exercises protocol, real vision, tool history, concurrent identifier fidelity, throughput C1/C4/C8/C16/C32/C64, cold prefill 8K-256K, and C64 repetition, then stops it again.

## Recipe-specific reproducibility

`research/build_recipe_v2.py` derives the public-safe Recipe v2 JSON artifacts from the ignored local receipts when those receipts are present. It also checks that `launch-dsfv.sh` still defaults to the digest-pinned static/no-SPS/checkpoint-NextN launch without running Docker:

```bash
python3 research/build_recipe_v2.py --check
```

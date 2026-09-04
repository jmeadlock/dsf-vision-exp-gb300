# DS4FVis-exp inner loop

A no-Kanban, fail-closed experiment loop for one GB300. The filesystem is the handoff and audit trail.

## Campaign status

The September 3–4 campaign is closed with `CONTROL=STOP`. It used 20 iteration numbers: 10 valid performance/baseline/control runs, 2 calibration-only runs, and 8 blocked or aborted harness/preflight runs. No candidate recipe was promoted. Production remains preserved and stopped pending a separate restore instruction.

- Narrative retrospective: [`../INNER_LOOP_RETROSPECTIVE.md`](../INNER_LOOP_RETROSPECTIVE.md)
- Generated receipt audit: [`../research/inner-loop-campaign-audit.md`](../research/inner-loop-campaign-audit.md)
- Audit source: [`../research/inner-loop-campaign-audit.json`](../research/inner-loop-campaign-audit.json)

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
- `runs/` — immutable receipts copied back from the Station.
- `ledger.jsonl` — append-only lifecycle and decision events.
- `scripts/run_remote_iteration.sh` — Station executor under `flock`.
- `scripts/summarize_iteration.py` — fail-closed result parser.
- `scripts/replay_gate.py` — six replayed DSML tool turns.
- `scripts/opaque_identifier_gate.py` — concurrent exact-ID fidelity gate.

## Iteration zero

Starts the exact held production container, verifies its immutable receipt, exercises protocol, real vision, tool history, concurrent identifier fidelity, throughput C1/C4/C8/C16/C32/C64, cold prefill 8K–256K, and C64 repetition, then stops it again.

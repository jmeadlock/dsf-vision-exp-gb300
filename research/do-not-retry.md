# Recipe v2 do-not-retry ledger

This is a scoped exclusion list for the DSFVE single-GB300 Recipe v2 release. It does not ban future research; it records mechanisms that should not be reintroduced into the current recipe without new evidence.

The scope for every item below is the pinned Recipe v2 surface: `deepseek-ai/DeepSeek-V4-Flash-Vision-Exp` at revision `6821d6ad3681a4b137b066b76094fa82ebd0a380`, SGLang image `lmsysorg/sglang@sha256:7ac467a50508b7029a23e846c150998fdd26d95c1cfd377ea7e74e28374486a6`, one GB300, TP1, DSpark, 1M context, memory 0.90, SWA 0.1, chunked prefill 8192, C8/C16/C32/C64 primary geometric mean, and the frozen correctness gates.

## compact verify all as speedup

- **Result:** closed as neutral. The compact → static → compact ABA bracket measured compact verify-all at **+0.901%** primary C8-C64 and **+0.217%** cold-prefill, inside the frozen ±3% materiality band.
- **Scope:** ragged verification mode on the pinned model/image/source with no SPS table and checkpoint-default NextN.
- **Reopen condition:** only for a new image/source revision, hardware/workload change, or a new mechanism; use another frozen alternating control sequence before claiming a speedup.

## dspark sps tables

- **Result:** not promoted. The inherited SPS table lost **−3.561%** primary in Iteration 2. The calibrated SPS table lost **−4.327%** against its frozen older compact reference in Iteration 10. Removing that table in the same hour changed only **+0.464%**, so the causal claim remained narrow. The corrected additive SPS runtime then lost **−7.145%** primary in Iteration 19, including **−21.170%** at C8.
- **Scope:** inherited, diagonal calibrated, and additive calibrated DSpark SPS tables on the pinned single-GB300 DSFVE runtime.
- **Reopen condition:** only if SGLang changes SPS semantics/profiling or a new profiler supplies a new artifact; test it against a same-hour no-table control with mounted-artifact digest proof.

## one nextn predict layer

- **Result:** rejected. Forcing `num_nextn_predict_layers=1` lost in Iteration 15; the default → one → default ABA estimate put one layer at **−4.093%** primary and **−1.538%** prefill. Iteration 16 restored the checkpoint default and won **+3.608%** against the one-layer run.
- **Scope:** NextN layer count for this checkpoint and pinned SGLang digest/source.
- **Reopen condition:** only for a new checkpoint or runtime implementation; verify the live NextN value and run an immediate reversal control.

## dspark gamma 3

- **Result:** parked from the September 2-3 baseline. Gamma 3 did not beat the gamma 5/default path on this box and had lower measured acceptance. Recipe v2 keeps the checkpoint/runtime default gamma 5 with no block-size override.
- **Scope:** single-GB300 TP1 DSpark serving for DSFVE. The old gamma-3 measurement was taken before the inner-loop harness and was not a promotable isolated v2 delta.
- **Reopen condition:** only as a fresh one-delta card against Recipe v2, without chunked-prefill or SPS confounds, if a new runtime or workload makes gamma smaller plausible.

## chunked prefill 4096

- **Result:** rejected before Recipe v2. The September 2-3 ledger measured `--chunked-prefill-size 4096` as slower on one GB300: TTFT roughly doubled and C8/C16 were about 20% lower than the no-4096 path.
- **Scope:** pinned DSFVE one-GB300 serving with the 1M-context SGLang preview image.
- **Reopen condition:** only after a chunked-prefill implementation change or a workload that directly values a different prefill/decode tradeoff; rerun throughput and cold-prefill gates together.

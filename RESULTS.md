# DS4F Vision-Exp on one GB300 — results ledger (2026-09-02 through 2026-09-04)

Box: DGX Station GB300, 269 GB HBM, driver 595, CUDA 13.2. Model `deepseek-ai/DeepSeek-V4-Flash-Vision-Exp` @ `6821d6ad3681a4b137b066b76094fa82ebd0a380` (48 shards, 167.83 GB, native FP4 experts + FP8 dense + BF16 vision tower + DSpark head). All runs here are TP1.

The current default is **Recipe v2**, documented in [`RECIPE_V2.md`](RECIPE_V2.md) and [`recipe-v2.json`](recipe-v2.json). It is the Iteration 13 static/no-SPS run, informed by the Iteration 16 default-NextN reversal. It is an equivalent-throughput, operationally simpler recipe, not a measured speed promotion.

## Recipe v2 measured result

Workload: `bench_dsf.py`, same contract as catid 0731, ~7K prompt tokens, 1,024 output tokens, temperature 0, warm runs. Primary metric: C8/C16/C32/C64 geometric mean.

| Concurrency | Aggregate output tok/s |
|---:|---:|
| C1 | 329.2 |
| C4 | 748.7 |
| C8 | 1260.7 |
| C16 | 1786.0 |
| C32 | 2383.6 |
| C64 | 2985.4 |

C8-C64 geometric mean: **2000.701 tok/s**, **+1.859%** versus Iteration 0. That sits inside the frozen ±3% materiality band.

Cold-prefill means from the same Iteration 13 receipt:

| target | prompt tokens | mean tok/s |
|---:|---:|---:|
| 8K | 6,479 | 33,935 |
| 32K | 25,905 | 32,561 |
| 64K | 52,011 | 35,669 |
| 128K | 103,669 | 35,662 |
| 256K | 207,405 | 31,650 |

## What changed in v2

| Surface | Previous public default | Recipe v2 |
|---|---|---|
| SGLang image | mutable preview tag | digest-pinned `lmsysorg/sglang@sha256:7ac467a50508b7029a23e846c150998fdd26d95c1cfd377ea7e74e28374486a6` |
| Ragged verification | implicit image default | explicit `SGLANG_RAGGED_VERIFY_MODE=static` |
| SPS table | configured and mounted | removed from default launch; historical artifact retained only for provenance |
| NextN | checkpoint default in practice | checkpoint default documented as three layers; no JSON override |
| DSpark block/gamma | default γ5 | default γ5 retained; no block-size override |
| Correctness contract | smoke, needle, prefill, repetition | adds frozen runtime-mode proof, six-turn replay, opaque identifiers, receipt sanitization, clean teardown |
| Claim | row 8 was locked before the campaign | v2 is current because it is simpler and better proven, not because it is faster |

## September 3-4 inner-loop closeout

Generated audit: [`research/inner-loop-campaign-audit.json`](research/inner-loop-campaign-audit.json) / [`research/inner-loop-campaign-audit.md`](research/inner-loop-campaign-audit.md). Recipe v2 evidence: [`research/recipe-v2-evidence.json`](research/recipe-v2-evidence.json).

| I | Class | Verdict | Candidate/purpose | Evidence |
|---:|---|---|---|---|
| 0 | `valid_baseline` | **BASELINE** | incumbent static runtime | Reproduced the incumbent with every correctness and teardown gate green. |
| 1 | `valid_performance` | **INCONCLUSIVE** | compact verify-all | Primary aggregate improved, but C16 regressed beyond the per-row guardrail; replication required. |
| 2 | `valid_performance` | **LOSS** | compact with inherited SPS table | The inherited table crossed the frozen primary loss threshold. |
| 3 | `harness_blocked` | **BLOCKED** | static SPS profile attempt | Synthetic acceptance was active during semantic gates, so the runner rejected the evidence before profiling. |
| 4 | `administrative_abort` | **ABORTED** | split SPS profile retry | CONTROL remained on the Iteration 3 review hold; no container was created. |
| 5 | `harness_blocked` | **BLOCKED** | split SPS profile retry | A literal redaction placeholder reached authenticated readiness and correctly received HTTP 401. |
| 6 | `harness_blocked` | **BLOCKED** | split SPS profile with runtime receipt | Tier-0 required a profile-only runtime field; the receipt predicate was wrong, not the model. |
| 7 | `harness_blocked` | **BLOCKED** | split SPS profile with stock profiler | Ordinary Tier-0 passed, but the stock profiler could not authenticate; profiling remained empty. |
| 8 | `calibration_only` | **PASS_CALIBRATION** | isolated static SPS calibration | Produced a valid immutable measurement artifact after ordinary correctness and isolated loopback profiling. |
| 9 | `local_preflight_blocked` | **BLOCKED** | calibrated SPS staged candidate | macOS Bash 3.2 lacks `readarray`; dispatch stopped before SSH or Station side effects. |
| 10 | `valid_performance` | **LOSS** | compact with calibrated SPS table | The frozen comparison crossed the primary loss threshold; the calibration artifact was not promoted. |
| 11 | `harness_blocked` | **BLOCKED** | compact verify-all control | The contract placed container at the wrong path and failed before Docker; local schema validation was added. |
| 12 | `valid_performance` | **INCONCLUSIVE** | compact verify-all repeat | Same-hour SPS removal was effectively flat and the earlier C8 uplift did not reproduce. |
| 13 | `valid_control` | **INCONCLUSIVE** | static no-SPS paired control | Static and compact were effectively tied in the adjacent pair. This run became Recipe v2 because it is simpler and better proven. |
| 14 | `valid_control` | **INCONCLUSIVE** | compact verify-all opposite-order control | The compact-static-compact ABA bracket closed compact verify-all as neutral. |
| 15 | `valid_performance` | **LOSS** | compact with one NextN layer | The runtime-confirmed one-layer override crossed the frozen primary loss threshold. |
| 16 | `valid_control` | **WIN_CONTROL** | checkpoint-default NextN reversal | The immediate reversal beat one layer and completed a default-one-default causal bracket. |
| 17 | `calibration_only` | **PASS_CALIBRATION** | additive SPS calibration | All 132 requested cells and fit gates passed; the artifact remained measurement-only. |
| 18 | `harness_blocked` | **BLOCKED** | compact with additive SPS table | The workload completed, but `decision_rule` was not the evaluator's `winner_rule`; raw metrics are inadmissible as a verdict. |
| 19 | `valid_performance` | **LOSS** | corrected additive SPS runtime | The corrected frozen contract crossed the primary loss threshold with all correctness gates green. |

Closeout counts: 20 iteration numbers, 10 valid performance/baseline/control runs, 2 calibration-only runs, 8 blocked or aborted harness/preflight runs, 0 candidate recipe promotions.

## Inner-loop findings retained in Recipe v2

| Mechanism | Result | Recipe v2 decision |
|---|---|---|
| Compact verify-all | ABA bracket: +0.901% primary, +0.217% prefill, inside ±3% | Use static; simpler and equivalent. |
| Inherited SPS table | Iteration 2: −3.561% primary, −11.796% C8 vs compact verify-all | Remove default SPS. |
| Calibrated SPS table | Iteration 10: −4.327% vs older compact; same-hour table-off control only +0.464% | Not promoted. |
| Additive SPS table | Iteration 19: −7.145% primary, −21.170% C8 vs Iteration 16 | Not promoted. |
| One NextN layer | ABA estimate: −4.093% primary, −1.538% prefill | Keep checkpoint default of three layers; no override. |
| γ3 block size | September 2-3 measurement was worse/lower-acceptance than γ5 default path | Keep default γ5; no block-size override. |
| Chunked prefill 4096 | September 2-3 measurement roughly doubled TTFT and cut C8/C16 about 20% | Keep chunked prefill 8192. |

## Historical September 2-3 aggregate output tok/s

These rows predate the September 3-4 inner-loop harness. Row 8 was the old **LOCKED** launch; it is now historical and superseded by Recipe v2.

| # | Engine | Mode | ctx | mem | extra | C1 | C4 | C8 | C16 | C32 |
|---|---|---|---|---|---|---:|---:|---:|---:|---:|
| 0 | SGLang v0.5.16 | 0731 text + DSpark (incumbent) | 1M | 0.85 | catid recipe | 457 | 906 | 1322 | 1771 | – |
| 1 | SGLang preview `7ac467a5` | AR | 32K | 0.85 | – | 163 | 500 | 776 | 1179 | – |
| 2 | SGLang preview | DSpark γ5 | 32K | 0.85 | – | 449 | 831 | 1122 | 1681 | – |
| 3 | SGLang preview | DSpark γ5 | 1M | 0.85 | swa 0.1 + sps | 410 | 939 | 1197 | 1740 | – |
| 4 | vLLM `deepseekv4-flash-vision` | AR | 32K | 0.85 | fp8 KV | 154 | 477 | 767 | 1163 | – |
| 5 | vLLM | DSpark k3 | 32K | 0.85 | adaptive verify | 289 | 709 | 1076 | 1577 | – |
| 6 | SGLang preview | DSpark γ3 | 1M | 0.90 | swa+sps+cp4096 | 366 | 772 | 1028 | 1353 | – |
| 7 | SGLang preview | DSpark γ5 | 1M | 0.90 | swa+sps+cp4096 | 363 | 782 | 1015 | 1447 | 1812 |
| 8 | SGLang preview | DSpark γ5 | 1M | 0.90 | swa 0.1 + sps | 337–437* | 762 | 1302 | 1795 | 2420 |

*C1 3-rep runs bounce 337–449 across all DSpark configs (short 8 s windows; per-stream 375–495). Treat C1 ≈ 400±50 for every γ5 config; the differentiator is C8+.*

Historical row 8 C64 repetition audit (~12K prompts, 1,164 mean out, EOS respected): **0/128 flagged**, worst 8-gram frac 0.003, 1,865 out tok/s. Catid 0731 reference: 11.9% at C64.

Raw September 2-3 files on Station were copied into `results/bench-*.txt`, `smoke-*.txt`, `needle-*.txt`, and `prefill-cold-dspark-1m.txt`.

## Historical correctness

- Smoke (`dsfv_smoke.py`, 10 checks: models, tool_calls parse, reasoning split, OCR exact, shapes/colors, chart read, 2-image compare, thinking off/on, long gen): **10/10** on SGLang AR, SGLang DSpark, vLLM AR.
- Needle ladder (SGLang DSpark 1M): exact recall at 26K / 104K / 208K / 415K prompt tokens; 1M rung — see `needle-dspark-1m.txt`. Prefill ~25K tok/s sustained; 415K TTFT 16 s.
- `bias_vl` image-token routing confirmed active in SGLang log (real vision path).
- vLLM DSpark warns: drafter gets text-only inputs (no MM embeddings passed) — fine for text decode, unverified for image-heavy turns.

## Historical cold prefill (old row 8)

| prompt tok | TTFT s | tok/s |
|---:|---:|---:|
| 6,532 | 0.21 | 31,700 |
| 25,978 | 0.82 | 31,800 |
| 51,926 | 1.49 | 34,800 |
| 103,841 | 3.0 | 34,100 |
| 207,296 | 6.7 | 30,800 |
| 415K (needle) | 16 | ~25,000 |
| 810K (needle) | 45 | ~18,000 |

## Memory (historical SGLang, mem 0.85/0.90, 1M ctx, DSpark)

Weights 148 GB + draft 9.9 GB; KV pool 6.06M tokens; 36 GB spare HBM; zero coherent spill. At 0.90: available GPU memory about 23 GB, pool 7.5M.

## Not pursued / parked

- EXL3: only Vision EXL3 pack (vcruz305 MixedK) drops vision tower + MTP, needs vllm-exl3 fork, ~16 tok/s on Spark. No reason on a box where native FP4 fits with 36 GB spare.
- NVFP4 (s-zaizen): W4A4 experts, +6% on Spark, DSpark accept collapses to ~2%. Would lose the 2.75× DSpark win to gain single digits. Not worth it here.
- TensorRT-LLM: no DSpark, NVFP4 loading bug open. Skip.
- Reederey GLM-5.3-Flash EXL3 kernel post: 2×Spark specific, not this model.

## Tool-call regression in the preview image — found and patched (2026-09-03)

**Symptom.** Real agent sessions (Hermes, terminal+file tools) degraded after a few tool turns: the model started emitting `{"arguments": {"command": ...}}` instead of `{"command": ...}`, one level deeper each turn. Short gates passed, which is why it slipped through.

**Root cause (source-verified inside the container).** `lmsysorg/sglang:dev-dsv4-flash-vision@7ac467a5` carries two halves of one upstream fix out of sync:

| File | State in image |
|---|---|
| `srt/entrypoints/openai/serving_chat.py` | has [#28035](https://github.com/sgl-project/sglang/pull/28035): `normalize_assistant_tool_call_arguments()` converts every history tool call's `arguments` **str → dict** before encoding |
| `srt/entrypoints/openai/encoding_dsv4.py` | **pre-#28035**: `json.loads(dict)` → TypeError → silent fallback `arguments = {"arguments": <dict>}` |

So every prior tool call is rendered into the DSML prompt as one parameter literally named `arguments`:

```text
expected:  <｜DSML｜parameter name="command" string="true">uname -a</｜DSML｜parameter>
actual:    <｜DSML｜parameter name="arguments" string="false">{"command": "uname -a"}</｜DSML｜parameter>
```

**Fix.** `patches/encoding_dsv4.py` = container file with `encode_arguments_to_dsml` replaced by upstream main's version (accepts str or dict; raises on non-object). Generated by `patches/make_patch.py`; diff in `patches/encoding_dsv4.diff`. Bind-mounted read-only over the module in `launch-dsfv.sh`. No rebuild, no perf change (prompt-encoding path only).

**Gate after patch.** Headless Hermes, terminal+file tools, 8 sequential tool hops (6× `read_file` on separate nonce files → `write_file` → `terminal wc -c`) → exact joined string, OUT file byte-identical to expected, 0 arg errors, 0 nested-`arguments` calls, 20.6 s wall. Session `20260902_203032_80baf4`.

**Lesson for the gate.** A tools smoke that passes with 1-2 hops is not evidence for agent use. Require at least 4 replayed tool turns in one conversation before calling any DSML-family endpoint Hermes-ready.

## Attribution

- **catid** — [dgx_station_benchmarks](https://github.com/catid/dgx_station_benchmarks): the single-GB300 0731 recipe, workload contract (8K/1K/temp 0/warm), the inherited SPS table, and the repetition-audit rule. This work is a direct continuation of that baseline.
- **SGLang team** — [PR #37253](https://github.com/sgl-project/sglang/pull/37253) (Vision-Exp support, preview image) and [PR #37492](https://github.com/sgl-project/sglang/pull/37492) (4×GB300 verification); the [DeepSeek-V4 cookbook](https://docs.sglang.io/cookbook/autoregressive/DeepSeek/DeepSeek-V4).
- **vLLM team** — [PR #54566](https://github.com/vllm-project/vllm/pull/54566) and the `deepseekv4-flash-vision` preview image.
- **DeepSeek** — the checkpoint, DSpark head, and the sane decision to ship native FP4 so nobody has to requantize.
- Community NVFP4/EXL3 packs that informed the "don't" decisions: [s-zaizen](https://huggingface.co/s-zaizen/DeepSeek-V4-Flash-Vision-Exp-NVFP4), [vcruz305](https://github.com/vcruz305/DeepSeek-V4-Flash-Vision-EXL3-MixedK-DGX-Spark-recipe), [tonyd2wild](https://github.com/tonyd2wild/DeepSeek-v4-Flash-Vision-Exp-DSpark-1M-NVFP4-KV-2x-DGX-Spark), [MiaAI-Lab](https://github.com/MiaAI-Lab/DeepSeek-v4-Flash-One-DGX-Spark).

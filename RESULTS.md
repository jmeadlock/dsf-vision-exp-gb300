# DS4F Vision-Exp on one GB300 — results ledger (2026-09-02/03)

> **September 3–4 inner-loop follow-up:** [`INNER_LOOP_RETROSPECTIVE.md`](INNER_LOOP_RETROSPECTIVE.md) documents the 20-number campaign, its fail-closed harness changes, the neutral compact-mode ABA result, rejected SPS artifacts, rejected one-layer NextN override, and the final decision to promote no new recipe. The generated accounting is [`research/inner-loop-campaign-audit.md`](research/inner-loop-campaign-audit.md).
Box: DGX Station GB300, 269 GB HBM, driver 595, CUDA 13.2. Model `deepseek-ai/DeepSeek-V4-Flash-Vision-Exp` @ `6821d6ad` (48 shards, 167.83 GB, native FP4 experts + FP8 dense + BF16 vision tower + DSpark head). All TP1.
Workload (bench_dsf.py, same contract as catid 0731): ~7K prompt tokens, 1,024 out, temp 0, 3 reps/C, warm.
## Aggregate output tok/s
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
| **8** | **SGLang preview** | **DSpark γ5** | **1M** | **0.90** | **swa 0.1 + sps** | 337–437* | 762 | **1302** | **1795** | **2420** |

*C1 3-rep runs bounce 337–449 across all DSpark configs (short 8 s windows; per-stream 375–495). Treat C1 ≈ 400±50 for every γ5 config; the differentiator is C8+.

**LOCKED = row 8.** `launch-dsfv.sh dspark` now defaults to it.

C64 repetition audit (row 8, ~12K prompts, 1,164 mean out, EOS respected): **0/128 flagged**, worst 8-gram frac 0.003, 1,865 out tok/s. (catid 0731 reference: 11.9% at C64.)

Raw files on Station: `~/ds4f-vision-exp/results/bench-*.txt`, `smoke-*.txt`, `needle-*.txt`.
## Correctness
- Smoke (`dsfv_smoke.py`, 10 checks: models, tool_calls parse, reasoning split, OCR exact, shapes/colors, chart read, 2-image compare, thinking off/on, long gen): **10/10** on SGLang AR, SGLang DSpark, vLLM AR.
- Needle ladder (SGLang DSpark 1M): exact recall at 26K / 104K / 208K / 415K prompt tokens; 1M rung — see needle-dspark-1m.txt. Prefill ~25K tok/s sustained; 415K TTFT 16 s.
- `bias_vl` image-token routing confirmed active in SGLang log (real vision path).
- vLLM DSpark warns: drafter gets text-only inputs (no MM embeddings passed) — fine for text decode, unverified for image-heavy turns.
## Cold prefill (row 8, nonce-at-start, max_tokens=1, 3 samples)
| prompt tok | TTFT s | tok/s |
|---:|---:|---:|
| 6,532 | 0.21 | 31,700 |
| 25,978 | 0.82 | 31,800 |
| 51,926 | 1.49 | 34,800 |
| 103,841 | 3.0 | 34,100 |
| 207,296 | 6.7 | 30,800 |
| 415K (needle) | 16 | ~25,000 |
| 810K (needle) | 45 | ~18,000 |

## Memory (SGLang, mem 0.85, 1M ctx, DSpark)
weights 148 GB + draft 9.9 GB; KV pool 6.06M tokens; 36 GB spare HBM; zero coherent spill. At 0.90: available_gpu_mem 23 GB, pool 7.5M.
## Findings
- DSpark: 2.75× C1, 1.4× C16 on SGLang. Keep.
- γ3 < γ5 on this box (accept len 3.12/3 vs 3.7–3.8/5). Checkpoint default wins.
- `--chunked-prefill-size 4096` hurts single-GPU: TTFT 0.19→0.36 s and C8/C16 −20%. Keep default 8192.
- mem 0.90 vs 0.85: +9% C8, +3% C16, enables C32 at 2,420. 22.7 GB still spare.
- SGLang ≈ vLLM on AR (within 5%). SGLang DSpark > vLLM DSpark by ~55% at C1 (449 vs 289; vLLM k=3 accept 2.45).
- Vision-Exp costs 2–15% vs text-only 0731 under DSpark. Native vision ~free.
- vLLM KV accounting: 154K tokens @ 59 GB (400 B/tok) vs SGLang 8.75M-token pool — vLLM allocates full-width; 1M ctx on vLLM would need util > 0.95 or smaller max-num-seqs.
## Not pursued / parked
- EXL3: only Vision EXL3 pack (vcruz305 MixedK) drops vision tower + MTP, needs vllm-exl3 fork, ~16 tok/s on Spark. No reason on a box where native FP4 fits with 36 GB spare.
- NVFP4 (s-zaizen): W4A4 experts, +6% on Spark, DSpark accept collapses to ~2%. Would lose the 2.75× DSpark win to gain single digits. Not worth it here.
- TensorRT-LLM: no DSpark, NVFP4 loading bug open. Skip.
- Reederey GLM-5.3-Flash EXL3 kernel post: 2×Spark specific, not this model.

## Tool-call regression in the preview image — FOUND + PATCHED (2026-09-03)

**Symptom.** Real agent sessions (Hermes, terminal+file tools) degraded after a few tool turns: the model started emitting `{"arguments": {"command": ...}}` instead of `{"command": ...}`, one level deeper each turn (10 deep by the end of a 70-error session). Short gates passed (the 15/16 tool-grounded loop in the post was 1–2 hops each), which is why it slipped through.

**Root cause (source-verified inside the container).** `lmsysorg/sglang:dev-dsv4-flash-vision@7ac467a5` carries two halves of one upstream fix out of sync:

| File | State in image |
|---|---|
| `srt/entrypoints/openai/serving_chat.py` | has [#28035](https://github.com/sgl-project/sglang/pull/28035): `normalize_assistant_tool_call_arguments()` converts every history tool call's `arguments` **str → dict** before encoding |
| `srt/entrypoints/openai/encoding_dsv4.py` | **pre-#28035** (identical to the model repo's `encoding/encoding_dsv4.py`): `json.loads(dict)` → TypeError → silent fallback `arguments = {"arguments": <dict>}` |

So every prior tool call is rendered into the DSML prompt as one parameter literally named `arguments`:

```
expected:  <｜DSML｜parameter name="command" string="true">uname -a</｜DSML｜parameter>
actual:    <｜DSML｜parameter name="arguments" string="false">{"command": "uname -a"}</｜DSML｜parameter>
```

The model imitates its own corrupted history. Not a model defect; the 0731 text image (SGLang v0.5.16) and the Anemll/vLLM Spark lane don't have it.

**Repro** (`patches/` — 3-turn replay, same request shape Hermes sends):

| Turn | replayed tool turns | before patch | after patch |
|---|---|---|---|
| 1 | 0 | `{"command": "uname -a"}` | same |
| 2 | 1 | `{"path": ..., "content": ...}` | same |
| 3 | 2 | **`{"arguments": {"command": "df -h /"}}`** | `{"command": "df -h /"}` |

**Fix.** `patches/encoding_dsv4.py` = container file with `encode_arguments_to_dsml` replaced by upstream main's version (accepts str or dict; raises on non-object). Generated by `patches/make_patch.py`; diff in `patches/encoding_dsv4.diff`. Bind-mounted read-only over the module in `launch-dsfv.sh`. No rebuild, no perf change (prompt-encoding path only).

**Gate after patch.** Headless Hermes, terminal+file tools, 8 sequential tool hops (6× `read_file` on separate nonce files → `write_file` → `terminal wc -c`) → exact joined string, OUT file byte-identical to expected, 0 arg errors, 0 nested-`arguments` calls, 20.6 s wall. Session `20260902_203032_80baf4`.

**Lesson for the gate.** A tools smoke that passes with 1–2 hops is not evidence for agent use. Require ≥4 *replayed* tool turns in one conversation before calling any DSML-family endpoint Hermes-ready.

1|# DS4F Vision-Exp on one GB300 — results ledger (2026-09-02/03)
2|
3|Box: DGX Station GB300, 269 GB HBM, driver 595, CUDA 13.2. Model `deepseek-ai/DeepSeek-V4-Flash-Vision-Exp` @ `6821d6ad` (48 shards, 167.83 GB, native FP4 experts + FP8 dense + BF16 vision tower + DSpark head). All TP1.
4|Workload (bench_dsf.py, same contract as catid 0731): ~7K prompt tokens, 1,024 out, temp 0, 3 reps/C, warm.
5|
6|## Aggregate output tok/s
7|
8|| # | Engine | Mode | ctx | mem | extra | C1 | C4 | C8 | C16 | C32 |
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
20|
21|## Correctness
22|- Smoke (`dsfv_smoke.py`, 10 checks: models, tool_calls parse, reasoning split, OCR exact, shapes/colors, chart read, 2-image compare, thinking off/on, long gen): **10/10** on SGLang AR, SGLang DSpark, vLLM AR.
23|- Needle ladder (SGLang DSpark 1M): exact recall at 26K / 104K / 208K / 415K prompt tokens; 1M rung — see needle-dspark-1m.txt. Prefill ~25K tok/s sustained; 415K TTFT 16 s.
24|- `bias_vl` image-token routing confirmed active in SGLang log (real vision path).
25|- vLLM DSpark warns: drafter gets text-only inputs (no MM embeddings passed) — fine for text decode, unverified for image-heavy turns.
26|
27|## Memory (SGLang, mem 0.85, 1M ctx, DSpark)
28|weights 148 GB + draft 9.9 GB; KV pool 6.06M tokens; 36 GB spare HBM; zero coherent spill. At 0.90: available_gpu_mem 23 GB, pool 7.5M.
29|
30|## Findings
31|- DSpark: 2.75× C1, 1.4× C16 on SGLang. Keep.
32|- γ3 < γ5 on this box (accept len 3.12/3 vs 3.7–3.8/5). Checkpoint default wins.
- `--chunked-prefill-size 4096` hurts single-GPU: TTFT 0.19→0.36 s and C8/C16 −20%. Keep default 8192.
- mem 0.90 vs 0.85: +9% C8, +3% C16, enables C32 at 2,420. 22.7 GB still spare.
33|- SGLang ≈ vLLM on AR (within 5%). SGLang DSpark > vLLM DSpark by ~55% at C1 (449 vs 289; vLLM k=3 accept 2.45).
34|- Vision-Exp costs 2–15% vs text-only 0731 under DSpark. Native vision ~free.
35|- vLLM KV accounting: 154K tokens @ 59 GB (400 B/tok) vs SGLang 8.75M-token pool — vLLM allocates full-width; 1M ctx on vLLM would need util > 0.95 or smaller max-num-seqs.
36|
37|## Not pursued / parked
38|- EXL3: only Vision EXL3 pack (vcruz305 MixedK) drops vision tower + MTP, needs vllm-exl3 fork, ~16 tok/s on Spark. No reason on a box where native FP4 fits with 36 GB spare.
39|- NVFP4 (s-zaizen): W4A4 experts, +6% on Spark, DSpark accept collapses to ~2%. Would lose the 2.75× DSpark win to gain single digits. Not worth it here.
40|- TensorRT-LLM: no DSpark, NVFP4 loading bug open. Skip.
41|- Reederey GLM-5.3-Flash EXL3 kernel post: 2×Spark specific, not this model.
42|
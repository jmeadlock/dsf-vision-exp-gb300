# DeepSeek-V4-Flash-Vision-Exp on one NVIDIA GB300 DGX Station

Recipe, launch scripts, correctness harness, and measured results for serving
[`deepseek-ai/DeepSeek-V4-Flash-Vision-Exp`](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-Vision-Exp)
(305B total / 13B active, native FP4 experts + FP8 dense + BF16 vision tower + bundled DSpark draft head)
on a **single GB300, TP=1, 1M context**, with SGLang.

Write-up: <https://al-engr.com/dsf-vision-exp-single-gb300.html>

Inner-loop retrospective: <https://al-engr.com/testing-the-inner-loop-on-dsfve.html>

## Current recipe: v2

[`RECIPE_V2.md`](RECIPE_V2.md) is the current launch recipe. It is the exact Iteration 13 static/no-SPS configuration, with the Iteration 16 default-NextN reversal used to keep the checkpoint default of three NextN layers.

Recipe v2 is **not a faster-throughput claim**. Iteration 13 measured equivalent C8-C64 throughput inside the frozen ±3% materiality band: **2000.701 tok/s**, **+1.859%** versus Iteration 0. The improvement is operational: the default launch removes an ineffective SPS table dependency, explicitly pins static ragged verification, keeps checkpoint NextN defaults, pins the image digest, and keeps the DSML encoding patch required by this SGLang image.

## Recipe v2 measured throughput

Workload: ~7K prompt tokens, 1,024 output tokens, temperature 0, warm runs. Primary metric: C8/C16/C32/C64 geometric mean.

| Concurrency | Aggregate output tok/s |
|---:|---:|
| C1 | 329.2 |
| C4 | 748.7 |
| C8 | 1260.7 |
| C16 | 1786.0 |
| C32 | 2383.6 |
| C64 | 2985.4 |

Cold-prefill means at the 8K/32K/64K/128K/256K targets were **33,935 / 32,561 / 35,669 / 35,662 / 31,650 tok/s**.

## Files

| File | Purpose |
|---|---|
| `launch-dsfv.sh` | Current SGLang launch (`ar` or `dspark`). Defaults = Recipe v2: digest-pinned static/no-SPS/checkpoint-NextN DSpark. |
| `recipe-v2.json` | Machine-readable Recipe v2 contract and measurements. |
| `RECIPE_V2.md` | Human-readable Recipe v2 launch and evidence boundary. |
| `research/recipe-v2-evidence.json` | Public-safe extraction generated from local ignored receipts by `research/build_recipe_v2.py`. |
| `research/do-not-retry.md` | Scoped exclusions for compact-as-speedup, SPS tables, one NextN layer, γ3, and chunked prefill 4096. |
| `launch-dsfv-vllm.sh` | vLLM preview equivalent, for A/B. |
| `dsfv_smoke.py` | 10-check protocol + vision smoke (OCR, shapes, chart, multi-image, tool_calls, reasoning split). Exit non-zero on any FAIL. |
| `needle.py` | Long-context ladder 32K → 1M with random-word filler (defeats prefix cache), exact-match recall, post-probe. |
| `prefill.py` | Cold-prefill probe: nonce at prompt start, `max_tokens=1`, `prompt_tokens / request time` at 8K–256K. |
| `repaudit.py` | C64 natural-decode repetition audit, catid's rule (4 consecutive sentence repeats or repeated-8gram ≥ 0.20). |
| `dspark_sps_tp1.json` | Historical inherited SPS table from catid/dgx_station_benchmarks. Retained for provenance; unused by Recipe v2. |
| `RESULTS.md` | Full ledger: historical September 2-3 rows, inner-loop closeout, losing configurations, and v2 decision. |
| `patches/encoding_dsv4.py` | **Required with image `7ac467a5`.** Fixes a history tool-call encoding bug that makes multi-turn agent use degrade (`{"arguments": {...}}` nesting). Bind-mounted by `launch-dsfv.sh`. |

## Quick start

```bash
# Box: DGX Station GB300 (269 GB HBM), driver 595.x, Docker + NVIDIA CDI.
python3 -m venv ~/hfenv && ~/hfenv/bin/pip install 'huggingface_hub[cli]'
~/hfenv/bin/hf download deepseek-ai/DeepSeek-V4-Flash-Vision-Exp \
  --revision 6821d6ad3681a4b137b066b76094fa82ebd0a380 \
  --local-dir ~/models/DeepSeek-V4-Flash-Vision-Exp/6821d6ad3681a4b137b066b76094fa82ebd0a380/original

docker pull lmsysorg/sglang@sha256:7ac467a50508b7029a23e846c150998fdd26d95c1cfd377ea7e74e28374486a6
mkdir -p ~/ds4f-vision-exp && cp -r patches ~/ds4f-vision-exp/   # encoding_dsv4.py bind-mount
printf '%s\n' 'your-key' > ~/.glm_api_key

./launch-dsfv.sh dspark          # 1M ctx, mem 0.90, DSpark γ5/default NextN, static/no-SPS, :30003
docker logs -f dsfv-dspark       # first cold start ~12 min (FlashInfer autotune); ~4 min warm
API_KEY=$(cat ~/.glm_api_key) python3 dsfv_smoke.py
```

Paths in the launcher assume `/home/milo`; edit `MODEL=` and the patch bind-mount path if yours differ. There is no SPS-table copy step for Recipe v2.

## What the launch actually is

```bash
SGLANG_RAGGED_VERIFY_MODE=static \
python3 -m sglang.launch_server --trust-remote-code --model-path /model --tp 1 \
  --mem-fraction-static 0.90 --context-length 1048576 \
  --chunked-prefill-size 8192 \
  --cuda-graph-max-bs-decode 64 --cuda-graph-bs-decode 1 2 4 8 16 32 64 \
  --max-running-requests 64 \
  --enable-metrics --host 0.0.0.0 --port 30003 \
  --served-model-name dsf-vision-exp --api-key YOUR_KEY \
  --tool-call-parser deepseekv4 --reasoning-parser deepseek-v4 \
  --speculative-algorithm DSPARK \
  --swa-full-tokens-ratio 0.1
```

Plus the read-only bind mount from `patches/encoding_dsv4.py` to SGLang's `encoding_dsv4.py`. Without it, multi-turn tool use degrades (see `RESULTS.md`).

Absent by design: `--speculative-dspark-sps-table-path`, any SPS mount, `--json-model-override-args`, `--speculative-dspark-block-size`, and `--chunked-prefill-size 4096`.

## Historical September 2-3 baseline

The original row 8 was the best pre-inner-loop launch, but it is now historical and superseded by Recipe v2 because it relied on a configured SPS table that the later static runtime evidence showed was ineffective.

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

*C1 3-rep runs bounce 337–449 across all DSpark configs. Treat C1 ≈ 400±50 for every γ5 config; the differentiator is C8+.*

## Inner-loop campaign closeout

The September 3-4 inner-loop campaign is documented in [`INNER_LOOP_RETROSPECTIVE.md`](INNER_LOOP_RETROSPECTIVE.md). It closed with 10 admissible performance/baseline/control runs, 2 calibration-only runs, 8 blocked or aborted harness/preflight runs, and no measured speed promotion. The generated public-safe accounting is in [`research/inner-loop-campaign-audit.md`](research/inner-loop-campaign-audit.md); Recipe v2 evidence is in [`research/recipe-v2-evidence.json`](research/recipe-v2-evidence.json).

## Attribution

- **catid** — [dgx_station_benchmarks](https://github.com/catid/dgx_station_benchmarks): the single-GB300 0731 recipe, workload contract (8K/1K/temp 0/warm), the inherited SPS table, and the repetition-audit rule. This work is a direct continuation of that baseline.
- **SGLang team** — [PR #37253](https://github.com/sgl-project/sglang/pull/37253) (Vision-Exp support, preview image) and [PR #37492](https://github.com/sgl-project/sglang/pull/37492) (4×GB300 verification); the [DeepSeek-V4 cookbook](https://docs.sglang.io/cookbook/autoregressive/DeepSeek/DeepSeek-V4).
- **vLLM team** — [PR #54566](https://github.com/vllm-project/vllm/pull/54566) and the `deepseekv4-flash-vision` preview image.
- **DeepSeek** — the checkpoint, DSpark head, and the sane decision to ship native FP4 so nobody has to requantize.
- Community NVFP4/EXL3 packs that informed the "don't" decisions: [s-zaizen](https://huggingface.co/s-zaizen/DeepSeek-V4-Flash-Vision-Exp-NVFP4), [vcruz305](https://github.com/vcruz305/DeepSeek-V4-Flash-Vision-EXL3-MixedK-DGX-Spark-recipe), [tonyd2wild](https://github.com/tonyd2wild/DeepSeek-v4-Flash-Vision-Exp-DSpark-1M-NVFP4-KV-2x-DGX-Spark), [MiaAI-Lab](https://github.com/MiaAI-Lab/DeepSeek-v4-Flash-One-DGX-Spark).

Scripts and harness written by Milo (James Meadlock's AI agent) on September 2-4, 2026. MIT.

# DeepSeek-V4-Flash-Vision-Exp on one NVIDIA GB300 DGX Station

Recipe, launch scripts, correctness harness, and measured results for serving
[`deepseek-ai/DeepSeek-V4-Flash-Vision-Exp`](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-Vision-Exp)
(305B total / 13B active, native FP4 experts + FP8 dense + BF16 vision tower + bundled DSpark draft head)
on a **single GB300, TP=1, 1M context**, with SGLang.

Write-up: <https://al-engr.com/dsf-vision-exp-single-gb300.html>

## TL;DR

| | C1 | C4 | C8 | C16 | C32 |
|---|---:|---:|---:|---:|---:|
| Output tok/s aggregate (7K in / 1K out, temp 0) | ~400 | 762 | 1302 | 1795 | 2420 |

- Native checkpoint, no quantization: 148 GB weights + 10 GB draft, ~23 GB HBM spare at `mem-fraction-static 0.90`.
- DSpark = 2.75× at C1 over autoregressive. Keep the checkpoint default γ=5.
- Cold prefill 31–35K tok/s flat through 200K prompt tokens (52K → 1.49 s TTFT); ~18K tok/s at 810K.
- 10/10 vision + tool-call smoke; exact needle recall to 810K tokens; **0/128 repetition flags at C64**.
- vLLM preview works (10/10 smoke) but its DSpark is ~35% slower at C1. SGLang is the pick.
- EXL3 / NVFP4 / TensorRT-LLM deliberately not used — see the post.
- **2026-09-03:** preview image has a tool-call history encoding bug (two halves of sglang #28035 out of sync) that breaks agent sessions after ~2 tool turns. Patched via a one-file bind-mount, included here; 8-hop Hermes gate passes.

## Files

| File | Purpose |
|---|---|
| `launch-dsfv.sh` | Locked SGLang launch (`ar` or `dspark`). Defaults = winning config. |
| `launch-dsfv-vllm.sh` | vLLM preview equivalent, for A/B. |
| `dsfv_smoke.py` | 10-check protocol + vision smoke (OCR, shapes, chart, multi-image, tool_calls, reasoning split). Exit non-zero on any FAIL. |
| `needle.py` | Long-context ladder 32K → 1M with random-word filler (defeats prefix cache), exact-match recall, post-probe. |
| `prefill.py` | Cold-prefill probe: nonce at prompt start, `max_tokens=1`, `prompt_tokens / request time` at 8K–256K. |
| `repaudit.py` | C64 natural-decode repetition audit, catid's rule (4 consecutive sentence repeats or repeated-8gram ≥ 0.20). |
| `dspark_sps_tp1.json` | Single-GB300 DSpark steps-per-second table. **From [catid/dgx_station_benchmarks](https://github.com/catid/dgx_station_benchmarks/tree/main/deepseek-v4-flash-0731)** — not mine. |
| `RESULTS.md` | Full ledger: every config tried, what lost and why. |
| `patches/encoding_dsv4.py` | **Required with image `7ac467a5`.** Fixes a history tool-call encoding bug that makes multi-turn agent use degrade (`{"arguments": {...}}` nesting). Bind-mounted by `launch-dsfv.sh`. See `RESULTS.md` → *Tool-call regression*. |

## Quick start

```bash
# Box: DGX Station GB300 (269 GB HBM), driver 595.x, Docker + NVIDIA CDI.
python3 -m venv ~/hfenv && ~/hfenv/bin/pip install 'huggingface_hub[cli]'
~/hfenv/bin/hf download deepseek-ai/DeepSeek-V4-Flash-Vision-Exp \
  --revision 6821d6ad3681a4b137b066b76094fa82ebd0a380 \
  --local-dir ~/models/DeepSeek-V4-Flash-Vision-Exp/6821d6ad3681a4b137b066b76094fa82ebd0a380/original

docker pull lmsysorg/sglang@sha256:7ac467a50508b7029a23e846c150998fdd26d95c1cfd377ea7e74e28374486a6
mkdir -p ~/ds4f-vision-exp && cp -r patches ~/ds4f-vision-exp/   # encoding_dsv4.py bind-mount (see RESULTS.md)
echo "your-key" > ~/.glm_api_key
cp dspark_sps_tp1.json ~/dspark_sps_tp1.json

./launch-dsfv.sh dspark          # 1M ctx, mem 0.90, DSpark γ5, :30003
docker logs -f dsfv-dspark       # first cold start ~12 min (FlashInfer autotune); ~4 min warm
API_KEY=$(cat ~/.glm_api_key) python3 dsfv_smoke.py
```

Paths in the scripts assume `/home/milo`; edit `MODEL=` and the SPS mount if yours differ.

## What the launch actually is

```
sglang.launch_server --trust-remote-code --model-path /model --tp 1
  --context-length 1048576 --mem-fraction-static 0.90
  --speculative-algorithm DSPARK
  --swa-full-tokens-ratio 0.1
  --speculative-dspark-sps-table-path /dspark_sps_tp1.json
  --reasoning-parser deepseek-v4 --tool-call-parser deepseekv4
  --served-model-name dsf-vision-exp --api-key ... --port 30003
```

Plus `-v patches/encoding_dsv4.py:/sgl-workspace/sglang/python/sglang/srt/entrypoints/openai/encoding_dsv4.py:ro` — without it, multi-turn tool use degrades (see RESULTS.md).

Things measured **slower** on one GB300 and therefore absent: `--chunked-prefill-size 4096`
(TTFT doubles, C8/C16 −20%) and `--speculative-dspark-block-size 3` (accept 3.1/3 vs 3.7/5).

## Attribution

- **catid** — [dgx_station_benchmarks](https://github.com/catid/dgx_station_benchmarks): the single-GB300 0731 recipe, workload contract (8K/1K/temp 0/warm), the SPS table, and the repetition-audit rule. This work is a direct continuation of that baseline.
- **SGLang team** — [PR #37253](https://github.com/sgl-project/sglang/pull/37253) (Vision-Exp support, preview image) and [PR #37492](https://github.com/sgl-project/sglang/pull/37492) (4×GB300 verification); the [DeepSeek-V4 cookbook](https://docs.sglang.io/cookbook/autoregressive/DeepSeek/DeepSeek-V4).
- **vLLM team** — [PR #54566](https://github.com/vllm-project/vllm/pull/54566) and the `deepseekv4-flash-vision` preview image.
- **DeepSeek** — the checkpoint, DSpark head, and the sane decision to ship native FP4 so nobody has to requantize.
- Community NVFP4/EXL3 packs that informed the "don't" decisions: [s-zaizen](https://huggingface.co/s-zaizen/DeepSeek-V4-Flash-Vision-Exp-NVFP4), [vcruz305](https://github.com/vcruz305/DeepSeek-V4-Flash-Vision-EXL3-MixedK-DGX-Spark-recipe), [tonyd2wild](https://github.com/tonyd2wild/DeepSeek-v4-Flash-Vision-Exp-DSpark-1M-NVFP4-KV-2x-DGX-Spark), [MiaAI-Lab](https://github.com/MiaAI-Lab/DeepSeek-v4-Flash-One-DGX-Spark).

Scripts and harness written by Milo (James Meadlock's AI agent, claude-fable-5-1) on September 2–3, 2026. MIT.

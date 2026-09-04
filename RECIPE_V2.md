# DSFVE single-GB300 Recipe v2

Recipe v2 is the evidence-hardened default for serving `deepseek-ai/DeepSeek-V4-Flash-Vision-Exp` on one NVIDIA GB300 with SGLang and DSpark.

It is **not** a measured speed promotion. Iteration 13 measured equivalent C8-C64 throughput inside the frozen ±3% band. The improvement is operational: fewer ineffective runtime dependencies, explicit static verification, checkpoint-default NextN retained, immutable image/model pins, and the DSML encoding patch kept in the launch path.

## Default launch

```bash
docker pull lmsysorg/sglang@sha256:7ac467a50508b7029a23e846c150998fdd26d95c1cfd377ea7e74e28374486a6
python3 -m venv ~/hfenv && ~/hfenv/bin/pip install 'huggingface_hub[cli]'
~/hfenv/bin/hf download deepseek-ai/DeepSeek-V4-Flash-Vision-Exp \
  --revision 6821d6ad3681a4b137b066b76094fa82ebd0a380 \
  --local-dir ~/models/DeepSeek-V4-Flash-Vision-Exp/6821d6ad3681a4b137b066b76094fa82ebd0a380/original
mkdir -p ~/ds4f-vision-exp && cp -r patches ~/ds4f-vision-exp/
echo "your-key" > ~/.glm_api_key

./launch-dsfv.sh dspark
```

The launcher assumes the host paths used above. Edit `MODEL=` and the patch bind-mount path if your model or checkout lives elsewhere.

## Runtime contract

| Surface | Recipe v2 value |
|---|---|
| Model | `deepseek-ai/DeepSeek-V4-Flash-Vision-Exp` |
| Model revision | `6821d6ad3681a4b137b066b76094fa82ebd0a380` |
| SGLang image | `lmsysorg/sglang@sha256:7ac467a50508b7029a23e846c150998fdd26d95c1cfd377ea7e74e28374486a6` |
| Hardware | one NVIDIA GB300, TP=1 |
| Context | `1048576` |
| Memory fraction | `0.90` |
| Speculation | DSpark, checkpoint/runtime default block size 5 |
| NextN | checkpoint default, three layers; no `--json-model-override-args` |
| Ragged verification | `SGLANG_RAGGED_VERIFY_MODE=static` |
| SPS table | none; no table argument or bind mount |
| SWA full-token ratio | `0.1` |
| Chunked prefill | `8192` |
| CUDA graph decode batches | `1 2 4 8 16 32 64`, max `64` |
| Max running requests | `64` |
| Parsers | `--tool-call-parser deepseekv4 --reasoning-parser deepseek-v4` |
| API auth | required at runtime; no secret is committed |
| DSML encoding patch | required bind mount from `patches/encoding_dsv4.py` |

In command form, the server side is:

```bash
SGLANG_RAGGED_VERIFY_MODE=static \
python3 -m sglang.launch_server --trust-remote-code --model-path /model --tp 1 \
  --mem-fraction-static 0.90 --context-length 1048576 \
  --chunked-prefill-size 8192 \
  --cuda-graph-max-bs-decode 64 --cuda-graph-bs-decode 1 2 4 8 16 32 64 \
  --max-running-requests 64 \
  --enable-metrics --host 0.0.0.0 --port 30003 \
  --served-model-name dsf-vision-exp --api-key '<runtime secret>' \
  --tool-call-parser deepseekv4 --reasoning-parser deepseek-v4 \
  --speculative-algorithm DSPARK \
  --swa-full-tokens-ratio 0.1
```

Recipe v2 deliberately omits `--speculative-dspark-sps-table-path`, any SPS table mount, `--json-model-override-args`, `--speculative-dspark-block-size`, and `--chunked-prefill-size 4096`.

## Iteration 13 measured throughput

Workload: approximately 7K input tokens, 1,024 output tokens, temperature 0, warm runs. The primary decision metric is the C8/C16/C32/C64 geometric mean.

| Concurrency | Aggregate output tok/s |
|---:|---:|
| C1 | 329.2 |
| C4 | 748.7 |
| C8 | 1260.7 |
| C16 | 1786.0 |
| C32 | 2383.6 |
| C64 | 2985.4 |

C8-C64 geometric mean: **2000.701 tok/s**, **+1.859%** versus Iteration 0. That is inside the ±3% frozen materiality band, so the claim is equivalent performance, not faster performance.

## Iteration 13 cold prefill

| Target tokens | Prompt tokens | Mean tok/s |
|---:|---:|---:|
| 8K | 6,479 | 33,935 |
| 32K | 25,905 | 32,561 |
| 64K | 52,011 | 35,669 |
| 128K | 103,669 | 35,662 |
| 256K | 207,405 | 31,650 |

## Evidence and exclusions

- Machine-readable recipe: [`recipe-v2.json`](recipe-v2.json)
- Public-safe evidence extracted from local receipts: [`research/recipe-v2-evidence.json`](research/recipe-v2-evidence.json)
- Campaign audit: [`research/inner-loop-campaign-audit.json`](research/inner-loop-campaign-audit.json)
- Do-not-retry ledger: [`research/do-not-retry.md`](research/do-not-retry.md)

The relevant local receipt set is Iteration 13 (`013-static-no-sps-paired-control-20260904T103545Z`) plus the Iteration 16 default-NextN reversal. The receipts proved static ragged verification, no SPS table, no NextN override, all correctness gates, all throughput/prefill rows, clean repetition audit, receipt sanitization, and clean stop.

To rebuild and check the public-safe JSON artifacts from local receipts:

```bash
python3 research/build_recipe_v2.py --check
```

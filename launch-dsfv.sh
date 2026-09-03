#!/bin/bash
# launch-dsfv.sh — DeepSeek-V4-Flash-Vision-Exp on one GB300, TP1, SGLang preview image.
# Usage: launch-dsfv.sh {ar|dspark} [ctx_tokens] [mem_fraction]
set -euo pipefail
mode="${1:?ar|dspark}"; CTX="${2:-1048576}"; MEM="${3:-0.90}"
# LOCKED 2026-09-03: dspark 1M 0.90 + swa 0.1 + sps table. Do NOT add --chunked-prefill-size 4096 or --speculative-dspark-block-size 3 (both measured slower on one GB300).
EXTRA_ARGS="${EXTRA_ARGS:---swa-full-tokens-ratio 0.1 --speculative-dspark-sps-table-path /dspark_sps_tp1.json}"
SHA=6821d6ad3681a4b137b066b76094fa82ebd0a380
MODEL=/home/milo/models/DeepSeek-V4-Flash-Vision-Exp/$SHA/original
IMAGE="${IMAGE:-lmsysorg/sglang:dev-dsv4-flash-vision}"
NAME="dsfv-${mode}"
CACHE=/home/milo/dsfv-cache; mkdir -p $CACHE/{tilelang,triton,nv,root-cache}
case "$mode" in
  ar) MODEARGS="";;
  dspark) MODEARGS="--speculative-algorithm DSPARK";;
  *) echo bad mode; exit 2;;
esac
docker run -d --name "$NAME" --gpus all --ipc host --network host \
  --ulimit memlock=-1 --ulimit stack=67108864 --cap-add IPC_LOCK --cap-add SYS_NICE \
  -v "$MODEL":/model:ro \
  -v /home/milo/dspark_sps_tp1.json:/dspark_sps_tp1.json:ro \
  -v /home/milo/ds4f-vision-exp/patches/encoding_dsv4.py:/sgl-workspace/sglang/python/sglang/srt/entrypoints/openai/encoding_dsv4.py:ro \
  -v $CACHE/root-cache:/root/.cache -v $CACHE/tilelang:/root/.tilelang -v $CACHE/triton:/root/.triton -v $CACHE/nv:/root/.nv \
  "$IMAGE" \
  python3 -m sglang.launch_server --trust-remote-code --model-path /model --tp 1 \
    --mem-fraction-static "$MEM" --context-length "$CTX" \
    --chunked-prefill-size 8192 \
    --cuda-graph-max-bs-decode 64 --cuda-graph-bs-decode 1 2 4 8 16 32 64 --max-running-requests 64 \
    --enable-metrics --host 0.0.0.0 --port 30003 \
    --served-model-name dsf-vision-exp --api-key "$(cat /home/milo/.glm_api_key)" \
    --tool-call-parser deepseekv4 --reasoning-parser deepseek-v4 \
    $MODEARGS ${EXTRA_ARGS:-}
echo "launched $NAME ($mode ctx=$CTX mem=$MEM) on :30003"

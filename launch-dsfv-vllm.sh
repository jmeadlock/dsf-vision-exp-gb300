#!/bin/bash
# launch-dsfv-vllm.sh — Vision-Exp on vLLM preview image, TP1. Usage: launch-dsfv-vllm.sh {ar|dspark} [ctx] [util]
set -euo pipefail
mode="${1:?ar|dspark}"; CTX="${2:-32768}"; UTIL="${3:-0.85}"
SHA=6821d6ad3681a4b137b066b76094fa82ebd0a380
MODEL=/home/milo/models/DeepSeek-V4-Flash-Vision-Exp/$SHA/original
IMAGE="${IMAGE:-vllm/vllm-openai:deepseekv4-flash-vision}"
NAME="dsfv-vllm-${mode}"
CACHE=/home/milo/vllm-cache; mkdir -p $CACHE/{root-cache,triton,nv,vllm}
case "$mode" in
  ar) SPEC="";;
  dspark) SPEC="--speculative-config {\"method\":\"dspark\",\"model\":\"/model\",\"num_speculative_tokens\":3,\"draft_sample_method\":\"probabilistic\",\"enable_adaptive_verification\":true}";;
  *) echo bad mode; exit 2;;
esac
docker run -d --name "$NAME" --gpus all --ipc host --network host \
  --ulimit memlock=-1 --ulimit stack=67108864 \
  -e VLLM_FLASHINFER_AUTOTUNE_SKIP_OPS="trtllm_fp4_block_scale_moe,flashinfer::trtllm_fp4_block_scale_moe" \
  -v "$MODEL":/model:ro \
  -v $CACHE/root-cache:/root/.cache -v $CACHE/triton:/root/.triton -v $CACHE/nv:/root/.nv \
  "$IMAGE" \
  /model --served-model-name dsf-vision-exp --trust-remote-code \
    --tensor-parallel-size 1 --kv-cache-dtype fp8 --block-size 256 \
    --gpu-memory-utilization "$UTIL" --max-model-len "$CTX" --max-num-seqs 64 \
    --tokenizer-mode deepseek_v4 --reasoning-parser deepseek_v4 \
    --tool-call-parser deepseek_v4 --enable-auto-tool-choice \
    --api-key "$(cat /home/milo/.glm_api_key)" --port 30004 \
    $SPEC ${EXTRA_ARGS:-}
echo "launched $NAME ($mode ctx=$CTX util=$UTIL) on :30004"

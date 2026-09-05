#!/usr/bin/env bash
# Launch a uniquely named DS4FVis experiment without changing the production launcher.
set -Eeuo pipefail

NAME="${1:?container name}"
RAGGED_MODE="${2:?static|compact}"
SPS_MODE="${3:?none|current|calibrated|fine-grained}"
PROFILE_MODE="${4:-none}"
NEXTN_LAYERS="${5:-checkpoint}"
CTX="${CTX:-1048576}"
MEM="${MEM:-0.90}"
SHA=6821d6ad3681a4b137b066b76094fa82ebd0a380
MODEL="/home/milo/models/DeepSeek-V4-Flash-Vision-Exp/$SHA/original"
IMAGE="${IMAGE:-lmsysorg/sglang:dev-dsv4-flash-vision}"
CACHE=/home/milo/dsfv-cache
mkdir -p "$CACHE"/{tilelang,triton,nv,root-cache}
HOST=0.0.0.0
AUTH_ARGS=()

case "$RAGGED_MODE" in
  static|compact) ;;
  *) printf 'invalid ragged mode: %s\n' "$RAGGED_MODE" >&2; exit 2 ;;
esac

SPS_MOUNTS=()
PROFILER_MOUNTS=()
ENV_ARGS=(-e "SGLANG_RAGGED_VERIFY_MODE=$RAGGED_MODE")
EXTRA_ARGS=(--swa-full-tokens-ratio 0.1)
MODEL_OVERRIDE_ARGS=()
case "$SPS_MODE" in
  none) ;;
  current|calibrated|fine-grained)
    if [[ "$SPS_MODE" == "fine-grained" && "$RAGGED_MODE" != "compact" ]]; then
      printf 'fine-grained SPS requires compact ragged mode; static ragged leaves SPS tables inactive\n' >&2
      exit 2
    fi
    if [[ -z "${SPS_TABLE_HOST_PATH:-}" || ! -f "$SPS_TABLE_HOST_PATH" ]]; then
      printf 'missing SPS table host path for mode %s\n' "$SPS_MODE" >&2
      exit 2
    fi
    SPS_MOUNTS=(-v "$SPS_TABLE_HOST_PATH":/dspark_sps_table.json:ro)
    EXTRA_ARGS+=(--speculative-dspark-sps-table-path /dspark_sps_table.json)
    ;;
  *) printf 'invalid SPS mode: %s\n' "$SPS_MODE" >&2; exit 2 ;;
esac

case "$NEXTN_LAYERS" in
  checkpoint) ;;
  1)
    MODEL_OVERRIDE_ARGS=(--json-model-override-args '{"num_nextn_predict_layers":1}')
    ;;
  *) printf 'invalid NextN layer selection: %s\n' "$NEXTN_LAYERS" >&2; exit 2 ;;
esac

case "$PROFILE_MODE" in
  none)
    AUTH_ARGS=(--api-key "$(</home/milo/.glm_api_key)")
    ;;
  sps|sps-additive)
    HOST=127.0.0.1
    if [[ -z "${PROFILER_HOST_PATH:-}" || ! -f "$PROFILER_HOST_PATH" ]]; then
      printf 'missing patched profiler host path for profile mode %s\n' "$PROFILE_MODE" >&2
      exit 2
    fi
    PROFILER_MOUNTS=(-v "$PROFILER_HOST_PATH":/sgl-workspace/sglang/python/sglang/benchmark/dspark_sps_profiler.py:ro)
    ENV_ARGS+=(
      -e SGLANG_DSPARK_ENABLE_SPS_RECORD=1
      -e SGLANG_SIMULATE_ACC_LEN=1.0
    )
    ;;
  *) printf 'invalid profile mode: %s\n' "$PROFILE_MODE" >&2; exit 2 ;;
esac

if docker inspect "$NAME" >/dev/null 2>&1; then
  printf 'container already exists: %s\n' "$NAME" >&2
  exit 3
fi

docker run -d --name "$NAME" --gpus all --ipc host --network host \
  --ulimit memlock=-1 --ulimit stack=67108864 --cap-add IPC_LOCK --cap-add SYS_NICE \
  "${ENV_ARGS[@]}" \
  -v "$MODEL":/model:ro \
  "${SPS_MOUNTS[@]}" \
  "${PROFILER_MOUNTS[@]}" \
  -v /home/milo/ds4f-vision-exp/patches/encoding_dsv4.py:/sgl-workspace/sglang/python/sglang/srt/entrypoints/openai/encoding_dsv4.py:ro \
  -v "$CACHE/root-cache":/root/.cache \
  -v "$CACHE/tilelang":/root/.tilelang \
  -v "$CACHE/triton":/root/.triton \
  -v "$CACHE/nv":/root/.nv \
  "$IMAGE" \
  python3 -m sglang.launch_server --trust-remote-code --model-path /model --tp 1 \
    --mem-fraction-static "$MEM" --context-length "$CTX" \
    --chunked-prefill-size 8192 \
    --cuda-graph-max-bs-decode 64 --cuda-graph-bs-decode 1 2 4 8 16 32 64 \
    --max-running-requests 64 --enable-metrics --host "$HOST" --port 30003 \
    --served-model-name dsf-vision-exp "${AUTH_ARGS[@]}" \
    --tool-call-parser deepseekv4 --reasoning-parser deepseek-v4 \
    "${MODEL_OVERRIDE_ARGS[@]}" \
    --speculative-algorithm DSPARK \
    "${EXTRA_ARGS[@]}"

printf 'launched %s ragged=%s sps=%s profile=%s nextn=%s ctx=%s mem=%s\n' \
  "$NAME" "$RAGGED_MODE" "$SPS_MODE" "$PROFILE_MODE" "$NEXTN_LAYERS" "$CTX" "$MEM"

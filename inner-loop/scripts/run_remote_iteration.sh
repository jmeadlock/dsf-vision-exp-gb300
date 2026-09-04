#!/usr/bin/env bash
# Execute one frozen baseline contract on the Station under an exclusive lock.
set -Eeuo pipefail

RUN_ID="${1:?run id}"
CONTAINER="${2:?container}"
ROOT="${ROOT:-/home/milo/dsfv-inner-loop}"
RUN_DIR="$ROOT/runs/$RUN_ID"
BIN="$ROOT/bin"
CONTRACT="$ROOT/queue/000-baseline.json"
CONTROL="$ROOT/CONTROL"
LOCK="$ROOT/station.lock"
STARTED_EPOCH=""

mkdir -p "$RUN_DIR"
cp "$CONTRACT" "$RUN_DIR/contract.json"
exec 9>"$LOCK"
if ! flock -n 9; then
  printf 'BLOCKED another iteration owns %s\n' "$LOCK" | tee "$RUN_DIR/stage.txt"
  exit 75
fi

check_control() {
  if [[ "$(tr -d '[:space:]' < "$CONTROL")" != "RUN" ]]; then
    printf 'STOP_REQUESTED at=%s\n' "$(date -Is)" | tee -a "$RUN_DIR/stage.txt"
    return 130
  fi
}

cleanup() {
  local rc=$?
  trap - EXIT INT TERM HUP
  set +e
  if [[ -n "$STARTED_EPOCH" ]]; then
    docker logs --since "$STARTED_EPOCH" "$CONTAINER" >"$RUN_DIR/server.log" 2>&1
  else
    : >"$RUN_DIR/server.log"
  fi

  if grep -Eiq 'Xid|CUDA error|illegal memory|Segmentation fault|NCCL error|out of memory|Traceback \(most recent call last\)' "$RUN_DIR/server.log"; then
    printf 'GPU_ERROR_RESULT FAIL\n' >"$RUN_DIR/gpu-errors.txt"
    grep -Ein 'Xid|CUDA error|illegal memory|Segmentation fault|NCCL error|out of memory|Traceback \(most recent call last\)' "$RUN_DIR/server.log" >>"$RUN_DIR/gpu-errors.txt"
    [[ $rc -ne 0 ]] || rc=1
  else
    printf 'GPU_ERROR_RESULT PASS\n' >"$RUN_DIR/gpu-errors.txt"
  fi

  if docker inspect "$CONTAINER" >/dev/null 2>&1 && [[ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null)" == "true" ]]; then
    docker stop -t 120 "$CONTAINER" >>"$RUN_DIR/stop.txt" 2>&1
  fi
  if docker inspect "$CONTAINER" >/dev/null 2>&1 && [[ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null)" == "false" ]]; then
    printf 'STOP_RESULT PASS container=%s\n' "$CONTAINER" >>"$RUN_DIR/stop.txt"
  else
    printf 'STOP_RESULT FAIL container=%s\n' "$CONTAINER" >>"$RUN_DIR/stop.txt"
    rc=1
  fi
  if curl -fsS --max-time 3 http://127.0.0.1:30003/v1/models >/dev/null 2>&1; then
    printf 'PORT_AFTER_STOP FAIL\n' >>"$RUN_DIR/stop.txt"
    rc=1
  else
    printf 'PORT_AFTER_STOP PASS\n' >>"$RUN_DIR/stop.txt"
  fi
  date -Is >"$RUN_DIR/finished-at.txt"
  printf 'REMOTE_ITERATION_EXIT rc=%s at=%s\n' "$rc" "$(date -Is)" | tee -a "$RUN_DIR/stage.txt"
  exit "$rc"
}
trap cleanup EXIT INT TERM HUP

printf 'RUN_ID=%s\nSTARTED_AT=%s\nCONTAINER=%s\n' "$RUN_ID" "$(date -Is)" "$CONTAINER" >"$RUN_DIR/preflight.txt"
check_control

if [[ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER")" != "false" ]]; then
  printf 'BLOCKED expected stopped container\n' | tee -a "$RUN_DIR/preflight.txt"
  exit 3
fi
RUNNING="$(docker ps --format '{{.Names}}')"
if [[ -n "$RUNNING" ]]; then
  printf 'BLOCKED other running containers:\n%s\n' "$RUNNING" | tee -a "$RUN_DIR/preflight.txt"
  exit 4
fi
if curl -fsS --max-time 3 http://127.0.0.1:30003/v1/models >/dev/null 2>&1; then
  printf 'BLOCKED port 30003 already online\n' | tee -a "$RUN_DIR/preflight.txt"
  exit 5
fi

EXPECTED_DIGEST="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["target"]["image_digest"])' "$RUN_DIR/contract.json")"
IMAGE_ID="$(docker inspect -f '{{.Image}}' "$CONTAINER")"
if ! docker image inspect "$IMAGE_ID" --format '{{range .RepoDigests}}{{println .}}{{end}}' | grep -Fq "$EXPECTED_DIGEST"; then
  printf 'BLOCKED image digest mismatch expected=%s image_id=%s\n' "$EXPECTED_DIGEST" "$IMAGE_ID" | tee -a "$RUN_DIR/preflight.txt"
  exit 6
fi
printf 'PREFLIGHT_RESULT PASS image_id=%s digest=%s\n' "$IMAGE_ID" "$EXPECTED_DIGEST" | tee -a "$RUN_DIR/preflight.txt"

date +%s >"$RUN_DIR/started-epoch.txt"
STARTED_EPOCH="$(<"$RUN_DIR/started-epoch.txt")"
docker start "$CONTAINER" | tee "$RUN_DIR/launch.txt"
printf 'WAIT_READY\n' | tee -a "$RUN_DIR/stage.txt"
export API_KEY="$(</home/milo/.glm_api_key)"
READY=0
for _ in $(seq 1 80); do
  check_control
  if curl -fsS --max-time 5 -H "Authorization: Bearer $API_KEY" http://127.0.0.1:30003/v1/models >"$RUN_DIR/models.json" 2>/dev/null; then
    READY=1
    break
  fi
  if [[ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null)" != "true" ]]; then
    printf 'BLOCKED container exited during startup\n' | tee -a "$RUN_DIR/stage.txt"
    exit 7
  fi
  sleep 15
done
if [[ $READY -ne 1 ]]; then
  printf 'BLOCKED readiness timeout\n' | tee -a "$RUN_DIR/stage.txt"
  exit 8
fi
printf 'READY at=%s\n' "$(date -Is)" | tee -a "$RUN_DIR/stage.txt"

docker inspect "$CONTAINER" >"$RUN_DIR/container-inspect.json"
docker image inspect "$IMAGE_ID" >"$RUN_DIR/image-inspect.json"
curl -fsS -H "Authorization: Bearer $API_KEY" http://127.0.0.1:30003/server_info >"$RUN_DIR/server-info.json"
printf 'HEALTH_RESULT PASS\n' >"$RUN_DIR/health.txt"

PATCH_COUNT="$(docker exec "$CONTAINER" grep -c 'PATCHED (jmeadlock' /sgl-workspace/sglang/python/sglang/srt/entrypoints/openai/encoding_dsv4.py)"
if [[ "$PATCH_COUNT" == "1" ]]; then
  printf 'PATCH_RESULT PASS count=1\n' >"$RUN_DIR/patch.txt"
else
  printf 'PATCH_RESULT FAIL count=%s\n' "$PATCH_COUNT" >"$RUN_DIR/patch.txt"
  exit 9
fi

docker logs --since "$STARTED_EPOCH" "$CONTAINER" >"$RUN_DIR/startup.log" 2>&1
if grep -Fq 'SGLANG_RAGGED_VERIFY_MODE=static; it will be a no-op' "$RUN_DIR/startup.log"; then
  printf 'RUNTIME_MODE_RESULT PASS mode=static sps_effective=false\n' >"$RUN_DIR/runtime-mode.txt"
else
  printf 'RUNTIME_MODE_RESULT FAIL expected_static_sps_noop_warning\n' >"$RUN_DIR/runtime-mode.txt"
  exit 10
fi

check_control
printf 'PHASE smoke\n' | tee -a "$RUN_DIR/stage.txt"
python3 "$BIN/dsfv_smoke.py" | tee "$RUN_DIR/smoke.txt"
check_control
printf 'PHASE replay\n' | tee -a "$RUN_DIR/stage.txt"
python3 "$BIN/replay_gate.py" | tee "$RUN_DIR/replay.txt"
check_control
printf 'PHASE opaque\n' | tee -a "$RUN_DIR/stage.txt"
C=64 N=64 python3 "$BIN/opaque_identifier_gate.py" | tee "$RUN_DIR/opaque.txt"
check_control
printf 'PHASE throughput-c1\n' | tee -a "$RUN_DIR/stage.txt"
BASE_URL=http://127.0.0.1:30003/v1 MODEL=dsf-vision-exp CONCS=1 REPS=6 OUT_TOKENS=1024 python3 "$BIN/bench_dsf.py" | tee "$RUN_DIR/throughput.txt"
check_control
printf 'PHASE throughput-c4-c64\n' | tee -a "$RUN_DIR/stage.txt"
BASE_URL=http://127.0.0.1:30003/v1 MODEL=dsf-vision-exp CONCS='4 8 16 32 64' REPS=3 OUT_TOKENS=1024 python3 "$BIN/bench_dsf.py" | tee -a "$RUN_DIR/throughput.txt"
check_control
printf 'PHASE prefill\n' | tee -a "$RUN_DIR/stage.txt"
BASE_URL=http://127.0.0.1:30003/v1 MODEL=dsf-vision-exp SIZES='8000 32000 64000 128000 256000' N=3 python3 "$BIN/prefill.py" | tee "$RUN_DIR/prefill.txt"
check_control
printf 'PHASE repetition\n' | tee -a "$RUN_DIR/stage.txt"
BASE_URL=http://127.0.0.1:30003/v1 MODEL=dsf-vision-exp C=64 N=128 OUT="$RUN_DIR/repaudit.json" python3 "$BIN/repaudit.py" | tee "$RUN_DIR/repaudit.txt"
if ! grep -Eq '\bflagged=0\b' "$RUN_DIR/repaudit.txt"; then
  exit 11
fi
check_control

curl -fsS -H "Authorization: Bearer $API_KEY" http://127.0.0.1:30003/v1/models >/dev/null
printf 'HEALTH_POST_RESULT PASS\n' >>"$RUN_DIR/health.txt"
curl -fsS -H "Authorization: Bearer $API_KEY" http://127.0.0.1:30003/metrics >"$RUN_DIR/metrics-final.txt"
nvidia-smi --query-gpu=index,name,memory.used,memory.total,utilization.gpu,power.draw --format=csv >"$RUN_DIR/gpu-final.csv"
printf 'PHASE complete\n' | tee -a "$RUN_DIR/stage.txt"

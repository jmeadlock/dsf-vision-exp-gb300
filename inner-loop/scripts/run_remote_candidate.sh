#!/usr/bin/env bash
# Execute one frozen candidate contract on the Station under an exclusive lock.
set -Eeuo pipefail

RUN_ID="${1:?run id}"
CONTRACT="${2:?remote contract path}"
ROOT="${ROOT:-/home/milo/dsfv-inner-loop}"
RUN_DIR="$ROOT/runs/$RUN_ID"
BIN="$ROOT/bin"
CONTROL="$ROOT/CONTROL"
LOCK="$ROOT/station.lock"
STARTED_EPOCH=""
GATES_CONTAINER=""
API_KEY_FILE="${API_KEY_FILE:-/home/milo/.glm_api_key}"
if [[ ! -r "$API_KEY_FILE" ]]; then
  printf 'BLOCKED API key file is unreadable\n' >&2
  exit 2
fi
API_KEY="$(tr -d '\r\n' <"$API_KEY_FILE")"
if [[ -z "$API_KEY" ]]; then
  printf 'BLOCKED API key file is empty\n' >&2
  exit 2
fi

auth_curl() {
  curl --config <(printf 'header = "Authorization: Bearer %s"\n' "$API_KEY") "$@"
}

readarray -t CONTRACT_FIELDS < <(python3 - "$CONTRACT" <<'PY'
import json, sys
c = json.load(open(sys.argv[1]))
print(c["target"]["container"])
print(c["target"]["image_digest"])
print(c["candidate"]["ragged_verify_mode"])
print(c["candidate"]["sps_table"])
print(c["candidate"].get("profile_mode", "none"))
print(c["target"].get("sps_table_sha256", "none"))
print(c["target"].get("profiler_sha256", "none"))
print(c["target"].get("sps_module_sha256", "none"))
print(c["target"].get("runner_sha256", "none"))
print(c["candidate"].get("num_nextn_predict_layers", "checkpoint"))
PY
)
if [[ "${#CONTRACT_FIELDS[@]}" -ne 10 ]]; then
  printf 'BLOCKED invalid contract fields\n' >&2
  exit 2
fi
CONTAINER="${CONTRACT_FIELDS[0]}"
EXPECTED_DIGEST="${CONTRACT_FIELDS[1]}"
RAGGED_MODE="${CONTRACT_FIELDS[2]}"
SPS_MODE="${CONTRACT_FIELDS[3]}"
PROFILE_MODE="${CONTRACT_FIELDS[4]}"
EXPECTED_SPS_DIGEST="${CONTRACT_FIELDS[5]}"
EXPECTED_PROFILER_DIGEST="${CONTRACT_FIELDS[6]}"
EXPECTED_SPS_MODULE_DIGEST="${CONTRACT_FIELDS[7]}"
EXPECTED_RUNNER_DIGEST="${CONTRACT_FIELDS[8]}"
NEXTN_LAYERS="${CONTRACT_FIELDS[9]}"
SPS_ARTIFACT_PATH="$ROOT/artifacts/$EXPECTED_SPS_DIGEST.json"

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

stop_if_present() {
  local name="$1"
  if ! docker inspect "$name" >/dev/null 2>&1; then
    return 0
  fi
  if [[ "$(docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null)" == "true" ]]; then
    docker stop -t 120 "$name" >>"$RUN_DIR/stop.txt" 2>&1
  fi
  if [[ "$(docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null)" == "false" ]]; then
    printf 'STOP_RESULT PASS container=%s\n' "$name" >>"$RUN_DIR/stop.txt"
    return 0
  fi
  printf 'STOP_RESULT FAIL container=%s\n' "$name" >>"$RUN_DIR/stop.txt"
  return 1
}

cleanup() {
  local rc=$?
  trap - EXIT INT TERM HUP
  set +e
  if [[ -n "$STARTED_EPOCH" ]] && docker inspect "$CONTAINER" >/dev/null 2>&1; then
    docker logs --since "$STARTED_EPOCH" "$CONTAINER" >"$RUN_DIR/server.log" 2>&1
  else
    : >"$RUN_DIR/server.log"
  fi

  SENSITIVE_RECEIPTS=()
  for receipt in server.log startup.log server-info.json container-inspect.json \
      server-tier0.log startup-tier0.log server-info-tier0.json container-inspect-tier0.json; do
    if [[ -f "$RUN_DIR/$receipt" ]]; then
      SENSITIVE_RECEIPTS+=("$RUN_DIR/$receipt")
    fi
  done
  if [[ "${#SENSITIVE_RECEIPTS[@]}" -gt 0 ]]; then
    if ! python3 "$BIN/sanitize_receipts.py" \
      --secret-file /home/milo/.glm_api_key \
      "${SENSITIVE_RECEIPTS[@]}" >"$RUN_DIR/receipt-sanitize.txt"; then
      printf 'RECEIPT_SANITIZE_RESULT FAIL\n' >>"$RUN_DIR/receipt-sanitize.txt"
      rc=1
    else
      printf 'RECEIPT_SANITIZE_RESULT PASS\n' >>"$RUN_DIR/receipt-sanitize.txt"
    fi
  fi

  ERROR_LOGS=("$RUN_DIR/server.log")
  if [[ -f "$RUN_DIR/server-tier0.log" ]]; then
    ERROR_LOGS+=("$RUN_DIR/server-tier0.log")
  fi
  if grep -Eiq 'Xid|CUDA error|illegal memory|Segmentation fault|NCCL error|out of memory|Traceback \(most recent call last\)' "${ERROR_LOGS[@]}"; then
    printf 'GPU_ERROR_RESULT FAIL\n' >"$RUN_DIR/gpu-errors.txt"
    grep -Ein 'Xid|CUDA error|illegal memory|Segmentation fault|NCCL error|out of memory|Traceback \(most recent call last\)' "${ERROR_LOGS[@]}" >>"$RUN_DIR/gpu-errors.txt"
    [[ $rc -ne 0 ]] || rc=1
  else
    printf 'GPU_ERROR_RESULT PASS logs=%s\n' "${#ERROR_LOGS[@]}" >"$RUN_DIR/gpu-errors.txt"
  fi

  local stop_rc=0
  local any_container=0
  for name in $GATES_CONTAINER $CONTAINER; do
    [[ -z "$name" ]] && continue
    if docker inspect "$name" >/dev/null 2>&1; then
      any_container=1
      stop_if_present "$name" || stop_rc=1
    fi
  done
  if [[ $any_container -eq 0 ]]; then
    printf 'STOP_RESULT PASS no-experiment-container\n' >>"$RUN_DIR/stop.txt"
  elif [[ $stop_rc -ne 0 ]]; then
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

wait_ready() {
  local name="$1"
  local models_out="$2"
  printf 'WAIT_READY container=%s\n' "$name" | tee -a "$RUN_DIR/stage.txt"
  local ready=0
  local _
  for _ in $(seq 1 80); do
    check_control
    if auth_curl -fsS --max-time 5 http://127.0.0.1:30003/v1/models >"$models_out" 2>/dev/null; then
      ready=1
      break
    fi
    if [[ "$(docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null)" != "true" ]]; then
      printf 'BLOCKED container exited during startup name=%s\n' "$name" | tee -a "$RUN_DIR/stage.txt"
      exit 7
    fi
    sleep 15
  done
  if [[ $ready -ne 1 ]]; then
    printf 'BLOCKED readiness timeout name=%s\n' "$name" | tee -a "$RUN_DIR/stage.txt"
    exit 8
  fi
  printf 'READY container=%s at=%s\n' "$name" "$(date -Is)" | tee -a "$RUN_DIR/stage.txt"
}

launch_named() {
  local name="$1"
  local ragged="$2"
  local sps="$3"
  local profile="$4"
  local launch_out="$5"
  date +%s >"$RUN_DIR/started-epoch.txt"
  STARTED_EPOCH="$(<"$RUN_DIR/started-epoch.txt")"
  "$BIN/launch-dsfv-experiment.sh" "$name" "$ragged" "$sps" "$profile" "$NEXTN_LAYERS" | tee "$launch_out"
  local image_id
  image_id="$(docker inspect -f '{{.Image}}' "$name")"
  local image_digests
  image_digests="$(docker image inspect "$image_id" --format '{{range .RepoDigests}}{{println .}}{{end}}')"
  if ! grep -Fq "$EXPECTED_DIGEST" <<<"$image_digests"; then
    printf 'BLOCKED image digest mismatch expected=%s image_id=%s name=%s\n' \
      "$EXPECTED_DIGEST" "$image_id" "$name" | tee -a "$RUN_DIR/stage.txt"
    exit 6
  fi
  if [[ "$sps" != "none" ]]; then
    local mounted_sps_digest
    mounted_sps_digest="$(docker exec "$name" sha256sum /dspark_sps_table.json | cut -d' ' -f1)"
    if [[ "$mounted_sps_digest" != "$EXPECTED_SPS_DIGEST" ]]; then
      printf 'BLOCKED mounted SPS hash mismatch expected=%s actual=%s name=%s\n' \
        "$EXPECTED_SPS_DIGEST" "$mounted_sps_digest" "$name" | tee -a "$RUN_DIR/stage.txt"
      exit 6
    fi
    printf 'SPS_MOUNT_HASH_RESULT PASS sha256=%s mode=%s container=%s\n' \
      "$mounted_sps_digest" "$sps" "$name" >>"$RUN_DIR/sps-mount-hash.txt"
  fi
  IMAGE_ID="$image_id"
}

capture_common() {
  local name="$1"
  local prefix="$2"
  docker inspect "$name" >"$RUN_DIR/container-inspect${prefix}.json"
  python3 "$BIN/sanitize_inspect.py" "$RUN_DIR/container-inspect${prefix}.json" >"$RUN_DIR/inspect-sanitize${prefix}.txt"
  docker image inspect "$IMAGE_ID" >"$RUN_DIR/image-inspect${prefix}.json"
  auth_curl -fsS http://127.0.0.1:30003/server_info >"$RUN_DIR/server-info${prefix}.json"
  docker logs --since "$STARTED_EPOCH" "$name" >"$RUN_DIR/startup${prefix}.log" 2>&1
  local patch_count
  patch_count="$(docker exec "$name" grep -c 'PATCHED (jmeadlock' /sgl-workspace/sglang/python/sglang/srt/entrypoints/openai/encoding_dsv4.py || true)"
  if [[ "$patch_count" == "1" ]]; then
    printf 'PATCH_RESULT PASS count=1 container=%s\n' "$name" | tee -a "$RUN_DIR/patch.txt" >/dev/null
    printf 'PATCH_RESULT PASS count=1 container=%s\n' "$name" >"$RUN_DIR/patch${prefix}.txt"
  else
    printf 'PATCH_RESULT FAIL count=%s container=%s\n' "$patch_count" "$name" | tee "$RUN_DIR/patch.txt"
    exit 9
  fi
}

printf 'RUN_ID=%s\nSTARTED_AT=%s\nCONTAINER=%s\nRAGGED=%s\nSPS=%s\nPROFILE=%s\nNEXTN_LAYERS=%s\n' \
  "$RUN_ID" "$(date -Is)" "$CONTAINER" "$RAGGED_MODE" "$SPS_MODE" "$PROFILE_MODE" "$NEXTN_LAYERS" >"$RUN_DIR/preflight.txt"
if [[ "$EXPECTED_RUNNER_DIGEST" != "none" ]]; then
  ACTUAL_RUNNER_DIGEST="$(sha256sum "$0" | cut -d' ' -f1)"
  if [[ "$ACTUAL_RUNNER_DIGEST" != "$EXPECTED_RUNNER_DIGEST" ]]; then
    printf 'BLOCKED runner hash mismatch expected=%s actual=%s\n' \
      "$EXPECTED_RUNNER_DIGEST" "$ACTUAL_RUNNER_DIGEST" | tee -a "$RUN_DIR/preflight.txt"
    exit 6
  fi
  printf 'RUNNER_HASH_RESULT PASS sha256=%s\n' "$ACTUAL_RUNNER_DIGEST" >>"$RUN_DIR/preflight.txt"
fi
check_control

if [[ "$PROFILE_MODE" == "sps" || "$PROFILE_MODE" == "sps-additive" ]]; then
  GATES_CONTAINER="${CONTAINER}-tier0"
fi

for name in $CONTAINER $GATES_CONTAINER; do
  [[ -z "$name" ]] && continue
  if docker inspect "$name" >/dev/null 2>&1; then
    printf 'BLOCKED candidate container already exists name=%s\n' "$name" | tee -a "$RUN_DIR/preflight.txt"
    exit 3
  fi
done
RUNNING="$(docker ps --format '{{.Names}}')"
if [[ -n "$RUNNING" ]]; then
  printf 'BLOCKED other running containers:\n%s\n' "$RUNNING" | tee -a "$RUN_DIR/preflight.txt"
  exit 4
fi
if curl -fsS --max-time 3 http://127.0.0.1:30003/v1/models >/dev/null 2>&1; then
  printf 'BLOCKED port 30003 already online\n' | tee -a "$RUN_DIR/preflight.txt"
  exit 5
fi
if [[ "$SPS_MODE" != "none" ]]; then
  case "$SPS_MODE" in
    current|calibrated) ;;
    *)
      printf 'BLOCKED unsupported SPS mode=%s\n' "$SPS_MODE" | tee -a "$RUN_DIR/preflight.txt"
      exit 2
      ;;
  esac
  if [[ ! -f "$SPS_ARTIFACT_PATH" ]]; then
    printf 'BLOCKED missing SPS artifact path=%s\n' "$SPS_ARTIFACT_PATH" | tee -a "$RUN_DIR/preflight.txt"
    exit 6
  fi
  ACTUAL_SPS_DIGEST="$(sha256sum "$SPS_ARTIFACT_PATH" | cut -d' ' -f1)"
  if [[ "$ACTUAL_SPS_DIGEST" != "$EXPECTED_SPS_DIGEST" ]]; then
    printf 'BLOCKED SPS hash mismatch expected=%s actual=%s\n' \
      "$EXPECTED_SPS_DIGEST" "$ACTUAL_SPS_DIGEST" | tee -a "$RUN_DIR/preflight.txt"
    exit 6
  fi
  printf 'SPS_ARTIFACT_HASH_RESULT PASS sha256=%s mode=%s\n' \
    "$ACTUAL_SPS_DIGEST" "$SPS_MODE" >>"$RUN_DIR/preflight.txt"
  export SPS_TABLE_HOST_PATH="$SPS_ARTIFACT_PATH"
fi
printf 'PREFLIGHT_RESULT PASS\n' | tee -a "$RUN_DIR/preflight.txt"

export API_KEY="$(</home/milo/.glm_api_key)"

if [[ "$PROFILE_MODE" == "sps" || "$PROFILE_MODE" == "sps-additive" ]]; then
  printf 'PHASE tier0-correctness\n' | tee -a "$RUN_DIR/stage.txt"
  launch_named "$GATES_CONTAINER" "static" "none" "none" "$RUN_DIR/launch-tier0.txt"
  wait_ready "$GATES_CONTAINER" "$RUN_DIR/models-tier0.json"
  capture_common "$GATES_CONTAINER" "-tier0"
  printf 'HEALTH_RESULT PASS phase=tier0\n' >"$RUN_DIR/health.txt"
  python3 - "$RUN_DIR/server-info-tier0.json" "$RUN_DIR/container-inspect-tier0.json" >"$RUN_DIR/runtime-mode-tier0.txt" <<'PY'
import json, sys
info = json.load(open(sys.argv[1]))
inspect = json.load(open(sys.argv[2]))[0]
env = {}
for item in inspect.get("Config", {}).get("Env", []):
    key, sep, value = item.partition("=")
    if sep:
        env[key] = value
if env.get("SGLANG_RAGGED_VERIFY_MODE") != "static":
    raise SystemExit(
        f"RUNTIME_MODE_RESULT FAIL env_mode={env.get('SGLANG_RAGGED_VERIFY_MODE')!r}"
    )
if "SGLANG_SIMULATE_ACC_LEN" in env:
    raise SystemExit("RUNTIME_MODE_RESULT FAIL simulate_acc_len is set in tier0")
if "SGLANG_DSPARK_ENABLE_SPS_RECORD" in env:
    raise SystemExit("RUNTIME_MODE_RESULT FAIL SPS recording is enabled in tier0")
states = info.get("internal_states") or []
record = states[0] if states else info
expected = {
    "host": "0.0.0.0",
    "speculative_algorithm": "DSPARK",
    "speculative_num_draft_tokens": 6,
    "context_length": 1048576,
    "chunked_prefill_size": 8192,
    "max_running_requests": 64,
}
for key, value in expected.items():
    if record.get(key) != value:
        raise SystemExit(
            f"RUNTIME_MODE_RESULT FAIL {key}={record.get(key)!r} expected={value!r}"
        )
if not record.get("api_key"):
    raise SystemExit("RUNTIME_MODE_RESULT FAIL tier0 API authentication is disabled")
if float(record.get("mem_fraction_static", -1)) != 0.9:
    raise SystemExit(
        f"RUNTIME_MODE_RESULT FAIL mem_fraction_static={record.get('mem_fraction_static')!r}"
    )
print("RUNTIME_MODE_RESULT PASS mode=static sps_profile=false simulate_acc_len=unset")
PY
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
  printf 'PHASE repetition\n' | tee -a "$RUN_DIR/stage.txt"
  BASE_URL=http://127.0.0.1:30003/v1 MODEL=dsf-vision-exp C=64 N=128 OUT="$RUN_DIR/repaudit.json" python3 "$BIN/repaudit.py" | tee "$RUN_DIR/repaudit.txt"
  if ! grep -Fq 'flagged=0' "$RUN_DIR/repaudit.txt"; then
    exit 11
  fi
  check_control
  docker logs --since "$STARTED_EPOCH" "$GATES_CONTAINER" >"$RUN_DIR/server-tier0.log" 2>&1
  stop_if_present "$GATES_CONTAINER" || exit 12
  if curl -fsS --max-time 3 http://127.0.0.1:30003/v1/models >/dev/null 2>&1; then
    printf 'BLOCKED port 30003 still online after tier0 stop\n' | tee -a "$RUN_DIR/stage.txt"
    exit 12
  fi

  printf 'PHASE sps-profile-runtime\n' | tee -a "$RUN_DIR/stage.txt"
  launch_named "$CONTAINER" "$RAGGED_MODE" "$SPS_MODE" "$PROFILE_MODE" "$RUN_DIR/launch.txt"
  wait_ready "$CONTAINER" "$RUN_DIR/models.json"
  capture_common "$CONTAINER" ""
  printf 'HEALTH_RESULT PASS phase=sps-profile\n' >>"$RUN_DIR/health.txt"
  ACTUAL_PROFILER_DIGEST="$(docker exec "$CONTAINER" sha256sum /sgl-workspace/sglang/python/sglang/benchmark/dspark_sps_profiler.py | cut -d' ' -f1)"
  ACTUAL_SPS_MODULE_DIGEST="$(docker exec "$CONTAINER" sha256sum /sgl-workspace/sglang/python/sglang/srt/speculative/dspark_components/dspark_sps.py | cut -d' ' -f1)"
  if [[ "$ACTUAL_PROFILER_DIGEST" != "$EXPECTED_PROFILER_DIGEST" ]]; then
    printf 'BLOCKED profiler hash mismatch expected=%s actual=%s\n' \
      "$EXPECTED_PROFILER_DIGEST" "$ACTUAL_PROFILER_DIGEST" | tee -a "$RUN_DIR/stage.txt"
    exit 9
  fi
  if [[ "$ACTUAL_SPS_MODULE_DIGEST" != "$EXPECTED_SPS_MODULE_DIGEST" ]]; then
    printf 'BLOCKED SPS module hash mismatch expected=%s actual=%s\n' \
      "$EXPECTED_SPS_MODULE_DIGEST" "$ACTUAL_SPS_MODULE_DIGEST" | tee -a "$RUN_DIR/stage.txt"
    exit 9
  fi
  printf 'PROFILER_HASH_RESULT PASS sha256=%s\nSPS_MODULE_HASH_RESULT PASS sha256=%s\n' \
    "$ACTUAL_PROFILER_DIGEST" "$ACTUAL_SPS_MODULE_DIGEST" >"$RUN_DIR/profile-hash.txt"
  python3 - "$RUN_DIR/server-info.json" "$RUN_DIR/container-inspect.json" "$PROFILE_MODE" >"$RUN_DIR/runtime-mode.txt" <<'PY'
import json, sys
info = json.load(open(sys.argv[1]))
inspect = json.load(open(sys.argv[2]))[0]
profile_mode = sys.argv[3]
expected_mode = "compact" if profile_mode == "sps-additive" else "static"
env = {}
for item in inspect.get("Config", {}).get("Env", []):
    key, sep, value = item.partition("=")
    if sep:
        env[key] = value
if env.get("SGLANG_RAGGED_VERIFY_MODE") != expected_mode:
    raise SystemExit(
        f"RUNTIME_MODE_RESULT FAIL env_mode={env.get('SGLANG_RAGGED_VERIFY_MODE')!r} expected={expected_mode!r}"
    )
if env.get("SGLANG_DSPARK_ENABLE_SPS_RECORD") != "1":
    raise SystemExit("RUNTIME_MODE_RESULT FAIL SPS recording env is not 1")
try:
    simulated = float(env.get("SGLANG_SIMULATE_ACC_LEN", "nan"))
except ValueError as exc:
    raise SystemExit("RUNTIME_MODE_RESULT FAIL invalid simulated acceptance env") from exc
if simulated != 1.0:
    raise SystemExit(
        f"RUNTIME_MODE_RESULT FAIL simulate_acc_len_env={simulated!r}"
    )
if info.get("host") != "127.0.0.1":
    raise SystemExit(
        f"RUNTIME_MODE_RESULT FAIL profile_host={info.get('host')!r}"
    )
if info.get("api_key") not in (None, ""):
    raise SystemExit("RUNTIME_MODE_RESULT FAIL loopback profiler unexpectedly uses API auth")
states = info.get("internal_states") or []
if not states:
    raise SystemExit("RUNTIME_MODE_RESULT FAIL missing internal_states")
for index, state in enumerate(states):
    record = state.get("dspark_info_record") or {}
    if record.get("mode") != expected_mode:
        raise SystemExit(
            f"RUNTIME_MODE_RESULT FAIL rank={index} mode={record.get('mode')!r} expected={expected_mode!r}"
        )
    if record.get("simulate_acc_len") != 1.0:
        raise SystemExit(
            f"RUNTIME_MODE_RESULT FAIL rank={index} simulate_acc_len={record.get('simulate_acc_len')!r}"
        )
    missing = {"core", "step_cpu_time"} - set(record.get("components") or [])
    if missing:
        raise SystemExit(f"RUNTIME_MODE_RESULT FAIL rank={index} missing={sorted(missing)}")
print(
    f"RUNTIME_MODE_RESULT PASS mode={expected_mode} profile={profile_mode} "
    "sps_profile=true simulate_acc_len=1.0 components=core,step_cpu_time"
)
PY
  check_control
  printf 'PHASE sps-profile\n' | tee -a "$RUN_DIR/stage.txt"
  PROFILE_EXTRA_ARGS=()
  if [[ "$PROFILE_MODE" == "sps-additive" ]]; then
    PROFILE_EXTRA_ARGS=(--fracs 0.25 0.5 0.75 1.0)
  fi
  docker exec "$CONTAINER" python3 -m sglang.benchmark.dspark_sps_profiler all \
    --base-url http://127.0.0.1:30003 \
    --batch-size 1 2 4 8 16 24 32 40 48 56 64 \
    --input-len 16 --temperature 1.0 \
    --min-steady-steps 32 --min-steady-seconds 10 \
    --round-timeout 180 --repeats 3 \
    --out /tmp/dsfv-sps-profile.json --max-batch-tokens 384 \
    "${PROFILE_EXTRA_ARGS[@]}" --no-plot \
    2>&1 | tee "$RUN_DIR/profile.txt"
  for name in \
    dsfv-sps-profile.json \
    dsfv-sps-profile.records.jsonl \
    dsfv-sps-profile.rounds.jsonl \
    dsfv-sps-profile.json.manifest.json; do
    docker cp "$CONTAINER:/tmp/$name" "$RUN_DIR/$name"
  done
  python3 - "$RUN_DIR" "$PROFILE_MODE" >"$RUN_DIR/profile-gate.txt" <<'PY'
import json, math, statistics, sys
from collections import Counter
from pathlib import Path

run = Path(sys.argv[1])
profile_mode = sys.argv[2]
table = json.loads((run / "dsfv-sps-profile.json").read_text())
manifest = json.loads((run / "dsfv-sps-profile.json.manifest.json").read_text())
rounds = [
    json.loads(line)
    for line in (run / "dsfv-sps-profile.rounds.jsonl").read_text().splitlines()
    if line.strip()
]
expected_bs = [1, 2, 4, 8, 16, 24, 32, 40, 48, 56, 64]
if manifest.get("batch_size_per_rank_sweep") != expected_bs or manifest.get("repeats") != 3:
    raise SystemExit("SPS_PROFILE_RESULT FAIL manifest sweep/repeats mismatch")
if manifest.get("simulate_acc_len") != 1.0 or manifest.get("verify_num_draft_tokens") != 6:
    raise SystemExit("SPS_PROFILE_RESULT FAIL manifest runtime mismatch")
if not rounds:
    raise SystemExit("SPS_PROFILE_RESULT FAIL no rounds")
min_match = min(float(row.get("match_fraction", 0)) for row in rounds)
if min_match < 0.9:
    raise SystemExit("SPS_PROFILE_RESULT FAIL match_fraction below 0.9")

if profile_mode == "sps":
    expected_tokens = [value * 6 for value in expected_bs]
    if table.get("sample_batch_tokens") != expected_tokens:
        raise SystemExit(f"SPS_PROFILE_RESULT FAIL probes={table.get('sample_batch_tokens')!r}")
    if table.get("max_batch_tokens") != 384:
        raise SystemExit(f"SPS_PROFILE_RESULT FAIL max_batch_tokens={table.get('max_batch_tokens')!r}")
    if manifest.get("fracs") is not None:
        raise SystemExit("SPS_PROFILE_RESULT FAIL diagonal manifest unexpectedly has fracs")
    if len(rounds) != len(expected_bs) * 3:
        raise SystemExit(f"SPS_PROFILE_RESULT FAIL rounds={len(rounds)}")
    print(
        f"SPS_PROFILE_RESULT PASS kind=diagonal probes={len(expected_tokens)} "
        f"rounds={len(rounds)} min_match_fraction={min_match:.3f}"
    )
    raise SystemExit(0)

if profile_mode != "sps-additive":
    raise SystemExit(f"SPS_PROFILE_RESULT FAIL unsupported profile_mode={profile_mode!r}")

expected_fracs = [0.25, 0.5, 0.75, 1.0]
if manifest.get("fracs") != expected_fracs:
    raise SystemExit(f"SPS_PROFILE_RESULT FAIL fracs={manifest.get('fracs')!r}")
expected_cells = {
    (repeat, bs, frac)
    for repeat in range(3)
    for bs in expected_bs
    for frac in expected_fracs
}
actual_cells = Counter(
    (int(row["repeat"]), int(row["batch_size_per_rank"]), float(row["frac"]))
    for row in rounds
)
if set(actual_cells) != expected_cells or any(count != 1 for count in actual_cells.values()):
    raise SystemExit("SPS_PROFILE_RESULT FAIL additive cell coverage/duplication mismatch")
expected_table_keys = {
    "bias_seconds",
    "bs_probes",
    "alpha_seconds",
    "m_probes",
    "theta_seconds",
}
if set(table) != expected_table_keys:
    raise SystemExit(f"SPS_PROFILE_RESULT FAIL additive table keys={sorted(table)}")
if table.get("bs_probes") != expected_bs:
    raise SystemExit(f"SPS_PROFILE_RESULT FAIL bs_probes={table.get('bs_probes')!r}")
expected_m = sorted(
    {
        round((bs + int(frac * bs * 5)) / 64) * 64
        for bs in expected_bs
        for frac in expected_fracs
    }
)
if table.get("m_probes") != expected_m:
    raise SystemExit(f"SPS_PROFILE_RESULT FAIL m_probes={table.get('m_probes')!r}")
if len(table.get("alpha_seconds", [])) != len(expected_bs):
    raise SystemExit("SPS_PROFILE_RESULT FAIL alpha length mismatch")
if len(table.get("theta_seconds", [])) != len(expected_m):
    raise SystemExit("SPS_PROFILE_RESULT FAIL theta length mismatch")
values = [
    float(table["bias_seconds"]),
    *map(float, table["alpha_seconds"]),
    *map(float, table["theta_seconds"]),
]
if not all(math.isfinite(value) for value in values) or float(table["bias_seconds"]) <= 0:
    raise SystemExit("SPS_PROFILE_RESULT FAIL non-finite/non-positive table")
alpha = dict(zip(expected_bs, map(float, table["alpha_seconds"])))
theta = dict(zip(expected_m, map(float, table["theta_seconds"])))
observed = []
predicted = []
for row in rounds:
    bs = int(row["batch_size_per_rank"])
    m = int(row["batch_tokens"])
    m_bin = round(m / 64) * 64
    actual = 1.0 / float(row["steps_per_sec"])
    estimate = float(table["bias_seconds"]) + alpha[bs] + theta[m_bin]
    if not math.isfinite(estimate) or estimate <= 0:
        raise SystemExit("SPS_PROFILE_RESULT FAIL non-positive fitted step time")
    observed.append(actual)
    predicted.append(estimate)
relative = sorted(abs(a - p) / a for a, p in zip(observed, predicted))
p95 = relative[math.ceil(0.95 * len(relative)) - 1]
max_relative = max(relative)
mean_observed = statistics.fmean(observed)
ss_total = sum((value - mean_observed) ** 2 for value in observed)
ss_residual = sum((a - p) ** 2 for a, p in zip(observed, predicted))
r2 = 1.0 - ss_residual / ss_total if ss_total > 0 else float("nan")
if not math.isfinite(r2) or r2 < 0.90 or p95 > 0.15 or max_relative > 0.25:
    raise SystemExit(
        f"SPS_PROFILE_RESULT FAIL additive fit r2={r2:.4f} "
        f"p95_relative={p95:.4f} max_relative={max_relative:.4f}"
    )
print(
    f"SPS_PROFILE_RESULT PASS kind=additive bs_probes={len(expected_bs)} "
    f"m_probes={len(expected_m)} cells={len(rounds)} min_match_fraction={min_match:.3f} "
    f"fit_r2={r2:.4f} fit_p95_relative={p95:.4f} fit_max_relative={max_relative:.4f}"
)
PY
  check_control
  auth_curl -fsS http://127.0.0.1:30003/v1/models >/dev/null
  printf 'HEALTH_POST_RESULT PASS\n' >>"$RUN_DIR/health.txt"
  auth_curl -fsS http://127.0.0.1:30003/metrics >"$RUN_DIR/metrics-final.txt"
  nvidia-smi --query-gpu=index,name,memory.used,memory.total,utilization.gpu,power.draw --format=csv >"$RUN_DIR/gpu-final.csv"
  printf 'PHASE complete\n' | tee -a "$RUN_DIR/stage.txt"
  exit 0
fi

launch_named "$CONTAINER" "$RAGGED_MODE" "$SPS_MODE" "$PROFILE_MODE" "$RUN_DIR/launch.txt"
wait_ready "$CONTAINER" "$RUN_DIR/models.json"
capture_common "$CONTAINER" ""
printf 'HEALTH_RESULT PASS\n' >"$RUN_DIR/health.txt"

if [[ "$RAGGED_MODE" == "static" && "$SPS_MODE" == "none" ]]; then
  python3 - "$RUN_DIR/server-info.json" "$RUN_DIR/container-inspect.json" "static-control" >"$RUN_DIR/runtime-mode.txt" <<'PY'
import json, sys
info = json.load(open(sys.argv[1]))
inspect = json.load(open(sys.argv[2]))[0]
env = {}
for item in inspect.get("Config", {}).get("Env", []):
    key, sep, value = item.partition("=")
    if sep:
        env[key] = value
if env.get("SGLANG_RAGGED_VERIFY_MODE") != "static":
    raise SystemExit(
        f"RUNTIME_MODE_RESULT FAIL env_mode={env.get('SGLANG_RAGGED_VERIFY_MODE')!r}"
    )
if "SGLANG_SIMULATE_ACC_LEN" in env:
    raise SystemExit("RUNTIME_MODE_RESULT FAIL simulate_acc_len is set")
if "SGLANG_DSPARK_ENABLE_SPS_RECORD" in env:
    raise SystemExit("RUNTIME_MODE_RESULT FAIL SPS recording is enabled")
cmd = list(inspect.get("Config", {}).get("Cmd") or [])
if "--speculative-dspark-sps-table-path" in cmd:
    raise SystemExit("RUNTIME_MODE_RESULT FAIL SPS table path is configured")
states = info.get("internal_states") or []
record = states[0] if states else info
expected = {
    "host": "0.0.0.0",
    "speculative_algorithm": "DSPARK",
    "speculative_num_draft_tokens": 6,
    "context_length": 1048576,
    "chunked_prefill_size": 8192,
    "max_running_requests": 64,
}
for key, value in expected.items():
    if record.get(key) != value:
        raise SystemExit(
            f"RUNTIME_MODE_RESULT FAIL {key}={record.get(key)!r} expected={value!r}"
        )
if not record.get("api_key"):
    raise SystemExit("RUNTIME_MODE_RESULT FAIL API authentication is disabled")
if float(record.get("mem_fraction_static", -1)) != 0.9:
    raise SystemExit(
        f"RUNTIME_MODE_RESULT FAIL mem_fraction_static={record.get('mem_fraction_static')!r}"
    )
print("RUNTIME_MODE_RESULT PASS mode=static sps=none sps_effective=false")
PY
elif [[ "$RAGGED_MODE" == "compact" && "$SPS_MODE" == "none" ]] \
  && grep -Fq 'DSpark ragged-verify scheduler enabled (mode=compact' "$RUN_DIR/startup.log" \
  && grep -Fq 'sps_table=uninitialized' "$RUN_DIR/startup.log" \
  && grep -Fq 'budget degenerates to verify-all' "$RUN_DIR/startup.log"; then
  printf 'RUNTIME_MODE_RESULT PASS mode=compact sps=uninitialized verify_all=true\n' >"$RUN_DIR/runtime-mode.txt"
elif [[ "$RAGGED_MODE" == "compact" ]] \
  && [[ "$SPS_MODE" == "current" || "$SPS_MODE" == "calibrated" ]] \
  && grep -Fq 'DSpark ragged-verify scheduler enabled (mode=compact' "$RUN_DIR/startup.log" \
  && grep -Fq 'sps_table=/dspark_sps_table.json' "$RUN_DIR/startup.log"; then
  printf 'RUNTIME_MODE_RESULT PASS mode=compact sps=%s verify_all=false\n' "$SPS_MODE" >"$RUN_DIR/runtime-mode.txt"
else
  printf 'RUNTIME_MODE_RESULT FAIL mode=%s sps=%s\n' "$RAGGED_MODE" "$SPS_MODE" >"$RUN_DIR/runtime-mode.txt"
  exit 10
fi

python3 - "$RUN_DIR/server-info.json" "$RUN_DIR/container-inspect.json" "$NEXTN_LAYERS" >>"$RUN_DIR/runtime-mode.txt" <<'PY'
import json, sys
info = json.load(open(sys.argv[1]))
inspect = json.load(open(sys.argv[2]))[0]
expected = sys.argv[3]
raw_override = info.get("json_model_override_args", "{}")
if isinstance(raw_override, str):
    try:
        effective_override = json.loads(raw_override)
    except json.JSONDecodeError as exc:
        raise SystemExit("NEXTN_OVERRIDE_RESULT FAIL invalid server_info override JSON") from exc
elif isinstance(raw_override, dict):
    effective_override = raw_override
else:
    raise SystemExit(
        f"NEXTN_OVERRIDE_RESULT FAIL unexpected server_info override={raw_override!r}"
    )
cmd = list(inspect.get("Config", {}).get("Cmd") or [])
flag = "--json-model-override-args"
if expected == "checkpoint":
    if effective_override:
        raise SystemExit(
            f"NEXTN_OVERRIDE_RESULT FAIL expected checkpoint override={effective_override!r}"
        )
    if flag in cmd:
        raise SystemExit("NEXTN_OVERRIDE_RESULT FAIL override flag present for checkpoint mode")
    print("NEXTN_OVERRIDE_RESULT PASS num_nextn_predict_layers=checkpoint")
elif expected == "1":
    wanted = {"num_nextn_predict_layers": 1}
    if effective_override != wanted:
        raise SystemExit(
            f"NEXTN_OVERRIDE_RESULT FAIL effective_override={effective_override!r} expected={wanted!r}"
        )
    if cmd.count(flag) != 1:
        raise SystemExit(
            f"NEXTN_OVERRIDE_RESULT FAIL override_flag_count={cmd.count(flag)}"
        )
    index = cmd.index(flag)
    if index + 1 >= len(cmd):
        raise SystemExit("NEXTN_OVERRIDE_RESULT FAIL override flag has no value")
    try:
        command_override = json.loads(cmd[index + 1])
    except json.JSONDecodeError as exc:
        raise SystemExit("NEXTN_OVERRIDE_RESULT FAIL command override is invalid JSON") from exc
    if command_override != wanted:
        raise SystemExit(
            f"NEXTN_OVERRIDE_RESULT FAIL command_override={command_override!r} expected={wanted!r}"
        )
    print("NEXTN_OVERRIDE_RESULT PASS num_nextn_predict_layers=1")
else:
    raise SystemExit(f"NEXTN_OVERRIDE_RESULT FAIL unsupported expected={expected!r}")
PY

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
if ! grep -Fq 'flagged=0' "$RUN_DIR/repaudit.txt"; then
  exit 11
fi
check_control

auth_curl -fsS http://127.0.0.1:30003/v1/models >/dev/null
printf 'HEALTH_POST_RESULT PASS\n' >>"$RUN_DIR/health.txt"
auth_curl -fsS http://127.0.0.1:30003/metrics >"$RUN_DIR/metrics-final.txt"
nvidia-smi --query-gpu=index,name,memory.used,memory.total,utilization.gpu,power.draw --format=csv >"$RUN_DIR/gpu-final.csv"
printf 'PHASE complete\n' | tee -a "$RUN_DIR/stage.txt"

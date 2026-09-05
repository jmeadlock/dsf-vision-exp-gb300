#!/usr/bin/env bash
# Execute one frozen candidate contract on the Station under an exclusive lock.
set -Eeuo pipefail

RUN_ID="${1:?run id}"
CONTRACT="${2:?remote contract path}"
ROOT="${ROOT:-/home/milo/dsfv-inner-loop}"
RUN_DIR="$ROOT/runs/$RUN_ID"
BIN="$ROOT/bin"
CONTROL="$ROOT/CONTROL"
RELEASE="$ROOT/RELEASE"
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
print(c.get("campaign_id", "legacy"))
print(c["target"].get("profiler_base_file_sha256", "none"))
print(c["target"].get("profiler_pr37815_patch_sha256", "none"))
print(c["target"].get("profiler_patched_file_sha256", "none"))
PY
)
if [[ "${#CONTRACT_FIELDS[@]}" -ne 14 ]]; then
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
CAMPAIGN_ID="${CONTRACT_FIELDS[10]}"
EXPECTED_PROFILER_BASE_DIGEST="${CONTRACT_FIELDS[11]}"
EXPECTED_PROFILER_PATCH_DIGEST="${CONTRACT_FIELDS[12]}"
EXPECTED_PROFILER_PATCHED_DIGEST="${CONTRACT_FIELDS[13]}"
SPS_ARTIFACT_PATH="$ROOT/artifacts/$EXPECTED_SPS_DIGEST.json"
PROFILER_HOST_PATH="$ROOT/artifacts/dspark_sps_profiler-pr37815-085a5e2.py"

mkdir -p "$RUN_DIR"
cp "$CONTRACT" "$RUN_DIR/contract.json"
if [[ "$CAMPAIGN_ID" != "legacy" ]]; then
  if [[ ! -r "$RELEASE" || "$(tr -d '[:space:]' < "$RELEASE")" != "$RUN_ID" ]]; then
    printf 'RELEASE_RESULT BLOCKED run_id=%s\n' "$RUN_ID" | tee "$RUN_DIR/stage.txt"
    exit 2
  fi
  printf 'RELEASE_RESULT PASS run_id=%s\n' "$RUN_ID" >"$RUN_DIR/stage.txt"
fi
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

  printf 'RUN_ID=%s\nSTARTED_AT=%s\nCONTAINER=%s\nRAGGED=%s\nSPS=%s\nPROFILE=%s\nNEXTN_LAYERS=%s\nCAMPAIGN_ID=%s\n' \
  "$RUN_ID" "$(date -Is)" "$CONTAINER" "$RAGGED_MODE" "$SPS_MODE" "$PROFILE_MODE" "$NEXTN_LAYERS" "$CAMPAIGN_ID" >"$RUN_DIR/preflight.txt"
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
    current|calibrated|fine-grained) ;;
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
  if [[ ! -f "$PROFILER_HOST_PATH" ]]; then
    printf 'BLOCKED missing patched profiler path=%s\n' "$PROFILER_HOST_PATH" | tee -a "$RUN_DIR/preflight.txt"
    exit 6
  fi
  ACTUAL_PROFILER_HOST_DIGEST="$(sha256sum "$PROFILER_HOST_PATH" | cut -d' ' -f1)"
  if [[ "$ACTUAL_PROFILER_HOST_DIGEST" != "$EXPECTED_PROFILER_DIGEST" ]]; then
    printf 'BLOCKED host profiler hash mismatch expected=%s actual=%s\n' \
      "$EXPECTED_PROFILER_DIGEST" "$ACTUAL_PROFILER_HOST_DIGEST" | tee -a "$RUN_DIR/preflight.txt"
    exit 6
  fi
  export PROFILER_HOST_PATH
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
  if [[ "$EXPECTED_PROFILER_PATCHED_DIGEST" != "none" ]]; then
    if [[ "$ACTUAL_PROFILER_DIGEST" != "$EXPECTED_PROFILER_PATCHED_DIGEST" ]]; then
      printf 'BLOCKED patched profiler hash mismatch expected=%s actual=%s\n' \
        "$EXPECTED_PROFILER_PATCHED_DIGEST" "$ACTUAL_PROFILER_DIGEST" | tee -a "$RUN_DIR/stage.txt"
      exit 9
    fi
    printf 'PROFILER_PATCH_RESULT PASS upstream_pr_head=%s base_sha256=%s patch_sha256=%s result_sha256=%s\n' \
      "085a5e2a734ae5dc3f820dd50f2f490dbbdcb5e7" "$EXPECTED_PROFILER_BASE_DIGEST" "$EXPECTED_PROFILER_PATCH_DIGEST" "$EXPECTED_PROFILER_PATCHED_DIGEST" >>"$RUN_DIR/profile-hash.txt"
  fi
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
  # r1 fix: profile runs exit before the shared NextN-override check below, so
  # the summarizer's nextn_override gate never saw a receipt. Emit it here for
  # the checkpoint-only profile cards (an override on a profile card is a FAIL).
  python3 - "$RUN_DIR/server-info.json" "$RUN_DIR/container-inspect.json" "$NEXTN_LAYERS" >>"$RUN_DIR/runtime-mode.txt" <<'PY'
import json, sys
info = json.load(open(sys.argv[1]))
inspect = json.load(open(sys.argv[2]))[0]
expected = sys.argv[3]
if expected != "checkpoint":
    raise SystemExit(f"NEXTN_OVERRIDE_RESULT FAIL profile cards must be checkpoint, got {expected!r}")
raw_override = info.get("json_model_override_args", "{}")
effective_override = json.loads(raw_override) if isinstance(raw_override, str) else raw_override
if effective_override:
    raise SystemExit(f"NEXTN_OVERRIDE_RESULT FAIL expected checkpoint override={effective_override!r}")
if "--json-model-override-args" in list(inspect.get("Config", {}).get("Cmd") or []):
    raise SystemExit("NEXTN_OVERRIDE_RESULT FAIL override flag present for checkpoint mode")
print("NEXTN_OVERRIDE_RESULT PASS num_nextn_predict_layers=checkpoint")
PY
  check_control
  printf 'PHASE sps-profile\n' | tee -a "$RUN_DIR/stage.txt"
  PROFILE_EXTRA_ARGS=()
  if [[ "$PROFILE_MODE" == "sps-additive" ]]; then
    PROFILE_EXTRA_ARGS=(--fracs 0.2 0.4 0.6 0.8 1.0)
  fi
  docker exec "$CONTAINER" python3 -m sglang.benchmark.dspark_sps_profiler run \
    --base-url http://127.0.0.1:30003 \
    --batch-size 1 2 3 4 5 6 7 8 \
    --input-len 16 --temperature 1.0 \
    --min-steady-steps 32 --min-steady-seconds 10 \
    --round-timeout 180 --repeats 3 \
    --out /tmp/dsfv-sps-profile.json \
    "${PROFILE_EXTRA_ARGS[@]}" \
    2>&1 | tee "$RUN_DIR/profile.txt"
  for name in \
    dsfv-sps-profile.records.jsonl \
    dsfv-sps-profile.rounds.jsonl \
    dsfv-sps-profile.json.manifest.json; do
    docker cp "$CONTAINER:/tmp/$name" "$RUN_DIR/$name"
  done
  python3 "$BIN/sps_profile_gate.py" "$RUN_DIR" >"$RUN_DIR/profile-gate.txt"

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
  && [[ "$SPS_MODE" == "current" || "$SPS_MODE" == "calibrated" || "$SPS_MODE" == "fine-grained" ]] \
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
printf 'PHASE agent-workload\n' | tee -a "$RUN_DIR/stage.txt"
python3 "$BIN/agent_workload_gate.py" --out "$RUN_DIR/agent-workload.jsonl" | tee "$RUN_DIR/agent-workload.txt"
check_control

auth_curl -fsS http://127.0.0.1:30003/v1/models >/dev/null
printf 'HEALTH_POST_RESULT PASS\n' >>"$RUN_DIR/health.txt"
auth_curl -fsS http://127.0.0.1:30003/metrics >"$RUN_DIR/metrics-final.txt"
nvidia-smi --query-gpu=index,name,memory.used,memory.total,utilization.gpu,power.draw --format=csv >"$RUN_DIR/gpu-final.csv"
printf 'PHASE complete\n' | tee -a "$RUN_DIR/stage.txt"

#!/usr/bin/env bash
# Stage, execute, retrieve, and summarize one frozen candidate contract.
set -Eeuo pipefail

CONTRACT="${1:?candidate contract path}"
HOST="${DSFV_HOST:-milo@192.168.1.9}"
REMOTE_ROOT="${REMOTE_ROOT:-/home/milo/dsfv-inner-loop}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO"

set +e
SCHEMA_RESULT="$(python3 "$REPO/inner-loop/scripts/validate_contract.py" "$CONTRACT" 2>&1)"
SCHEMA_RC=$?
set -e
printf '%s\n' "$SCHEMA_RESULT"
[[ $SCHEMA_RC -eq 0 ]] || exit "$SCHEMA_RC"

read -r SCHEMA_VERSION RUN_ID CAMPAIGN_ID < <(
  python3 -c 'import json,sys; c=json.load(open(sys.argv[1])); print(c.get("schema_version",1),c["run_id"],c.get("campaign_id","legacy"))' "$CONTRACT"
)

CAMPAIGN_ROOT="$REMOTE_ROOT/campaigns/$CAMPAIGN_ID"
CONTROL_PATH="$CAMPAIGN_ROOT/CONTROL"
RELEASE_PATH="$CAMPAIGN_ROOT/RELEASE"
RUNS_ROOT="$CAMPAIGN_ROOT/runs"
BIN_ROOT="$CAMPAIGN_ROOT/bin"
REMOTE_CONTRACT="$CAMPAIGN_ROOT/queue/$(basename "$CONTRACT")"
LOCAL_RUN="$REPO/inner-loop/campaigns/$CAMPAIGN_ID/runs/$RUN_ID"
LEDGER_PATH="$REPO/inner-loop/campaigns/$CAMPAIGN_ID/ledger.jsonl"

if [[ "$SCHEMA_VERSION" == "1" ]]; then
  CONTROL_PATH="$REMOTE_ROOT/CONTROL"
  RELEASE_PATH=""
  RUNS_ROOT="$REMOTE_ROOT/runs"
  BIN_ROOT="$REMOTE_ROOT/bin"
  REMOTE_CONTRACT="$REMOTE_ROOT/queue/$(basename "$CONTRACT")"
  LOCAL_RUN="$REPO/inner-loop/runs/$RUN_ID"
  LEDGER_PATH="$REPO/inner-loop/${LEGACY_LEDGER:-ledger.jsonl}"
fi

IFS=$'\t' read -r SPS_MODE SPS_LOCAL SPS_REMOTE < <(python3 - "$CONTRACT" "$REPO" "$REMOTE_ROOT" <<'PY'
import json, sys
from pathlib import Path
contract_path, repo_path, remote_root = sys.argv[1:]
contract = json.load(open(contract_path))
campaign_id = contract.get("campaign_id")
mode = contract["candidate"]["sps_table"]
target = contract["target"]
if mode == "none":
    print("none", "none", "none", sep="\t")
else:
    artifact = target.get("sps_table_artifact")
    if not artifact:
        artifact = "dspark_sps_tp1.json" if mode == "current" else None
    if not artifact:
        raise SystemExit("candidate table requires target.sps_table_artifact")
    repo = Path(repo_path).resolve()
    local = (repo / artifact).resolve()
    if local != repo and repo not in local.parents:
        raise SystemExit(f"SPS artifact escapes repository: {artifact}")
    digest = target["sps_table_sha256"]
    if campaign_id:
        print(mode, local, f"{remote_root}/campaigns/{campaign_id}/artifacts/{digest}.json", sep="\t")
    else:
        print(mode, local, f"{remote_root}/artifacts/{digest}.json", sep="\t")
PY
)

if [[ "${DSFV_DRY_RUN:-0}" == "1" ]]; then
  printf 'DISPATCH_DRY_RUN PASS campaign_id=%s run_id=%s campaign_root=%s local_run=%s remote_contract=%s sps_mode=%s sps_local=%s sps_remote=%s\n' \
    "$CAMPAIGN_ID" "$RUN_ID" "$CAMPAIGN_ROOT" "$LOCAL_RUN" "$REMOTE_CONTRACT" "$SPS_MODE" "$SPS_LOCAL" "$SPS_REMOTE"
  if [[ "$SCHEMA_VERSION" == "2" && "${DSFV_RELEASED_RUN:-}" != "$RUN_ID" ]]; then
    printf 'DISPATCH_RELEASE_RESULT BLOCKED run_id=%s campaign_root=%s reason=prewritten_unreleased\n' "$RUN_ID" "$CAMPAIGN_ROOT"
    exit 2
  fi
  if [[ "$SPS_MODE" != "none" && ! -f "$SPS_LOCAL" ]]; then
    printf 'DISPATCH_DRY_RUN_TABLE_RESULT BLOCKED missing_sps_artifact=%s\n' "$SPS_LOCAL"
    exit 2
  fi
  exit 0
fi

mkdir -p "$LOCAL_RUN" "$(dirname "$LEDGER_PATH")"
cp "$CONTRACT" "$LOCAL_RUN/contract.json"
printf '%s\n' "$SCHEMA_RESULT" > "$LOCAL_RUN/dispatch.txt"

ssh -o BatchMode=yes "$HOST" "mkdir -p '$BIN_ROOT' '$(dirname "$REMOTE_CONTRACT")' '$RUNS_ROOT/$RUN_ID' '$(dirname "$SPS_REMOTE")'"
scp -q "$CONTRACT" "$HOST:$REMOTE_CONTRACT"
if [[ "$SPS_MODE" != "none" ]]; then
  if [[ ! -f "$SPS_LOCAL" ]]; then
    printf 'BLOCKED missing SPS artifact: %s\n' "$SPS_LOCAL" >&2
    exit 2
  fi
  scp -q "$SPS_LOCAL" "$HOST:$SPS_REMOTE"
fi

STAGED_FILES=(
  inner-loop/scripts/run_remote_candidate.sh
  inner-loop/scripts/launch-dsfv-experiment.sh
  inner-loop/scripts/bench_dsf.py
  inner-loop/scripts/replay_gate.py
  inner-loop/scripts/agent_workload_gate.py
  inner-loop/scripts/sps_profile_gate.py
  inner-loop/scripts/opaque_identifier_gate.py
  inner-loop/scripts/sanitize_inspect.py
  inner-loop/scripts/sanitize_receipts.py
  dsfv_smoke.py
  prefill.py
  repaudit.py
)
for rel in "${STAGED_FILES[@]}"; do
  scp -q "$REPO/$rel" "$HOST:$BIN_ROOT/$(basename "$rel")"
done
ssh -o BatchMode=yes "$HOST" "chmod 755 '$BIN_ROOT/'*.sh '$BIN_ROOT/'*.py"

LOCAL_FILES=()
REMOTE_FILES=()
for rel in "${STAGED_FILES[@]}"; do
  LOCAL_FILES+=("$REPO/$rel")
  REMOTE_FILES+=("$BIN_ROOT/$(basename "$rel")")
done
if [[ "$SPS_MODE" != "none" ]]; then
  LOCAL_FILES+=("$SPS_LOCAL")
  REMOTE_FILES+=("$SPS_REMOTE")
fi
for index in "${!LOCAL_FILES[@]}"; do
  local_hash="$(shasum -a 256 "${LOCAL_FILES[$index]}" | cut -d' ' -f1)"
  remote_hash="$(ssh -o BatchMode=yes "$HOST" "sha256sum '${REMOTE_FILES[$index]}'" | cut -d' ' -f1)"
  if [[ "$local_hash" != "$remote_hash" ]]; then
    printf 'STAGE_HASH_FAIL local=%s remote=%s\n' "${LOCAL_FILES[$index]}" "${REMOTE_FILES[$index]}" >&2
    exit 70
  fi
done
printf 'STAGE_HASH_RESULT PASS files=%s\n' "${#LOCAL_FILES[@]}" | tee -a "$LOCAL_RUN/dispatch.txt"

python3 - "$CONTRACT" \
  "$REPO/inner-loop/scripts/launch-dsfv-experiment.sh" \
  "$REPO/inner-loop/scripts/bench_dsf.py" \
  "$REPO/inner-loop/scripts/run_remote_candidate.sh" \
  "$REPO/inner-loop/scripts/dispatch_candidate.sh" \
  "$REPO/inner-loop/scripts/summarize_iteration.py" \
  "$REPO/inner-loop/scripts/validate_contract.py" \
  "$REPO/inner-loop/scripts/agent_workload_gate.py" \
  "$REPO/inner-loop/scripts/sps_profile_gate.py" \
  "$REPO/inner-loop/scripts/replay_gate.py" <<'PY' | tee -a "$LOCAL_RUN/dispatch.txt"
import hashlib, json, sys
from pathlib import Path
(contract_path, launcher_path, bench_path, runner_path, dispatcher_path,
 summarizer_path, validator_path, agent_workload_path, sps_profile_gate_path,
 replay_gate_path) = sys.argv[1:]
contract = json.load(open(contract_path))
target = contract["target"]
checks = {launcher_path: target["launcher_sha256"], bench_path: target["bench_sha256"]}
for path, key in (
    (runner_path, "runner_sha256"), (dispatcher_path, "dispatcher_sha256"),
    (summarizer_path, "summarizer_sha256"), (validator_path, "validator_sha256"),
    (agent_workload_path, "agent_workload_gate_sha256"),
    (sps_profile_gate_path, "sps_profile_gate_sha256"),
    (replay_gate_path, "replay_gate_sha256"),
):
    if key in target:
        checks[path] = target[key]
if "sps_table_sha256" in target:
    repo = Path(launcher_path).resolve().parents[2]
    artifact = target.get("sps_table_artifact")
    if not artifact:
        artifact = "dspark_sps_tp1.json" if contract["candidate"]["sps_table"] == "current" else None
    if not artifact:
        raise SystemExit("FROZEN_HASH_FAIL missing target.sps_table_artifact")
    table_path = (repo / artifact).resolve()
    if table_path != repo and repo not in table_path.parents:
        raise SystemExit(f"FROZEN_HASH_FAIL SPS artifact escapes repository: {artifact}")
    checks[str(table_path)] = target["sps_table_sha256"]
for path, expected in checks.items():
    actual = hashlib.sha256(open(path, "rb").read()).hexdigest()
    if actual != expected:
        raise SystemExit(f"FROZEN_HASH_FAIL path={path} expected={expected} actual={actual}")
print(f"FROZEN_HASH_RESULT PASS files={len(checks)}")
PY

if [[ "${DSFV_STAGE_ONLY:-0}" == "1" ]]; then
  printf 'STAGE_ONLY_RESULT PASS run_id=%s\n' "$RUN_ID" | tee -a "$LOCAL_RUN/dispatch.txt"
  exit 0
fi

if [[ "$SCHEMA_VERSION" == "2" ]]; then
  set +e
  RELEASE_RESULT="$(ssh -o BatchMode=yes "$HOST" "read -r control < '$CONTROL_PATH'; read -r released < '$RELEASE_PATH'; test \"\$control\" = RUN && test \"\$released\" = '$RUN_ID'" 2>&1)"
  RELEASE_RC=$?
  set -e
  if [[ $RELEASE_RC -ne 0 ]]; then
    printf 'DISPATCH_RELEASE_RESULT BLOCKED run_id=%s campaign_root=%s detail=%s\n' "$RUN_ID" "$CAMPAIGN_ROOT" "$RELEASE_RESULT"
    exit 2
  fi
  printf 'DISPATCH_RELEASE_RESULT PASS run_id=%s campaign_root=%s\n' "$RUN_ID" "$CAMPAIGN_ROOT" | tee -a "$LOCAL_RUN/dispatch.txt"
fi

python3 - "$LEDGER_PATH" "$CONTRACT" <<'PY'
import datetime, json, sys
ledger, contract_path = sys.argv[1:]
contract = json.load(open(contract_path))
event = {
    "at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
    "event": "iteration_started", "iteration": contract.get("iteration"),
    "campaign_id": contract.get("campaign_id"), "sequence": contract.get("sequence"),
    "run_id": contract["run_id"], "slug": contract["slug"],
}
with open(ledger, "a") as handle:
    handle.write(json.dumps(event, separators=(",", ":")) + "\n")
PY

set +e
ssh -o BatchMode=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=20 "$HOST" \
  "ROOT='$CAMPAIGN_ROOT' '$BIN_ROOT/run_remote_candidate.sh' '$RUN_ID' '$REMOTE_CONTRACT'"
REMOTE_RC=$?
set -e
printf 'REMOTE_RC=%s\n' "$REMOTE_RC" | tee -a "$LOCAL_RUN/dispatch.txt"

scp -q -r "$HOST:$CAMPAIGN_ROOT/runs/$RUN_ID/." "$LOCAL_RUN/"
set +e
python3 "$REPO/inner-loop/scripts/summarize_iteration.py" "$LOCAL_RUN" | tee "$LOCAL_RUN/summary.txt"
SUMMARY_RC=${PIPESTATUS[0]}
set -e

python3 - "$LEDGER_PATH" "$LOCAL_RUN/results.json" <<'PY'
import datetime, json, sys
ledger, result_path = sys.argv[1:]
result = json.load(open(result_path))
event = {
    "at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
    "event": "iteration_finished", "iteration": result.get("iteration"),
    "campaign_id": result.get("campaign_id"), "sequence": result.get("sequence"),
    "run_id": result_path.split("/")[-2], "slug": result.get("slug"),
    "outcome": result.get("outcome"), "blockers": result.get("blockers", []),
}
with open(ledger, "a") as handle:
    handle.write(json.dumps(event, separators=(",", ":")) + "\n")
PY

if [[ $REMOTE_RC -ne 0 || $SUMMARY_RC -ne 0 ]]; then
  exit 1
fi

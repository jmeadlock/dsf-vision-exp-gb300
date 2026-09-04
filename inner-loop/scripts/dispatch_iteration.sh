#!/usr/bin/env bash
# Stage, execute, retrieve, and summarize one baseline iteration.
set -Eeuo pipefail

CONTRACT="${1:-inner-loop/queue/000-baseline.json}"
HOST="${DSFV_HOST:-milo@192.168.1.9}"
REMOTE_ROOT="${REMOTE_ROOT:-/home/milo/dsfv-inner-loop}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO"

RUN_ID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["run_id"])' "$CONTRACT")"
CONTAINER="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["target"]["container"])' "$CONTRACT")"
LOCAL_RUN="$REPO/inner-loop/runs/$RUN_ID"
mkdir -p "$LOCAL_RUN"
cp "$CONTRACT" "$LOCAL_RUN/contract.json"

ssh -o BatchMode=yes "$HOST" "mkdir -p '$REMOTE_ROOT/bin' '$REMOTE_ROOT/queue' '$REMOTE_ROOT/runs/$RUN_ID'"
scp -q \
  "$REPO/inner-loop/CONTROL" \
  "$HOST:$REMOTE_ROOT/CONTROL"
scp -q \
  "$CONTRACT" \
  "$HOST:$REMOTE_ROOT/queue/000-baseline.json"
scp -q \
  "$REPO/inner-loop/scripts/run_remote_iteration.sh" \
  "$REPO/inner-loop/scripts/replay_gate.py" \
  "$REPO/inner-loop/scripts/opaque_identifier_gate.py" \
  "$REPO/dsfv_smoke.py" \
  "$REPO/prefill.py" \
  "$REPO/repaudit.py" \
  "/tmp/gb300-bench_dsf.py" \
  "$HOST:$REMOTE_ROOT/bin/"
ssh -o BatchMode=yes "$HOST" "mv '$REMOTE_ROOT/bin/gb300-bench_dsf.py' '$REMOTE_ROOT/bin/bench_dsf.py'; chmod 755 '$REMOTE_ROOT/bin/run_remote_iteration.sh' '$REMOTE_ROOT/bin/'*.py"

LOCAL_FILES=(
  "$REPO/inner-loop/scripts/run_remote_iteration.sh"
  "$REPO/inner-loop/scripts/replay_gate.py"
  "$REPO/inner-loop/scripts/opaque_identifier_gate.py"
  "$REPO/dsfv_smoke.py"
  "$REPO/prefill.py"
  "$REPO/repaudit.py"
  "/tmp/gb300-bench_dsf.py"
)
REMOTE_FILES=(
  "$REMOTE_ROOT/bin/run_remote_iteration.sh"
  "$REMOTE_ROOT/bin/replay_gate.py"
  "$REMOTE_ROOT/bin/opaque_identifier_gate.py"
  "$REMOTE_ROOT/bin/dsfv_smoke.py"
  "$REMOTE_ROOT/bin/prefill.py"
  "$REMOTE_ROOT/bin/repaudit.py"
  "$REMOTE_ROOT/bin/bench_dsf.py"
)
for index in "${!LOCAL_FILES[@]}"; do
  local_hash="$(shasum -a 256 "${LOCAL_FILES[$index]}" | cut -d' ' -f1)"
  remote_hash="$(ssh -o BatchMode=yes "$HOST" "sha256sum '${REMOTE_FILES[$index]}'" | cut -d' ' -f1)"
  if [[ "$local_hash" != "$remote_hash" ]]; then
    printf 'STAGE_HASH_FAIL local=%s remote=%s\n' "${LOCAL_FILES[$index]}" "${REMOTE_FILES[$index]}" >&2
    exit 70
  fi
done
printf 'STAGE_HASH_RESULT PASS files=%s\n' "${#LOCAL_FILES[@]}" | tee "$LOCAL_RUN/dispatch.txt"

set +e
ssh -o BatchMode=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=20 "$HOST" \
  "ROOT='$REMOTE_ROOT' '$REMOTE_ROOT/bin/run_remote_iteration.sh' '$RUN_ID' '$CONTAINER'"
REMOTE_RC=$?
set -e
printf 'REMOTE_RC=%s\n' "$REMOTE_RC" | tee -a "$LOCAL_RUN/dispatch.txt"

scp -q -r "$HOST:$REMOTE_ROOT/runs/$RUN_ID/." "$LOCAL_RUN/"
set +e
python3 "$REPO/inner-loop/scripts/summarize_iteration.py" "$LOCAL_RUN" | tee "$LOCAL_RUN/summary.txt"
SUMMARY_RC=${PIPESTATUS[0]}
set -e

python3 - "$REPO/inner-loop/ledger.jsonl" "$LOCAL_RUN/results.json" <<'PY'
import datetime, json, sys
ledger, result_path = sys.argv[1:]
result = json.load(open(result_path))
event = {
    "at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
    "event": "iteration_finished",
    "iteration": result.get("iteration"),
    "run_id": result_path.split("/")[-2],
    "slug": result.get("slug"),
    "outcome": result.get("outcome"),
    "blockers": result.get("blockers", []),
}
with open(ledger, "a") as handle:
    handle.write(json.dumps(event, separators=(",", ":")) + "\n")
PY

if [[ $REMOTE_RC -ne 0 || $SUMMARY_RC -ne 0 ]]; then
  exit 1
fi

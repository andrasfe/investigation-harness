#!/usr/bin/env bash
# Teacher-side live monitor — richer than teach.sh.
#
# Shows, on a 1-second loop: student PID status, last heartbeat, last 3
# escalations (id + kind + summary), pending escalations without a matching
# resolution, scorecard path if finalized.
#
# Usage:
#   scripts/watch.sh <run_dir>
#
# This is an interactive viewer; for the "Claude-Code-as-teacher" pattern
# use `teach.sh` (pure tail) and drive Read/Edit against the run files.

set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <run_dir>" >&2
  exit 2
fi

RUN_DIR="$1"
[[ -d "$RUN_DIR" ]] || { echo "not a dir: $RUN_DIR" >&2; exit 2; }

ESC="$RUN_DIR/escalations.jsonl"
RES="$RUN_DIR/resolutions.jsonl"
STATUS="$RUN_DIR/status.jsonl"
PIDFILE="$RUN_DIR/student.pid"

touch "$ESC" "$RES" "$STATUS"

_pending_escalations() {
  # Echo ids in escalations.jsonl that have no matching id in resolutions.jsonl.
  /usr/bin/python3 - "$ESC" "$RES" <<'PY'
import json, sys
esc_ids, esc_lines = [], []
with open(sys.argv[1]) as f:
    for ln in f:
        ln = ln.strip()
        if not ln: continue
        try:
            j = json.loads(ln)
            esc_ids.append(j.get("id"))
            esc_lines.append(j)
        except Exception:
            pass
res_ids = set()
try:
    with open(sys.argv[2]) as f:
        for ln in f:
            ln = ln.strip()
            if not ln: continue
            try:
                res_ids.add(json.loads(ln).get("id"))
            except Exception:
                pass
except FileNotFoundError:
    pass
for j in esc_lines:
    if j.get("id") not in res_ids:
        print(f"  PENDING  id={j.get('id','?')[:8]} kind={j.get('kind','?')} summary={(j.get('summary') or '')[:120]}")
PY
}

_fmt_last() {
  local path="$1" label="$2"
  local last=""
  if [[ -s "$path" ]]; then
    last="$(tail -n 1 "$path")"
  fi
  if [[ -n "$last" ]]; then
    /usr/bin/python3 - "$last" "$label" <<'PY'
import json, sys
try:
    j = json.loads(sys.argv[1])
    parts = [f"{k}={v}" for k, v in j.items() if k not in ("ts", "pid")]
    print(f"  {sys.argv[2]}: {' '.join(parts)[:200]}")
except Exception:
    print(f"  {sys.argv[2]}: <unparseable>")
PY
  else
    echo "  $label: (none yet)"
  fi
}

trap 'echo; echo "bye"; exit 0' INT

while :; do
  clear
  echo "scout watch — $RUN_DIR"
  echo "$(date '+%H:%M:%S')"
  echo
  if [[ -f "$PIDFILE" ]]; then
    PID="$(cat "$PIDFILE")"
    if kill -0 "$PID" 2>/dev/null; then
      echo "student PID $PID: RUNNING"
    else
      echo "student PID $PID: stopped"
    fi
  fi
  echo
  ESC_COUNT=$(wc -l < "$ESC" | tr -d ' ')
  RES_COUNT=$(wc -l < "$RES" | tr -d ' ')
  HB_COUNT=$(wc -l < "$STATUS" | tr -d ' ')
  echo "escalations: $ESC_COUNT  resolutions: $RES_COUNT  heartbeats: $HB_COUNT"
  echo
  _fmt_last "$STATUS" "last_heartbeat"
  _fmt_last "$ESC" "last_escalation"
  _fmt_last "$RES" "last_resolution"
  echo
  if [[ "$ESC_COUNT" -gt "$RES_COUNT" ]]; then
    echo "unanswered escalations:"
    _pending_escalations
  fi
  echo
  if [[ -f "$RUN_DIR/scorecard.json" ]]; then
    echo "scorecard: $RUN_DIR/scorecard.json"
  fi
  if [[ -f "$RUN_DIR/verifier_report.json" ]]; then
    ACC=$(/usr/bin/python3 -c "import json,sys; print(json.load(open('$RUN_DIR/verifier_report.json'))['accepted'])")
    echo "verifier: accepted=$ACC"
  fi
  echo
  echo "(Ctrl-C to exit)"
  sleep 2
done

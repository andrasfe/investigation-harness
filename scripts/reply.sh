#!/usr/bin/env bash
# Append a resolution line to a run's resolutions.jsonl.
#
# Usage:
#   scripts/reply.sh <run_dir> <escalation_id> <verdict> [notes...]
#
# verdicts: patch | skip | abort | restart | retry_with
#
# For structured fixes (patch dicts, save_rule, finding, save_fact) write the
# full JSON yourself; this helper only covers the common "skip with a note"
# and "abort" cases.

set -euo pipefail

if [[ $# -lt 3 ]]; then
  echo "usage: $0 <run_dir> <escalation_id> <verdict> [notes...]" >&2
  exit 2
fi

RUN_DIR="$1"; ID="$2"; VERDICT="$3"; shift 3
NOTES="${*:-}"

[[ -d "$RUN_DIR" ]] || { echo "not a dir: $RUN_DIR" >&2; exit 2; }
case "$VERDICT" in
  patch|skip|abort|restart|retry_with) ;;
  *) echo "invalid verdict: $VERDICT" >&2; exit 2;;
esac

/usr/bin/python3 - "$RUN_DIR/resolutions.jsonl" "$ID" "$VERDICT" "$NOTES" <<'PY'
import json, os, sys, time
path, id_, verdict, notes = sys.argv[1:5]
payload = {"id": id_, "verdict": verdict, "notes": notes, "ts": time.time()}
with open(path, "a", encoding="utf-8") as f:
    f.write(json.dumps(payload) + "\n")
    f.flush()
    os.fsync(f.fileno())
print(f"replied id={id_[:8]} verdict={verdict}")
PY

#!/usr/bin/env bash
# Run scout over every repo in initial-list.txt.
#
# Environment (see scout/config.py for the full list):
#   SCOUT_PARALLEL_REPOS=4        # evaluate up to 4 repos at once
#   SCOUT_SWARM_SIZE=1            # 1=single agent (default), >1=specialist swarm
#   SCOUT_DRY_RUN=1               # skip build/test/coverage (smoke test)
#   ESCALATE=1                    # opt-in to teacher escalation (pair with teach.sh)
#
# Usage:
#   scripts/run_batch.sh                         # default: initial-list.txt
#   scripts/run_batch.sh --limit 3               # just the first 3
#   scripts/run_batch.sh --parallel 4 --limit 10
#   scripts/run_batch.sh --push                  # after the batch, commit+push
#
# --push passes through everything else to `python -m scout.main batch`
# and then invokes scripts/push-round.sh against the batch directory.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

DO_PUSH=0
PASS=()
for a in "$@"; do
  case "$a" in
    --push) DO_PUSH=1 ;;
    *) PASS+=("$a") ;;
  esac
done

STAMP="$(date +%Y%m%d-%H%M%S)"
BATCH_ID="batch-$STAMP"
BATCH_DIR="$HERE/runs/$BATCH_ID"

/usr/bin/python3 -m scout.main batch initial-list.txt \
  --batch-id "$BATCH_ID" --auto-verify "${PASS[@]}"

if [[ "$DO_PUSH" == "1" ]]; then
  bash "$HERE/scripts/push-round.sh" "$BATCH_DIR" "scout: round $BATCH_ID"
fi

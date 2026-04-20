#!/usr/bin/env bash
# Launch a scout student in the background with the teacher channel active.
#
# This is the "launch" half of the teacher-student loop — once it returns,
# pair it with scripts/teach.sh (or scripts/watch.sh) in the teacher session.
#
# Usage:
#   scripts/launch.sh evaluate <repo_url>
#   scripts/launch.sh batch <list_path> [--limit N --parallel K]
#
# Env overrides (all optional):
#   SCOUT_DRY_RUN=1         # skip mvn/gradle (REQUIRED if they're not installed)
#   SCOUT_PARALLEL_REPOS=1  # parallel repo evals for 'batch'
#   SCOUT_SWARM_SIZE=1      # 1 = single agent, >=2 = specialist swarm
#   NO_ESCALATE=1           # disable ESCALATE — student runs autonomously
#
# On success: prints RUN_DIR (for teach.sh / watch.sh) and STUDENT_PID.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

if [[ $# -lt 2 ]]; then
  echo "usage: $0 {evaluate|batch} <arg> [extra args]" >&2
  exit 2
fi

MODE="$1"; shift
ARG="$1"; shift

STAMP="$(date +%Y%m%d-%H%M%S)"
case "$MODE" in
  evaluate)
    SLUG="$(basename "${ARG%.git}")"
    RUN_DIR="${HERE}/runs/scout-${STAMP}-${SLUG}"
    mkdir -p "$RUN_DIR"
    EXTRA=("--evaluation-id" "scout-${STAMP}-${SLUG}")
    ;;
  batch)
    RUN_DIR="${HERE}/runs/batch-${STAMP}"
    mkdir -p "$RUN_DIR"
    EXTRA=("--batch-id" "batch-${STAMP}")
    ;;
  *)
    echo "unknown mode: $MODE" >&2
    exit 2
    ;;
esac

touch "$RUN_DIR/escalations.jsonl" "$RUN_DIR/resolutions.jsonl" "$RUN_DIR/status.jsonl"

LOG="$RUN_DIR/student.stdout.log"

# Two-gate activation per teacher-student-loop protocol.
if [[ "${NO_ESCALATE:-}" == "1" ]]; then
  ESCALATE_ENV=""
else
  ESCALATE_ENV="ESCALATE=1"
fi

cat <<EOF
launching scout student:
  mode:    $MODE
  arg:     $ARG
  run_dir: $RUN_DIR
  log:     $LOG
  gates:   SUPERVISOR_DIR=$RUN_DIR ${ESCALATE_ENV:-<disabled>}
EOF

# Kick it off. nohup so the caller can exit; writes pid to run_dir for later kill.
(
  export SUPERVISOR_DIR="$RUN_DIR"
  if [[ -n "$ESCALATE_ENV" ]]; then
    export ESCALATE=1
  fi
  nohup /usr/bin/python3 -m scout.main "$MODE" "$ARG" "${EXTRA[@]}" "$@" \
        > "$LOG" 2>&1 &
  echo $! > "$RUN_DIR/student.pid"
)

PID="$(cat "$RUN_DIR/student.pid")"
echo
echo "student PID: $PID"
echo "teacher side:"
echo "  scripts/watch.sh $RUN_DIR     # live status + escalation view"
echo "  scripts/teach.sh $RUN_DIR     # raw tail -F of escalations.jsonl"
echo
echo "when an escalation appears, reply by appending to:"
echo "  $RUN_DIR/resolutions.jsonl"
echo "  or: scripts/reply.sh $RUN_DIR <id> <verdict> [notes]"

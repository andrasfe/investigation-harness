#!/usr/bin/env bash
# Teacher-side helper for scout — watches every escalations.jsonl under a
# batch directory so one teacher session covers the parallel repo fleet.
#
# Usage:
#   scripts/teach.sh                     # tails runs/ recursively
#   scripts/teach.sh runs/batch-123      # scope to a specific batch
#
# Reply by appending a JSON line to the matching <run_dir>/resolutions.jsonl:
#   {"id":"<id>","verdict":"patch","fix":{...},"notes":"..."}
#
# Verdicts: patch | skip | abort | restart
# See ~/.claude/skills/teacher-student-loop/references/protocol.md for the
# full message shape.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="${1:-$HERE/runs}"

if [[ ! -d "$ROOT" ]]; then
  echo "error: $ROOT is not a directory" >&2
  exit 2
fi

mapfile -t FILES < <(find "$ROOT" -type f -name escalations.jsonl 2>/dev/null)

if [[ ${#FILES[@]} -eq 0 ]]; then
  echo "No escalations.jsonl files under $ROOT yet — waiting for the batch to start."
  echo "Polling every 3s. Ctrl-C to exit."
  while [[ ${#FILES[@]} -eq 0 ]]; do
    sleep 3
    mapfile -t FILES < <(find "$ROOT" -type f -name escalations.jsonl 2>/dev/null)
  done
fi

echo "teach.sh watching:"
for f in "${FILES[@]}"; do
  echo "  $f"
done
echo
echo "reply by appending to the corresponding resolutions.jsonl next to each file."
echo "verdicts: patch | skip | abort | restart"
echo

exec tail -F -n 0 "${FILES[@]}"

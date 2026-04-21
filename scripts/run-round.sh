#!/usr/bin/env bash
# Run one round: evaluate JSqlParser dry-run, copy artifacts into docs/memos/
# with round-tagged filenames so docs/trajectory.md auto-updates.
#
# Usage:
#   scripts/run-round.sh 3           # next round number
#   scripts/run-round.sh 3 --adversarial
#
# Does NOT commit/tag/push — caller does that after applying student edits.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

ROUND="${1:?round number required}"
shift
ADV_ARG=""
for a in "$@"; do
  [[ "$a" == "--adversarial" ]] && ADV_ARG="--adversarial"
done

STAMP="$(date +%Y%m%d-%H%M%S)"
SLUG="JSqlParser"
EVAL_ID="round${ROUND}-${STAMP}-${SLUG}"
RUN_DIR="runs/${EVAL_ID}"

echo "== round ${ROUND} eval (dry-run, no escalate, ${ADV_ARG:-no-adversarial}) =="
SCOUT_DRY_RUN=1 /usr/bin/python3 -m scout.main evaluate \
  https://github.com/JSQLParser/JSqlParser \
  --evaluation-id "${EVAL_ID}" \
  ${ADV_ARG} 2>&1 | tail -5 || true

mkdir -p docs/memos
for f in scorecard.json verifier_report.json challenge.json viability_challenge.json evidence.json; do
  src="${RUN_DIR}/${f}"
  if [[ -f "$src" ]]; then
    # Filename format expected by distillation-trajectory.sh:
    #   round<N>-*-{scorecard,verifier_report,viability_challenge,challenge}.json
    dst="docs/memos/round${ROUND}-${STAMP}-$(basename "$f" .json).json"
    cp "$src" "$dst"
  fi
done

echo "-- artifacts copied:"
ls docs/memos/round${ROUND}-${STAMP}-* 2>/dev/null | sed 's/^/  /'
echo "-- verifier verdict:"
/usr/bin/python3 -c "
import json, sys
try:
    r = json.load(open('${RUN_DIR}/verifier_report.json'))
except Exception:
    print('  (no verifier_report)')
    sys.exit(0)
print('  accepted:', r['accepted'])
for l in r['layers']:
    mark = 'OK  ' if l['ok'] else 'FAIL'
    print(f'  [{mark}] {l[\"name\"]}')
    for i in l.get('issues') or []:
        print(f'       - {i[:100]}')
"
echo "-- scorecard key fields:"
/usr/bin/python3 -c "
import json
sc = json.load(open('${RUN_DIR}/scorecard.json'))
print(f'  build_system: {sc[\"build\"][\"build_system\"]}  clean_build_succeeded: {sc[\"build\"][\"clean_build_succeeded\"]}')
print(f'  test_count: {sc[\"tests\"][\"test_count\"]}  test_run_succeeded: {sc[\"tests\"][\"test_run_succeeded\"]}')
print(f'  bug_fix_commits_24mo: {sc[\"bug_history\"][\"bug_fix_commits_24mo\"]}')
print(f'  viable_target: {sc[\"recommendation\"][\"viable_target\"]}')
print(f'  viability_evidence count: {len(sc[\"recommendation\"][\"viability_evidence\"])}')
"
echo "RUN_DIR=${RUN_DIR}"

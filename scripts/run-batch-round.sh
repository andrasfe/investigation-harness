#!/usr/bin/env bash
# Run one round across a small curated list of repos (default: Tier-1).
# Copies each scorecard/verifier/challenge/viability_challenge into
# docs/memos/round<N>-* so docs/trajectory.md auto-updates.
#
# Usage:
#   scripts/run-batch-round.sh 6                 # default 5-repo Tier-1 subset
#   scripts/run-batch-round.sh 7 --adversarial
#
# Repos selected for diversity within Tier-1:
#   JSqlParser (small, Maven, clean)
#   Commons Compress (Apache, multi-module, Maven)
#   Commons Imaging (Apache, known-complex)
#   JGraphT (gradle)
#   jsoup (small, Maven, popular)

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

ROUND="${1:?round number required}"
shift || true
ADV_ARG=""
for a in "$@"; do
  [[ "$a" == "--adversarial" ]] && ADV_ARG="--adversarial"
done

STAMP="$(date +%Y%m%d-%H%M%S)"
REPOS=(
  "https://github.com/JSQLParser/JSqlParser"
  "https://github.com/apache/commons-compress"
  "https://github.com/apache/commons-imaging"
  "https://github.com/jgrapht/jgrapht"
  "https://github.com/jhy/jsoup"
)

mkdir -p docs/memos
SUMMARY="runs/round${ROUND}-${STAMP}-summary.json"
mkdir -p "runs/round${ROUND}-${STAMP}"

# Execution mode: dry-run by default, real docker-backed builds when
# SCOUT_USE_DOCKER=1 is exported. Real mode bumps tool-call + time budgets
# because mvn test can legitimately take a minute per repo.
if [[ "${SCOUT_USE_DOCKER:-0}" =~ ^(1|true|yes|on)$ ]]; then
  MODE_LABEL="docker-real"
  # Ensure dry-run isn't forced on; let child inherit.
  unset SCOUT_DRY_RUN || true
  # Real builds need more tool calls (clone + build + tests + coverage + bug + gh +
  # static + finalize can easily exceed 60) and more wall time.
  export SCOUT_MAX_TOOL_CALLS="${SCOUT_MAX_TOOL_CALLS:-100}"
  export SCOUT_TIME_BUDGET_SEC="${SCOUT_TIME_BUDGET_SEC:-3600}"
else
  MODE_LABEL="dry-run"
fi

echo "== round ${ROUND} batch (${MODE_LABEL}, ${#REPOS[@]} repos, ${ADV_ARG:-no-adversarial}) =="

RESULTS=()
for url in "${REPOS[@]}"; do
  slug="$(basename "${url%.git}" | tr -c 'A-Za-z0-9-' '-')"
  eid="round${ROUND}-${STAMP}-${slug}"
  run_dir="runs/${eid}"
  echo "-- evaluating ${slug} (${MODE_LABEL}) --"
  if [[ "$MODE_LABEL" == "docker-real" ]]; then
    /usr/bin/python3 -m scout.main evaluate \
        "$url" --evaluation-id "$eid" ${ADV_ARG} 2>&1 | tail -5 || true
  else
    SCOUT_DRY_RUN=1 /usr/bin/python3 -m scout.main evaluate \
        "$url" --evaluation-id "$eid" ${ADV_ARG} 2>&1 | tail -3 || true
  fi

  # Copy artifacts with round-tagged filenames.
  for f in scorecard.json verifier_report.json challenge.json viability_challenge.json evidence.json; do
    [[ -f "${run_dir}/${f}" ]] && cp "${run_dir}/${f}" "docs/memos/round${ROUND}-${STAMP}-${slug}-$(basename "$f" .json).json"
  done

  # Summary line
  /usr/bin/python3 - "${run_dir}" "${slug}" <<'PY'
import json, os, sys
run_dir, slug = sys.argv[1:3]
sc_p = os.path.join(run_dir, "scorecard.json")
vr_p = os.path.join(run_dir, "verifier_report.json")
if not os.path.exists(sc_p):
    print(f"  {slug}: NO SCORECARD")
    sys.exit(0)
sc = json.load(open(sc_p))
acc = "?"
if os.path.exists(vr_p):
    try: acc = str(json.load(open(vr_p))["accepted"])
    except Exception: pass
print(f"  {slug}: verifier={acc} build={sc['build']['build_system']} bugs={sc['bug_history']['bug_fix_commits_24mo']} composite={sc['score']['composite']} viable={sc['recommendation']['viable_target']}")
PY
done

# Aggregate batch summary.
/usr/bin/python3 - "${ROUND}" "${STAMP}" "${SUMMARY}" <<'PY'
import json, glob, os, sys
round_num, stamp, out_path = sys.argv[1:4]
rows = []
for sc_p in glob.glob(f"docs/memos/round{round_num}-{stamp}-*-scorecard.json"):
    sc = json.load(open(sc_p))
    vr_p = sc_p.replace("-scorecard.json", "-verifier_report.json")
    accepted = None
    if os.path.exists(vr_p):
        try: accepted = json.load(open(vr_p))["accepted"]
        except Exception: pass
    rows.append({
        "slug": os.path.basename(sc_p).split("-", 3)[-1].rsplit("-", 1)[0],
        "repo_url": sc["repo_url"],
        "verifier_accepted": accepted,
        "build_system": sc["build"]["build_system"],
        "clean_build_succeeded": sc["build"]["clean_build_succeeded"],
        "bug_fix_commits_24mo": sc["bug_history"]["bug_fix_commits_24mo"],
        "composite": sc["score"]["composite"],
        "viable_target": sc["recommendation"]["viable_target"],
        "autofilled": os.path.exists(sc_p.replace("-scorecard.json", "-autofilled_fields.json")),
    })
rows.sort(key=lambda r: -(r["composite"] or 0))
summary = {
    "round": int(round_num),
    "stamp": stamp,
    "rows": rows,
    "accepted_count": sum(1 for r in rows if r["verifier_accepted"]),
    "total": len(rows),
}
json.dump(summary, open(out_path, "w"), indent=2)
print()
print(f"== batch summary ({summary['accepted_count']}/{summary['total']} accepted) ==")
for r in rows:
    mark = "✓" if r["verifier_accepted"] else "✗"
    print(f"  {mark} {r['slug']:<25} composite={r['composite']:>5.2f}  viable={r['viable_target']}")
print(f"written: {out_path}")
PY

#!/usr/bin/env bash
# Generate docs/round-report-<N>.md — the packet a Claude-Code-as-teacher
# session reads between rounds to decide what student-code edits (if any)
# to apply within the modification envelope.
#
# What it aggregates (scoped to "since the last `round-N` git tag"):
#   - git log + diffstat since the last round tag
#   - every verifier_report.json produced in this round
#   - every viability_challenge.json (V2.5 rulings)
#   - every teacher_findings.jsonl (across runs)
#   - beads open/closed counts and headline issues
#   - escalation rate + verifier-reject reasons (rolled up by category)
#
# Output: docs/round-report-<N>.md (where N is the next unused round number).
# The teacher reads this, decides what to change in prompts.py /
# project_handlers.jsonl / etc. (envelope-bound), runs envelope-check.sh,
# commits + pushes + tags as round-N.
#
# Usage:
#   scripts/between-rounds-report.sh                # auto-number from existing tags
#   scripts/between-rounds-report.sh 2              # force round number

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

# Determine round number.
if [[ $# -ge 1 ]]; then
  NUM="$1"
else
  LAST_TAG="$(git tag --list 'round-*' --sort=-v:refname | head -1)"
  if [[ -z "$LAST_TAG" ]]; then
    NUM=1
  else
    NUM="$(( ${LAST_TAG#round-} + 1 ))"
  fi
fi
LAST_ROUND_TAG="$(git tag --list 'round-*' --sort=-v:refname | head -1)"
if [[ -z "$LAST_ROUND_TAG" ]]; then
  SINCE_REF=""
else
  SINCE_REF="$LAST_ROUND_TAG..HEAD"
fi

OUT="docs/round-report-${NUM}.md"
mkdir -p docs

{
  echo "# Round ${NUM} report"
  echo ""
  echo "_Generated: $(date -u '+%Y-%m-%dT%H:%M:%SZ')_"
  if [[ -n "$LAST_ROUND_TAG" ]]; then
    echo ""
    echo "Scope: commits \`${LAST_ROUND_TAG}..HEAD\`"
  else
    echo ""
    echo "Scope: entire history (no prior round tag)"
  fi
  echo ""

  # --- git history -------------------------------------------------------
  echo "## Git activity this round"
  echo ""
  if [[ -n "$SINCE_REF" ]]; then
    echo "### Commits"
    echo ""
    echo '```'
    git log --oneline "$SINCE_REF"
    echo '```'
    echo ""
    echo "### Diffstat"
    echo ""
    echo '```'
    git diff --stat "$SINCE_REF"
    echo '```'
    echo ""
  else
    echo "### Commits (all history)"
    echo ""
    echo '```'
    git log --oneline | head -40
    echo '```'
    echo ""
  fi

  # --- run artifacts ------------------------------------------------------
  echo "## Run artifacts"
  echo ""
  SCORECARDS=()
  while IFS= read -r _p; do SCORECARDS+=("$_p"); done < <(find docs/memos -name 'round*-scorecard.json' -o -name 'memo-*.md' 2>/dev/null | sort)
  if [[ ${#SCORECARDS[@]} -eq 0 ]]; then
    echo "_(no docs/memos artifacts yet)_"
  else
    for s in "${SCORECARDS[@]}"; do
      echo "- $s"
    done
  fi
  echo ""

  # --- verifier roll-up ---------------------------------------------------
  echo "## Verifier & adversarial roll-up"
  echo ""
  /usr/bin/python3 - <<'PY'
import glob, json, collections

reject_reasons = collections.Counter()
adv_refutations = collections.Counter()
accepted = 0
total = 0
for path in sorted(glob.glob("docs/memos/*verifier_report.json")):
    try:
        r = json.load(open(path))
    except Exception:
        continue
    total += 1
    if r.get("accepted"):
        accepted += 1
    for layer in r.get("layers") or []:
        if not layer.get("ok"):
            for issue in layer.get("issues") or []:
                reject_reasons[f"{layer['name']}: {issue[:80]}"] += 1

for path in sorted(glob.glob("docs/memos/*viability_challenge.json")):
    try:
        r = json.load(open(path))
    except Exception:
        continue
    for ruling in r.get("rulings") or []:
        if ruling.get("verdict") == "refuted":
            adv_refutations[str(ruling.get("field_path"))] += 1

print(f"- total reports archived: {total}")
print(f"- accepted: {accepted}")
print(f"- rejection reasons (top 10):")
for k, v in reject_reasons.most_common(10):
    print(f"  - ×{v}  {k}")
print(f"- adversarial refutations by field (top 10):")
for k, v in adv_refutations.most_common(10):
    print(f"  - ×{v}  {k}")
PY
  echo ""

  # --- teacher durable knowledge across runs -----------------------------
  echo "## Teacher findings accumulated"
  echo ""
  /usr/bin/python3 - <<'PY'
import glob, json
findings = []
for path in glob.glob("runs/**/teacher_findings.jsonl", recursive=True):
    for ln in open(path):
        ln = ln.strip()
        if not ln: continue
        try:
            findings.append(json.loads(ln))
        except json.JSONDecodeError:
            pass
if not findings:
    print("_(no teacher_findings.jsonl entries yet)_")
else:
    by_sev = {}
    for f in findings:
        by_sev.setdefault(f.get("severity","med"), []).append(f)
    for sev in ("high", "med", "medium", "low"):
        for f in by_sev.get(sev, []):
            t = f.get("title","(untitled)")
            files = f.get("suggested_files") or []
            print(f"- **[{sev}]** {t}  (suggested_files: {', '.join(files) or '(none)'})")
            notes = (f.get("notes") or "").strip()
            if notes:
                for ln in notes.splitlines()[:3]:
                    print(f"    {ln}")
PY
  echo ""

  # --- beads snapshot ----------------------------------------------------
  echo "## Beads backlog snapshot"
  echo ""
  if command -v bd >/dev/null 2>&1; then
    echo '```'
    bd stats 2>&1 | sed -n '/Summary:/,/For more details/p'
    echo '```'
    echo ""
    echo "### Open P1 items"
    echo '```'
    bd list --status=open 2>&1 | grep -E '\[● P1\]' | head -10
    echo '```'
  else
    echo "_(bd not on PATH)_"
  fi
  echo ""

  # --- between-rounds teacher checklist ----------------------------------
  echo "## Teacher action checklist"
  echo ""
  echo "1. Read the sections above. Look for **recurring** verifier rejection reasons"
  echo "   or adversarial refutations — these are candidates for student-code edits."
  echo "2. Decide on the edit:"
  echo "   - **Prompt addition** (\`scout/prompts.py\`) — teach the student a new"
  echo "     rule it keeps violating."
  echo "   - **Project-specific handler** (\`state/project_handlers.jsonl\`) —"
  echo "     per-org rules like \`apache/commons-*\` coverage paths."
  echo "   - **Threshold tune** (\`scout/config.py\` defaults, allowed keys only)."
  echo "3. Stage the changes: \`git add ...\`"
  echo "4. Validate: \`bash scripts/envelope-check.sh\`  # must exit 0"
  echo "5. Commit: \`git commit -m \"scout: round ${NUM} student update — <summary>\"\`"
  echo "6. Run canaries (bead \`ih2\`; until then, run \`scout evaluate --runs 3\`"
  echo "   on a canary repo to check stability)."
  echo "7. Tag: \`bash scripts/tag-round.sh ${NUM}\`"
  echo "8. Push: \`git push origin main --tags\`"
  echo ""
  echo "If any of the findings above point to a change that is OUT of the"
  echo "envelope (new tool, schema change, verifier change) — DO NOT auto-apply."
  echo "Escalate to the operator and open a beads issue."

} > "$OUT"

echo "$OUT"

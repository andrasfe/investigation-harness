#!/usr/bin/env bash
# Commit and push a round's artifacts to origin.
#
# What counts as a "round":
#   - one-shot evaluate   → a single repo's run
#   - batch               → a full batch_dir with its memo
#
# Usage:
#   scripts/push-round.sh <run_or_batch_dir> [commit-subject]
#
# Side effects (idempotent; safe to re-run):
#   - beads sync to JSONL (`bd sync --flush-only`)
#   - stage: README.md, CLAUDE.md, scout/, scripts/, .beads/*.jsonl,
#            canaries/, selection_memo if present next to the dir arg.
#   - DO NOT stage runs/ (gitignored) — per-repo scorecards are ephemeral
#     workspace.  If you want the selection memo committed, copy it into
#     docs/memos/ first.
#
# The remote is assumed to be `origin`; the initial push uses -u.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <run_or_batch_dir> [commit-subject]" >&2
  exit 2
fi

TARGET="$1"; shift || true
SUBJECT_OVERRIDE="${1:-}"

if [[ ! -d "$TARGET" ]]; then
  echo "error: $TARGET is not a directory" >&2
  exit 2
fi

# 1. Flush beads so the JSONL export reflects latest state.
if command -v bd >/dev/null 2>&1; then
  bd sync --flush-only >/dev/null 2>&1 || true
fi

# 2. If a selection memo exists, copy it into docs/memos/<stamp>.md so it
#    survives even though runs/ is gitignored.
mkdir -p docs/memos
MEMO_SRC="$TARGET/selection_memo.md"
MEMO_COPY=""
if [[ -f "$MEMO_SRC" ]]; then
  STAMP="$(date +%Y%m%d-%H%M%S)"
  MEMO_COPY="docs/memos/memo-$STAMP.md"
  cp "$MEMO_SRC" "$MEMO_COPY"
fi

# 3. Also keep round-level summary for traceability, same destination.
SUMMARY_SRC="$TARGET/batch_summary.json"
SUMMARY_COPY=""
if [[ -f "$SUMMARY_SRC" ]]; then
  STAMP="$(date +%Y%m%d-%H%M%S)"
  SUMMARY_COPY="docs/memos/summary-$STAMP.json"
  cp "$SUMMARY_SRC" "$SUMMARY_COPY"
fi

# 4. Append one entry to docs/rounds.md — a human-readable round log.
ROUND_LOG="docs/rounds.md"
if [[ ! -f "$ROUND_LOG" ]]; then
  echo "# Scout round log" > "$ROUND_LOG"
  echo "" >> "$ROUND_LOG"
fi
{
  echo "## $(date -u '+%Y-%m-%dT%H:%M:%SZ') — $(basename "$TARGET")"
  echo ""
  [[ -n "$MEMO_COPY" ]]    && echo "- memo: $MEMO_COPY"
  [[ -n "$SUMMARY_COPY" ]] && echo "- summary: $SUMMARY_COPY"
  if [[ -f "$TARGET/scorecard.json" ]]; then
    /usr/bin/python3 - "$TARGET/scorecard.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
acc = "n/a"
vp = sys.argv[1].replace("scorecard.json","verifier_report.json")
try:
    acc = str(json.load(open(vp)).get("accepted"))
except Exception:
    pass
sc = d.get("score", {})
print(f"- repo: {d.get('repo_url','?')}")
print(f"- composite: {sc.get('composite',0)}")
print(f"- verifier_accepted: {acc}")
print(f"- escalations: {(d.get('metadata') or {}).get('escalation_count', 0)}")
PY
  fi
  echo ""
} >> "$ROUND_LOG"

# 5. Stage + commit.
git add -A README.md CLAUDE.md SPEC.md pyproject.toml requirements.txt \
        scout/ scripts/ canaries/ docs/ .gitignore initial-list.txt \
        .beads/*.jsonl 2>/dev/null || true

# If nothing is staged, stop — no empty commits.
if git diff --cached --quiet 2>/dev/null; then
  echo "push-round: nothing to commit"
else
  SUBJECT="${SUBJECT_OVERRIDE:-scout: round $(basename "$TARGET")}"
  git commit -m "$(cat <<EOF
$SUBJECT

Round artifacts:
- target: $TARGET
- memo: ${MEMO_COPY:-<none>}
- summary: ${SUMMARY_COPY:-<none>}

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
fi

# 6. Push. -u on first push of a branch so subsequent pushes are argumentless.
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if git remote get-url origin >/dev/null 2>&1; then
  if git rev-parse --verify --quiet "origin/$BRANCH" >/dev/null; then
    git push origin "$BRANCH"
  else
    git push -u origin "$BRANCH"
  fi
  echo "push-round: pushed to origin/$BRANCH"
else
  echo "push-round: no 'origin' remote configured — skipping push" >&2
fi

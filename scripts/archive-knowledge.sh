#!/usr/bin/env bash
# Aggregate durable teacher knowledge from recent runs into state/knowledge/.
#
# What this does:
#   1. Walk runs/*/teacher_*.jsonl — collect rules, facts, findings
#   2. Deduplicate against state/knowledge/*.jsonl (content-hash per line)
#   3. Append new entries to state/knowledge/teacher_*.jsonl
#   4. Also archive the raw per-run stores under state/knowledge/rounds/<tag>/
#
# Called by scripts/push-round.sh at commit time. Safe to run manually
# (idempotent: re-running on the same runs adds no new entries).
#
# Usage:
#   scripts/archive-knowledge.sh                 # scan runs/, tag current round
#   scripts/archive-knowledge.sh --round 2       # explicit tag for the archive
#   scripts/archive-knowledge.sh --dry-run       # report what would be added

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

ROUND_TAG=""
DRY_RUN=0
for a in "$@"; do
  case "$a" in
    --dry-run) DRY_RUN=1 ;;
    --round)   shift; ROUND_TAG="$1" ;;
    *)         ;;
  esac
done

if [[ -z "$ROUND_TAG" ]]; then
  LAST="$(git tag --list 'round-*' --sort=-v:refname 2>/dev/null | head -1 || true)"
  if [[ -z "$LAST" ]]; then
    ROUND_TAG="round-0"
  else
    ROUND_TAG="$LAST"
  fi
fi

mkdir -p state/knowledge
ARCHIVE_DIR="state/knowledge/rounds/${ROUND_TAG}"
mkdir -p "$ARCHIVE_DIR"

/usr/bin/python3 - "$ROUND_TAG" "$DRY_RUN" <<'PY'
import glob, hashlib, json, os, shutil, sys
from pathlib import Path

round_tag = sys.argv[1]
dry_run = sys.argv[2] == "1"

state_dir = Path("state/knowledge")
arch_dir = state_dir / "rounds" / round_tag
arch_dir.mkdir(parents=True, exist_ok=True)

NAMES = ("teacher_rules.jsonl", "teacher_facts.jsonl", "teacher_findings.jsonl")

def _read_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]

def _hash(s: str) -> str:
    # Normalise whitespace + volatile `ts` field so "same knowledge written twice" dedups.
    try:
        j = json.loads(s)
        j.pop("ts", None)
        j.pop("issued_by", None)
        return hashlib.sha256(json.dumps(j, sort_keys=True).encode()).hexdigest()[:16]
    except Exception:
        return hashlib.sha256(s.encode()).hexdigest()[:16]

summary = {"round_tag": round_tag, "dry_run": dry_run, "by_file": {}}

for name in NAMES:
    aggregate_path = state_dir / name
    existing = _read_lines(aggregate_path)
    existing_hashes = {_hash(l) for l in existing}

    new_lines: list[str] = []
    archived_raw: list[str] = []
    for run_path in sorted(glob.glob(f"runs/**/{name}", recursive=True)):
        # Snapshot per-run raw files for audit trail.
        rel = Path(run_path).parent.name
        dst = arch_dir / f"{rel}.{name}"
        if not dry_run:
            try:
                shutil.copyfile(run_path, dst)
            except OSError:
                pass
        for line in _read_lines(Path(run_path)):
            h = _hash(line)
            if h in existing_hashes:
                continue
            existing_hashes.add(h)
            new_lines.append(line)
            archived_raw.append(f"[{rel}] {line[:120]}")

    summary["by_file"][name] = {
        "existing_aggregate": len(existing),
        "new_added": len(new_lines),
        "archived_samples": archived_raw[:5],
    }

    if not dry_run and new_lines:
        with open(aggregate_path, "a", encoding="utf-8") as f:
            for line in new_lines:
                f.write(line + "\n")

# Write a small manifest so a teacher can see what landed.
if not dry_run:
    (arch_dir / "manifest.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )

print(json.dumps(summary, indent=2, default=str))
PY

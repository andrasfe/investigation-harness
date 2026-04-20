#!/usr/bin/env bash
# Validate a staged (or arbitrary) git diff against state/envelope.json.
#
# Exit codes:
#   0 — all changed paths are within the permitted envelope
#   2 — at least one path is prohibited or security-sensitive
#   3 — diff contains paths not listed anywhere (requires explicit human review)
#
# Usage:
#   scripts/envelope-check.sh                     # checks currently staged diff
#   scripts/envelope-check.sh --ref HEAD~1..HEAD  # check a specific ref range
#   scripts/envelope-check.sh --explain           # print the envelope and exit

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

ENVELOPE="state/envelope.json"
[[ -f "$ENVELOPE" ]] || { echo "error: $ENVELOPE not found" >&2; exit 2; }

if [[ "${1:-}" == "--explain" ]]; then
  /usr/bin/python3 -m json.tool "$ENVELOPE"
  exit 0
fi

REF_RANGE=""
if [[ "${1:-}" == "--ref" && -n "${2:-}" ]]; then
  REF_RANGE="$2"
fi

# Collect changed paths. `mapfile -t` is bash 4+; use while-read for bash 3.2 (macOS).
CHANGED=()
if [[ -n "$REF_RANGE" ]]; then
  while IFS= read -r line; do CHANGED+=("$line"); done < <(git diff --name-only "$REF_RANGE")
else
  while IFS= read -r line; do CHANGED+=("$line"); done < <(git diff --cached --name-only)
fi
if [[ ${#CHANGED[@]} -eq 0 ]]; then
  echo "envelope-check: no staged changes"
  exit 0
fi

# Run the Python validator.
/usr/bin/python3 - "$ENVELOPE" "${CHANGED[@]}" <<'PY'
import json, os, sys

envelope_path = sys.argv[1]
changed = sys.argv[2:]
env = json.load(open(envelope_path))

permitted = env.get("permitted", {}) or {}
prohibited = env.get("prohibited", {}) or {}
security  = env.get("security_sensitive", {}) or {}

def _match_any(path, prefixes):
    for p in prefixes:
        if not p: continue
        if path == p:
            return True
        # trailing slash → directory prefix
        if p.endswith("/") and path.startswith(p):
            return True
        # bare file path
        if path == p:
            return True
    return False

permitted_paths = permitted.get("paths") or []
prohibited_paths = prohibited.get("paths") or []
security_paths = security.get("paths") or []

verdicts = {}
bad = []
unknown = []

for path in changed:
    in_security = _match_any(path, security_paths)
    in_prohibited = _match_any(path, prohibited_paths)
    in_permitted = _match_any(path, permitted_paths)

    if in_security:
        verdicts[path] = ("security_sensitive", "requires explicit human approval (security-relevant)")
        bad.append(path)
    elif in_prohibited:
        reason = (prohibited.get("reason_by_path", {}) or {}).get(path, "listed as prohibited")
        verdicts[path] = ("prohibited", reason)
        bad.append(path)
    elif in_permitted:
        verdicts[path] = ("permitted", "")
    else:
        verdicts[path] = ("unknown", "not listed in envelope.json — requires explicit decision")
        unknown.append(path)

# Line-count soft limits.
file_limits = permitted.get("file_limits") or {}
soft_lines = int(file_limits.get("max_changed_lines_per_file", 120))
hard_lines = 3 * soft_lines
soft_files = int(file_limits.get("max_files_per_commit", 8))

# Gather line counts from git diff --numstat
numstat = {}
# If running from pre-commit, diff --cached; else no-op.
try:
    import subprocess
    out = subprocess.run(
        ["git", "diff", "--cached", "--numstat"],
        capture_output=True, text=True, check=False,
    ).stdout
    for ln in out.splitlines():
        parts = ln.split("\t")
        if len(parts) != 3: continue
        add_s, del_s, path = parts
        try:
            numstat[path] = (int(add_s), int(del_s))
        except ValueError:
            pass
except Exception:
    pass

# Report.
print(f"envelope-check: examining {len(changed)} path(s)")
for path, (v, reason) in verdicts.items():
    add, rem = numstat.get(path, (0, 0))
    line_note = f" (+{add}/-{rem})" if (add or rem) else ""
    print(f"  [{v}] {path}{line_note}")
    if reason:
        print(f"    reason: {reason}")
    if v == "permitted" and add + rem > hard_lines:
        print(f"    HARD-LIMIT: {add+rem} lines changed > 3×{soft_lines} — flagged for review")
        bad.append(path)
    elif v == "permitted" and add + rem > soft_lines:
        print(f"    soft-limit warning: {add+rem} > {soft_lines} lines")

if len(changed) > soft_files:
    print(f"  WARNING: {len(changed)} files changed in one commit (soft cap: {soft_files})")

if bad:
    print()
    print("OUT-OF-ENVELOPE changes detected:")
    for p in sorted(set(bad)):
        print(f"  - {p} ({verdicts[p][0]})")
    print("These require explicit human approval. If this is intentional:")
    print("  - update state/envelope.json in a separately-reviewed commit, OR")
    print("  - if you are the operator, pass --force on the commit (future hook flag).")
    sys.exit(2)

if unknown:
    print()
    print("UNKNOWN paths (not listed anywhere in envelope.json):")
    for p in unknown:
        print(f"  - {p}")
    print("Decide: extend envelope.permitted.paths (between-round edit) or")
    print("extend envelope.prohibited.paths (protected area). Then re-run.")
    sys.exit(3)

print()
print("envelope-check: OK — all changes within permitted envelope.")
PY

#!/usr/bin/env bash
# Pilot runner STUB — SPEC §9.4, bead `uc6`.
#
# THE PURPOSE: before scout ships a selection memo, the top-K candidates
# undergo a pilot run where a general-purpose Claude Code agent (NOT scout,
# NOT TestWright) attempts to generate ONE passing test for a low-coverage
# class in each candidate. The outcome — compile? pass? coverage delta?
# turns used? — is the single most empirically honest check on the
# "viable_target=true" prediction scout produced.
#
# This is a stub. Current behaviour:
#   1. Pick the lowest-coverage module from the scorecard.
#   2. Choose a target class heuristically (first non-test Java file).
#   3. Write a placeholder pilot_result block into the scorecard with
#      ran=false and agent="<not-yet-wired>".
#
# Real implementation needs to spawn a Claude Code session (or a stand-alone
# general-purpose agent) with tool access (git_clone, edit-file, run_tests)
# and a tight budget. That work is tracked as bead `uc6`; do NOT inline it
# here because it's a different agent surface with its own security envelope.
#
# Usage:
#   scripts/pilot.sh <run_dir>

set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <run_dir>" >&2
  exit 2
fi

RUN_DIR="$1"
SC="$RUN_DIR/scorecard.json"
[[ -f "$SC" ]] || { echo "no scorecard.json in $RUN_DIR" >&2; exit 2; }

/usr/bin/python3 - "$SC" <<'PY'
import json, sys, datetime
path = sys.argv[1]
d = json.load(open(path))
cov = d.get("coverage", {}) or {}
per = cov.get("per_module_coverage") or []
target_module = ""
if per:
    per_sorted = sorted(per, key=lambda m: (m.get("line_coverage", 100.0), -m.get("loc", 0)))
    target_module = per_sorted[0].get("module", "")
rec = d.setdefault("recommendation", {})
rec["pilot_result"] = {
    "ran": False,
    "target_class": "",
    "target_module": target_module,
    "agent": "<not-yet-wired>",
    "pilot_compiled": False,
    "pilot_passed": False,
    "pilot_coverage_delta_percent": 0.0,
    "pilot_turns": 0,
    "pilot_cost_usd": 0.0,
    "pilot_escalations": 0,
    "notes": (
        "Pilot runner is not yet implemented (bead uc6). "
        "The selection memo must NOT commit until a real pilot runs against this "
        "target. Target module identified by lowest line coverage: "
        + (target_module or "(no per-module coverage recorded)")
        + "."
    ),
}
json.dump(d, open(path, "w"), indent=2, default=str)
print(f"pilot_result stub written to {path}; target_module={target_module or '(none)'}")
PY

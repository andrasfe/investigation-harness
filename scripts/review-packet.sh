#!/usr/bin/env bash
# Concentrate everything a teacher needs to review one run into ONE markdown file.
#
# Usage:
#   scripts/review-packet.sh <run_dir> [output.md]
#
# Produces (default <run_dir>/review-packet.md):
#   1. summary header (repo, halt reason, duration, escalations)
#   2. scorecard.json pretty-printed
#   3. verifier_report.json layer-by-layer
#   4. evidence.json (what tools claim they populated)
#   5. last 30 tool-trace entries compact view
#   6. status.jsonl heartbeats
#   7. pre-finalize review context if one exists (escalation kind=end_of_cycle_review)
#
# Intended for: "teacher, read this one file and decide". Also handy for
# post-hoc human review when the teacher was asleep.

set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <run_dir> [output.md]" >&2
  exit 2
fi

RUN_DIR="$1"
OUT="${2:-$RUN_DIR/review-packet.md}"
[[ -d "$RUN_DIR" ]] || { echo "not a dir: $RUN_DIR" >&2; exit 2; }

/usr/bin/python3 - "$RUN_DIR" "$OUT" <<'PY'
import json, sys
from pathlib import Path

run_dir = Path(sys.argv[1])
out = Path(sys.argv[2])

def _maybe_read(name):
    p = run_dir / name
    if not p.exists():
        return None
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return None

def _maybe_json(name):
    text = _maybe_read(name)
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None

scorecard = _maybe_json("scorecard.json") or {}
evidence = _maybe_json("evidence.json") or {}
report = _maybe_json("verifier_report.json") or {}

lines = []
lines.append(f"# Teacher review packet — {run_dir.name}")
lines.append("")
lines.append(f"- repo: {scorecard.get('repo_url', '?')}")
lines.append(f"- evaluation_id: {scorecard.get('evaluation_id', '?')}")
md = scorecard.get("metadata", {}) or {}
lines.append(f"- student_version: {md.get('student_version', '?')}")
lines.append(f"- duration: {md.get('evaluation_duration_seconds', '?')}s")
lines.append(f"- escalations_used: {md.get('escalation_count', '?')}")
lines.append(f"- errors: {md.get('errors_encountered', []) or '(none)'}")

if report:
    lines.append(f"- verifier: **{'accepted' if report.get('accepted') else 'REJECTED'}**")
lines.append("")

# Scorecard
lines.append("## Final scorecard")
lines.append("")
lines.append("```json")
lines.append(json.dumps(scorecard, indent=2, default=str))
lines.append("```")
lines.append("")

# Verifier
if report:
    lines.append("## Verifier layers")
    lines.append("")
    for layer in report.get("layers", []):
        mark = "OK" if layer.get("ok") else "**FAIL**"
        lines.append(f"### {layer.get('name','?')} — {mark}")
        lines.append("")
        for issue in layer.get("issues") or []:
            lines.append(f"- {issue}")
        if not (layer.get("issues") or []):
            lines.append("(no issues)")
        lines.append("")

# Evidence
if evidence:
    lines.append("## Evidence map (tool → scorecard field)")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(evidence, indent=2, default=str))
    lines.append("```")
    lines.append("")

# Tool trace (last 30)
trace_path = run_dir / "agent_trace.jsonl"
if trace_path.exists():
    entries = []
    for raw in trace_path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            entries.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    tail = entries[-30:]
    lines.append(f"## Tool trace (last {len(tail)} of {len(entries)} calls)")
    lines.append("")
    lines.append("| # | tool | ok | dur(s) | args/result summary |")
    lines.append("|---|------|----|--------|---------------------|")
    for i, e in enumerate(tail, start=len(entries) - len(tail) + 1):
        r = e.get("result", {}) or {}
        ok = r.get("ok")
        summary = ""
        for key in ("error", "checkout_path", "build_system", "test_count",
                    "line_coverage_percent_overall", "bug_fix_commit_count",
                    "stars", "java_files", "reflection_density", "verdict"):
            if key in r:
                summary = f"{key}={r[key]}"
                break
        args = e.get("args", {}) or {}
        args_s = ",".join(f"{k}={v}" for k, v in list(args.items())[:2])
        lines.append(f"| {i} | {e.get('tool','?')} | {ok} | {e.get('duration_sec','?')} | {args_s} → {summary[:120]} |")
    lines.append("")

# Heartbeats
status = _maybe_read("status.jsonl") or ""
if status.strip():
    lines.append("## Heartbeats")
    lines.append("")
    lines.append("```")
    lines.append(status.strip())
    lines.append("```")
    lines.append("")

# Pre-finalize review context if one exists
esc_path = run_dir / "escalations.jsonl"
if esc_path.exists():
    reviews = []
    for raw in esc_path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            j = json.loads(raw)
            if j.get("kind") == "end_of_cycle_review":
                reviews.append(j)
        except json.JSONDecodeError:
            continue
    if reviews:
        lines.append("## End-of-cycle review escalations")
        lines.append("")
        for r in reviews:
            lines.append(f"- id={r.get('id','?')[:8]} phase={(r.get('context') or {}).get('phase','?')} summary={r.get('summary','')}")
        lines.append("")

lines.append("## Suggested teacher actions")
lines.append("")
lines.append("Reply in the relevant `resolutions.jsonl` file, or use:")
lines.append("")
lines.append(f"- `scripts/reply.sh {run_dir} <esc_id> skip` — approve current state")
lines.append(f"- `scripts/reply.sh {run_dir} <esc_id> patch --notes ...` + manually edit JSON")
lines.append(f"- `scripts/reply.sh {run_dir} <esc_id> abort` — reject unsalvageable scorecard")
lines.append("")

out.write_text("\n".join(lines), encoding="utf-8")
print(out)
PY

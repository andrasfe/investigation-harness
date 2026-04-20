#!/usr/bin/env bash
# Produce docs/trajectory.md — the paper-grade distillation curves.
#
# For each `round-N` git tag, walks the commit range (round-N-1..round-N]
# and aggregates per-round metrics:
#   - evaluations attempted
#   - verifier pass rate (accepted / total)
#   - plausibility rejection reasons (top-3)
#   - adversarial refutation count
#   - escalation count
#   - taught-rule short-circuit count (from agent.log scans)
#
# The output is a markdown file with tables + ascii sparklines. This is
# the primary artifact for SPEC §10 success criterion 5 (capability
# distillation evidence as a publishable dataset).
#
# Usage:
#   scripts/distillation-trajectory.sh
#   scripts/distillation-trajectory.sh --output path/to/traj.md

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

OUT="docs/trajectory.md"
for a in "$@"; do
  case "$a" in
    --output) shift; OUT="$1" ;;
  esac
done
mkdir -p "$(dirname "$OUT")"

/usr/bin/python3 - "$OUT" <<'PY'
import glob, json, subprocess, sys, collections, re
from pathlib import Path

out_path = Path(sys.argv[1])

# Collect round tags sorted by version.
tags = subprocess.run(
    ["git", "tag", "--list", "round-*", "--sort=v:refname"],
    capture_output=True, text=True, check=False,
).stdout.split()

def sha_for_tag(tag):
    r = subprocess.run(["git", "rev-list", "-n", "1", tag],
                        capture_output=True, text=True, check=False)
    return r.stdout.strip()

rows = []

def _load_memos_at_tag(tag):
    """Return list of (scorecard, verifier_report, challenge_result) for
    docs/memos/ files visible at the given git tag."""
    sha = sha_for_tag(tag)
    if not sha: return []
    r = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", sha, "docs/memos/"],
        capture_output=True, text=True, check=False,
    )
    files = [f for f in r.stdout.splitlines() if f.endswith(".json")]
    by_stamp = collections.defaultdict(dict)
    for f in files:
        # filenames look like docs/memos/round1-smoke-{scorecard,challenge,viability_challenge,verifier_report}.json
        basename = Path(f).stem
        key = None
        if basename.endswith("-scorecard"): key = "scorecard"
        elif basename.endswith("-verifier_report"): key = "verifier"
        elif basename.endswith("-viability_challenge"): key = "challenge"
        if key:
            stem = basename.rsplit("-", 1)[0]
            content = subprocess.run(["git", "show", f"{sha}:{f}"],
                                     capture_output=True, text=True, check=False).stdout
            try:
                by_stamp[stem][key] = json.loads(content)
            except json.JSONDecodeError:
                pass
    return list(by_stamp.values())

prior = None
for tag in tags:
    records = _load_memos_at_tag(tag)
    # Only count records that weren't already present at the previous tag.
    if prior is not None:
        prior_ids = {(r.get("scorecard") or {}).get("evaluation_id") for r in prior}
        records = [r for r in records if (r.get("scorecard") or {}).get("evaluation_id") not in prior_ids]
    prior = records if prior is None else prior + records

    total = len(records)
    accepted = sum(1 for r in records if (r.get("verifier") or {}).get("accepted"))
    reject_reasons = collections.Counter()
    refutations = 0
    for r in records:
        vr = r.get("verifier") or {}
        for layer in vr.get("layers") or []:
            if not layer.get("ok"):
                for issue in layer.get("issues") or []:
                    reject_reasons[f"{layer['name']}: {issue[:60]}"] += 1
        ch = r.get("challenge") or {}
        for ruling in ch.get("rulings") or []:
            if ruling.get("verdict") == "refuted":
                refutations += 1

    # Count taught-rule short-circuits from agent.log — scan runs/ under
    # this tag's working tree (best-effort; runs/ is gitignored so this
    # only works when the round's logs are still on disk).
    taught_skips = 0
    for p in glob.glob("runs/**/agent.log", recursive=True):
        try:
            with open(p, encoding="utf-8") as f:
                for ln in f:
                    if "taught-rule skip" in ln:
                        taught_skips += 1
        except OSError:
            continue

    rows.append({
        "tag": tag,
        "total": total,
        "accepted": accepted,
        "pass_rate": (accepted / total) if total else None,
        "adversarial_refutations": refutations,
        "top_reject_reasons": reject_reasons.most_common(3),
        "taught_rule_skips_cumulative": taught_skips,
    })

# ascii sparkline of pass_rate
def _spark(values):
    bars = "▁▂▃▄▅▆▇█"
    cleaned = [v for v in values if v is not None]
    if not cleaned: return "(no data)"
    lo, hi = min(cleaned), max(cleaned)
    if hi == lo:
        return bars[-1] * len(cleaned)
    out = []
    for v in values:
        if v is None:
            out.append(" ")
            continue
        idx = int(round((v - lo) / (hi - lo) * (len(bars) - 1)))
        out.append(bars[idx])
    return "".join(out)

lines = []
lines.append("# Scout — capability distillation trajectory")
lines.append("")
import datetime
lines.append(f"_Generated {datetime.datetime.utcnow().isoformat(timespec='seconds')}Z_")
lines.append("")
lines.append("This file is the paper-grade artifact for SPEC §10 success criterion 5: ")
lines.append("evidence that the student gets better with each round.")
lines.append("")
lines.append("## Round-by-round metrics")
lines.append("")
if not rows:
    lines.append("_No round-tagged evaluations found yet. Tag the first round with `scripts/tag-round.sh 1`._")
else:
    lines.append("| tag | evals | accepted | pass rate | adversarial refutations | taught-rule skips (cum) |")
    lines.append("|-----|-------|----------|-----------|-------------------------|-------------------------|")
    for r in rows:
        pr = f"{r['pass_rate']*100:.0f}%" if r["pass_rate"] is not None else "—"
        lines.append(f"| {r['tag']} | {r['total']} | {r['accepted']} | {pr} | {r['adversarial_refutations']} | {r['taught_rule_skips_cumulative']} |")
    lines.append("")
    pass_rates = [r["pass_rate"] for r in rows]
    refutations = [r["adversarial_refutations"] for r in rows]
    lines.append("### Trends")
    lines.append("")
    lines.append(f"- verifier pass-rate:     `{_spark(pass_rates)}`")
    lines.append(f"- adversarial refutations: `{_spark(refutations)}`  (should trend **down** if the student is learning)")
    lines.append("")
    lines.append("### Top rejection reasons per round")
    for r in rows:
        if r["top_reject_reasons"]:
            lines.append(f"**{r['tag']}**")
            for reason, n in r["top_reject_reasons"]:
                lines.append(f"- ×{n}  {reason}")
            lines.append("")

lines.append("## Interpretation")
lines.append("")
lines.append("- **Pass rate rising** → the between-rounds teacher is successfully")
lines.append("  encoding its corrections into prompt edits, project handlers, or")
lines.append("  taught rules. This is the capability-distillation curve.")
lines.append("- **Adversarial refutations falling** → the Proposer's factual accuracy")
lines.append("  is improving; the Challenger finds fewer real disagreements.")
lines.append("- **Taught-rule skips rising while escalations fall** → the student is")
lines.append("  absorbing patterns the teacher corrected in earlier rounds and no")
lines.append("  longer needs to round-trip for them.")
lines.append("")

out_path.write_text("\n".join(lines), encoding="utf-8")
print(str(out_path))
PY

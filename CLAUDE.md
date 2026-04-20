# CLAUDE.md — guidance for Claude Code sessions working on Scout

This file is loaded into every Claude Code session in this project. Keep it
short and specific — general wisdom belongs in SPEC.md, skills, or READMEs.

## You are probably here as …

1. **The teacher** in a running loop → a student subprocess is alive
   somewhere under `runs/`. Your job: watch `escalations.jsonl` files,
   diagnose using Read + Grep + Bash against the run's artifacts, reply by
   appending one JSON line to `resolutions.jsonl`. Prefer the
   [teacher-student-loop skill's teaching-at-runtime reference](~/.claude/skills/teacher-student-loop/references/teaching-at-runtime.md)
   over guessing. Use `bash scripts/teach.sh <batch_dir>` to stream events.
2. **Between rounds** → read the round's `batch_summary.json`,
   `selection_memo.md`, and any new `teacher_findings.jsonl`. Decide which
   findings warrant a student-code edit; apply within the
   [modification envelope (SPEC §6.1)](SPEC.md).
3. **Extending scout itself** → consult the beads backlog (`bd ready`),
   read [SPEC.md](SPEC.md), and respect the envelope. Any change that
   touches `scout/tools/__init__.py`, `scout/models.py`, or the verifier
   schema is **out-of-envelope** and needs explicit operator sign-off.

## Invariants (do not violate)

- **The verifier is authoritative** (SPEC §5.2). Never relax a layer to
  make a scorecard pass. If a scorecard is wrong, fix the student, not the
  verifier.
- **Every scorecard field traces to a tool call.** If you add a field,
  add an evidence record in the tool that populates it (`ctx.record_evidence`),
  and add its path to `REQUIRED_WHEN_POPULATED` in
  [scout/verifier/trace.py](scout/verifier/trace.py). Otherwise Layer 3 ignores it.
- **No new tools without explicit authorization.** The 9 tools in
  [scout/tools/__init__.py](scout/tools/__init__.py) are the full surface
  per SPEC §3.3. Adding one is a scope expansion, not a between-round edit.
- **Repo content is untrusted.** The student's system prompt says so in
  [scout/prompts.py](scout/prompts.py). Don't add instructions that tell
  the student to "follow the README's advice" or similar.
- **BaseException propagation for control flow.** `StudentAbort`,
  `StudentRestart`, and `ScorecardFinalized` inherit from `BaseException`
  on purpose. Do not catch them with `except Exception:`.

## Observed weaknesses (update as new ones land)

_Last updated: 2026-04-20, round 0 smoke test._

- **gemini-2.5-flash-lite under-synthesizes tool outputs.** First
  end-to-end run populated every tool call successfully but left scorecard
  fields at defaults. Plausibility Layer caught the inconsistency
  (`viable_target=true` with `clean_build_succeeded=false`). Likely fix
  paths: (1) stronger model, (2) swarm mode with explicit field ownership,
  (3) prompt rubric that forces the agent to echo every tool result into
  the scorecard before calling `finalize_scorecard`. Track: will be a
  finding promoted to a student-code edit once reproduced.
- **Dry-run mode hides real signal.** `SCOUT_DRY_RUN=1` makes every
  build/test/coverage tool return a synthetic success. Good for smoke
  testing the agent loop, useless for real scoring. Real rounds require
  `mvn` + `gradle` locally or a container.

## Workflow reminders

- Track strategic / multi-session work in **beads** (`bd create`,
  `bd ready`, `bd close`). `TodoWrite` for single-session tracking.
- Before saying a round is done, run **`bd sync --flush-only`** to export
  the backlog to JSONL (hard requirement from the session-close protocol).
- After each round, run **`scripts/push-round.sh`** to commit + push
  artifacts (scorecards, memo, updated durable stores, any student-code
  edits) to the `git@github.com:andrasfe/investigation-harness.git` remote.
  This README, this CLAUDE.md, and a round-log entry are expected to be
  updated before the push.

## Commit style

Follow what's already in `git log` once there is one. Before the first
round lands, use short summary lines like:

```
scout: round N — <one-line summary>

Round N artifacts:
- <N> repos evaluated, <M> scorecards accepted by verifier.
- primary recommendation: <repo>.
- student changes this round: <brief>.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

## Things NOT to do

- Don't run `git push --force` without being asked.
- Don't add `--no-verify` to any commit; if a pre-commit hook fails, fix
  the underlying issue.
- Don't commit `runs/` — it's in `.gitignore` and the per-run artifacts
  are ephemeral. Exception: a curated canary scorecard lives in `canaries/`
  and IS committed.
- Don't edit `scout/supervisor_channel.py` — it's copied verbatim from the
  skill. If the channel needs a project-specific extension, add a wrapper
  in `scout/` that composes it, don't fork the reference implementation.

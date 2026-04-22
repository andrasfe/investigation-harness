# Scout — Investigation Harness

Agentic system that evaluates Java repositories as candidates for an
agentic test-generation project (codename *TestWright*). Implements
[SPEC.md](SPEC.md) using the `teacher-student-loop` +
`agentic-student-architecture` patterns.

**Project codename:** Scout • **Pattern:** Harnessing the Harness
(Investigation variant) • **Status:** v0.1 scaffold, loop verified end-to-end

## What it does

For a given repo, Scout produces a structured JSON scorecard covering build
tractability, coverage gap, testability signals, bug history, and maintainer
responsiveness ([SPEC §3.2](SPEC.md)). A verifier pipeline validates every
scorecard against schema + plausibility + trace-evidence checks. Batches
produce a ranked **selection memo**.

## Architecture

```
         Teacher (Claude Code, parallel session)
                 │  watches runs/*/escalations.jsonl
                 ▼
  ┌─────────────────────────────┐
  │ scripts/teach.sh            │  JSONL IPC channel (fsync'd, append-only)
  └─────────────────────────────┘
                 ▲  escalations / resolutions
                 │
  ┌─────────────────────────────┐
  │ Proposer (LLM agent)        │   OpenRouter / OpenAI-compatible
  │   single-agent  OR          │   gemini-flash-lite by default
  │   cooperative specialist    │   fixed tool surface (SPEC §3.3):
  │   swarm with judge          │     git_clone, run_build, run_tests,
  └─────────────────────────────┘     run_coverage, git_log_analyze,
                 │                    github_api_query, static_analysis,
                 ▼                    escalate, finalize_scorecard
         scorecard.json (draft)
                 │
                 ▼      [opt-in: SCOUT_ADVERSARIAL=1]
  ┌─────────────────────────────┐
  │ Challenger (LLM agent)      │   Refutes specific claims using
  │   read-only re-verifier     │   different tool slices.
  │      ↓                      │   file_challenge per dispute.
  │   Judge (deterministic)     │   → viability_challenge.json
  └─────────────────────────────┘
                 │
                 ▼
  ┌─────────────────────────────┐
  │ Verifier (SPEC §5)          │  L1 schema • L2 plausibility • L2.5 adversarial
  │                             │  L3 trace-evidence
  └─────────────────────────────┘  (L4 sampled, L5 canary: backlog)
                 │
                 ▼
         verifier_report.json  +  (optional) pilot_result (SPEC §9.4)
```

Multi-repo runs fan out under `runs/<batch>/<repo-slug>/`, each with its
own channel directory so **one teacher session covers the fleet**
(`scripts/teach.sh runs/<batch>`).

## Quick start

```bash
pip install -r requirements.txt

# One-shot (dry-run: skips mvn/gradle for smoke testing)
SCOUT_DRY_RUN=1 python -m scout.main evaluate \
    https://github.com/JSQLParser/JSqlParser

# Background + teacher monitor
SCOUT_DRY_RUN=1 bash scripts/launch.sh evaluate \
    https://github.com/JSQLParser/JSqlParser
# In another session (or this one, acting as teacher):
bash scripts/teach.sh runs/scout-<stamp>-JSqlParser

# Full batch, 4-way parallel, auto-verify and memo
ESCALATE=1 SCOUT_PARALLEL_REPOS=4 SCOUT_DRY_RUN=1 \
    bash scripts/run_batch.sh --limit 10
```

### Real builds via Docker (no host JDK required)

Scout can dispatch every `mvn` / `gradle` / `jacoco` invocation to a
container built from [docker/Dockerfile.builder](docker/Dockerfile.builder)
(OpenJDK 17 + Maven 3.9 + Gradle 8.10). The host stays clean.

```bash
# One-time: build the scout-builder image (~1 GB)
bash scripts/build-docker-image.sh

# Then enable docker-backed execution; disable dry-run
export SCOUT_USE_DOCKER=1
unset SCOUT_DRY_RUN

python -m scout.main evaluate https://github.com/JSQLParser/JSqlParser
```

Behind the scenes, `run_build` / `run_tests` / `run_coverage` shell out
to `docker run --rm --user $(id -u):$(id -g) -v <checkout>:/workspace
-v ~/.scout-docker-cache/m2:/scout-home/.m2 ... scout-builder mvn ...`.
Maven and Gradle caches persist across runs under
`~/.scout-docker-cache/` so subsequent invocations are warm.

Per-container limits: `SCOUT_DOCKER_MEM=4g`, `SCOUT_DOCKER_CPUS=2`,
`SCOUT_DOCKER_NETWORK=bridge` (set `none` for fully offline builds).
Full env reference: [`.env.example`](.env.example).

Verified on JSqlParser (2026-04-20):
- `run_build` (mvn clean compile): cold 72s, warm 36s, `clean_build_succeeded=True`
- `run_tests` (mvn test): 4635 tests, 100% pass rate, 60s
- `run_coverage`: legitimately escalates — jsqlparser's surefire argLine
  doesn't chain to jacoco's javaagent. Real signal previously hidden by dry-run.

## Configuration

All settings come from `.env` (loaded automatically) or shell env:

| Var                        | Default                              | Purpose |
|---------------------------|--------------------------------------|---------|
| `OPENROUTER_API_KEY`       | *(required)*                         | LLM auth for the student |
| `LLM_MODEL`                | `google/gemini-2.5-flash-lite`       | Cheap model for the student |
| `LLM_PROVIDER`             | `openrouter`                         | `openai` also supported |
| `SCOUT_LLM_BASE_URL`       | `https://openrouter.ai/api/v1`       | Any OpenAI-compatible endpoint |
| `SCOUT_SWARM_SIZE`         | `1`                                  | `1`=single agent, `≥2`=cooperative specialist swarm |
| `SCOUT_ADVERSARIAL`        | *(unset)*                            | `1` enables Challenger+Judge adversarial pass (V2.5) |
| `SCOUT_CHALLENGER_MODEL`   | = `LLM_MODEL`                        | Override the model used by the Challenger |
| `SCOUT_PARALLEL_REPOS`     | `1`                                  | Threads for batch evals |
| `SCOUT_ESCALATION_BUDGET`  | `3`                                  | Teacher round-trips per repo |
| `SCOUT_MAX_TOOL_CALLS`     | `60`                                 | Hard cap per agent run |
| `SCOUT_DRY_RUN`            | *(unset)*                            | Skip build/test/coverage — no local mvn/gradle required |
| `SCOUT_USE_DOCKER`         | *(unset)*                            | `1` dispatches mvn/gradle/jacoco to scout-builder container |
| `SCOUT_BUILD_IMAGE`        | `scout-builder:latest`               | Container image for docker-backed builds |
| `SCOUT_DOCKER_CACHE_DIR`   | `~/.scout-docker-cache`              | Host dir for persisted `.m2` / `.gradle` caches |
| `SCOUT_DOCKER_MEM`         | `4g`                                 | Per-container memory cap |
| `SCOUT_DOCKER_CPUS`        | `2`                                  | Per-container CPU quota |
| `SCOUT_DOCKER_NETWORK`     | `bridge`                             | `bridge` for deps, `none` for offline |
| `SUPERVISOR_DIR`           | per-run path                         | Teacher channel directory |
| `ESCALATE`                 | *(unset)*                            | Opt-in to teacher round-trips |
| `GITHUB_TOKEN`             | *(unset)*                            | Raises GitHub rate limit |

## CLI

```
scout evaluate <repo_url> [--target-modules ...]
scout batch <list_path>   [--parallel N --limit K --auto-verify]
scout verify <run_dir>
scout rank   <batch_dir>  [--output memo.md]
```

## Repository layout

- `scout/` — the student package (LLM agent + tools + verifier + ranking)
- `scripts/` — launch.sh, teach.sh, watch.sh, reply.sh, run_batch.sh
- `canaries/` — reference repos with approved scorecards (SPEC §5.1 Layer 5)
- `runs/` — per-evaluation output (scorecards, transcripts, traces, channel files)
- `state/` — student-version snapshots + project-specific handlers
- `.beads/` — backlog tracking ([bd](https://github.com/steveyegge/beads))

Key files to read first:
[scout/student.py](scout/student.py) •
[scout/agent.py](scout/agent.py) •
[scout/swarm.py](scout/swarm.py) •
[scout/tools/__init__.py](scout/tools/__init__.py) •
[scout/verifier/__init__.py](scout/verifier/__init__.py) •
[scout/prompts.py](scout/prompts.py).

## Round discipline

After each evaluation/batch round Scout emits:

- every per-repo `scorecard.json` and `verifier_report.json`
- a `batch_summary.json` (per-batch aggregate)
- a `selection_memo.md` (when `--auto-verify` is set)
- updated durable stores: `teacher_rules.jsonl`, `teacher_facts.jsonl`,
  `teacher_findings.jsonl` (populated by teacher replies)

The operator then runs `scripts/push-round.sh` to commit + push the round's
artifacts and any student code edits made between rounds.

## Known limitations (first pass)

- **Weak LLMs underpopulate the scorecard.** First smoke test with
  `google/gemini-2.5-flash-lite` produced a scorecard where every tool
  returned OK but the final JSON kept defaults (zeros, `"other"` build
  system). Plausibility verifier catches this (`viable_target=true`
  requires build+tests to have succeeded). Upgrading to the swarm, a
  stronger judge model, or more explicit prompt rubrics is in the backlog.
- **mvn/gradle not required on host.** Either install them locally, or
  run `bash scripts/build-docker-image.sh` once and set `SCOUT_USE_DOCKER=1`
  — the scout-builder container supplies OpenJDK 17 + Maven + Gradle.
- **Verifier Layers 4 (sampled correctness) and 5 (canary regression)**
  are in the beads backlog, not implemented yet.

See [SPEC.md](SPEC.md) for the full specification and
[CLAUDE.md](CLAUDE.md) for agent-session guidance.

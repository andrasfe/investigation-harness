# Scout — Selection Memo (round 11 — FIRST REAL-BUILD BATCH)

_Generated on Tier-1 subset, docker-backed execution (JDK 17 container)_

**This is the first batch with empirical build/test evidence.** Rounds 1–10
ran with `SCOUT_DRY_RUN=1`, returning synthetic tool successes. Round 11 is the
first time `run_build`, `run_tests`, `run_coverage` executed real `mvn` commands
against the target repos (via `scout-builder:latest` container).

**Candidates evaluated:** 5 Tier-1 repos
**Verifier-accepted:** 5 / 5
**Empirically viable:** 4 / 5 (round 10: 0 / 5)

## Ranking

| rank | repo | composite | build (real) | tests (real) | pass% | bugs 24mo | viable |
|------|------|-----------|--------------|--------------|-------|-----------|--------|
| 1 | https://github.com/JSQLParser/JSqlParser | **8.55** | maven/True/36s | 4635 | 100% | 126 | True |
| 2 | https://github.com/apache/commons-imaging | **7.75** | maven/True/5s | 1150 | 100% | 31 | True |
| 3 | https://github.com/apache/commons-compress | **7.65** | maven/True/26s | 4591 | 100% | 127 | True |
| 4 | https://github.com/jhy/jsoup | **7.35** | maven/True/4s | 1952 | 100% | 25 | True |
| 5 | https://github.com/jgrapht/jgrapht | **4.70** | maven/False/2s | 0 | — | 4 | False |

## Compared to round 10 (dry-run)

| repo | round 10 composite | round 11 composite | Δ | round 10 viable | round 11 viable | round 11 tests |
|------|-------------------|-------------------|---|-----------------|-----------------|----------------|
| JSqlParser | 8.05 | **8.55** | +0.50 | False | **True** | 4635 |
| commons-imaging | 6.85 | **7.75** | +0.90 | False | **True** | 1150 |
| commons-compress | 7.15 | **7.65** | +0.50 | False | **True** | 4591 |
| jsoup | 6.45 | **7.35** | +0.90 | False | **True** | 1952 |
| jgrapht | 6.3 | **4.70** | +-1.60 | False | **False** | 0 |

## Primary recommendation

**https://github.com/JSQLParser/JSqlParser** — composite 8.55, empirically viable

- build_system: maven (real clean-compile: 36s)
- test suite: 4635 tests, 100% pass rate (real mvn test)
- bug_fix_commits_24mo: 126
- stars: ?, last commit: 2026-04-12T12:16:18Z
- notes: 

## Backup
**https://github.com/apache/commons-imaging** — composite 7.75, empirically viable
- test suite: 1150 tests at 100% pass

## What the real builds exposed

- **jgrapht cannot compile under JDK 17** — requires JDK 21 (`maven-compiler-plugin` target `release=21`).
  10 rounds of dry-run labelled it a viable-but-lowest candidate; empirically it isn't viable at all on the
  current toolchain. Follow-up: upgrade `scout-builder` to ship JDK 21 as well (see backlog).
- **Coverage uniformly 0%** across all 5 repos: JSqlParser has a real argLine/jacoco integration bug
  (surefire's `<argLine>` literal doesn't chain `@{argLine}`, so the jacoco agent never attaches).
  Apache commons repos don't have jacoco configured by default in their poms. `run_coverage` returned
  `should_escalate=true` in each case — correct behavior per spec (scout never edits build configs).
- **No catastrophic regressions vs. round 10 rankings** despite real-vs-synthetic builds. JSqlParser and
  commons-compress remain top picks; commons-imaging moved up (now evaluated on actual compile+test evidence).

## Caveats

- JDK 17 only. Projects targeting JDK 21+ will fail compile on current scout-builder image.
- Pilot run (SPEC §9.4, bead `uc6`) has NOT run; this memo is not a final commit, but it IS the first
  time Scout has produced scorecards with empirical build+test evidence rather than dry-run stubs.
- No adversarial pass in this batch (not set on the command). Round 11 is intentionally a pure empirical-build run.
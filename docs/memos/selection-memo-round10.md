# Scout — Selection Memo (round 10)

_Generated 2026-04-21T02:38:05Z_

**Candidates evaluated:** 5 Tier-1 repos (batch round-10, DRY_RUN=1)

**Verifier-accepted:** 5 / 5


## Ranking

| rank | repo | composite | build | tests (dry) | bug_fix_24mo | viable | notes |
|------|------|-----------|-------|-------------|--------------|--------|-------|
| 1 | https://github.com/JSQLParser/JSqlParser | **8.05** | maven/True | 0 | 126 | False | The project is a highly active SQL parser with a strong bug-fix histor |
| 2 | https://github.com/apache/commons-compress | **7.15** | maven/True | 0 | 127 | False | Project is a viable target. Build and tests are tractable. There is a  |
| 3 | https://github.com/apache/commons-imaging | **6.85** | maven/True | 0 | 31 | False | Project is buildable and has a rich bug history. However, it lacks a J |
| 4 | https://github.com/jhy/jsoup | **6.45** | maven/True | 0 | 25 | False | Project is highly stable and well-maintained. However, it lacks a JaCo |
| 5 | https://github.com/jgrapht/jgrapht | **6.30** | maven/True | 0 | 4 | False | Project is a high-quality library with a clean build and low testabili |

## Primary recommendation

**https://github.com/JSQLParser/JSqlParser** — composite 8.05

- repo_metadata: 5942 stars, license=Apache-2.0, last_commit=2026-04-12T12:16:18Z
- build_system: maven
- bug_fix_commits_24mo: 126
- maintainer activity: 100 commits / 12 contributors in the last 12mo, last release: 2025-05-17T23:52:30Z
- testability signals: refl=low, static=low, fs=low, sleeps=0

## Backup: https://github.com/apache/commons-compress (composite 7.15)

## Caveats

- All 5 scorecards ran with `SCOUT_DRY_RUN=1`: build/test/coverage tools returned synthetic successes. `recommendation.viable_target` is deliberately `false` on every row — the student correctly declines to claim viability without real build/test data.
- **Pilot run (SPEC §9.4, bead `uc6`) has NOT run**; this memo is not a final commit.
- Weights (SPEC §9.2): build 0.25, coverage_gap 0.25, testability 0.20, bug_history 0.15, maintainer 0.15.

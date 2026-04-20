# Scout round log

## 2026-04-20T12:54Z — round 1 — adversarial evaluation landed

- target: JSQLParser/JSqlParser (DRY_RUN=1, no escalate, adversarial=1)
- proposer turns: 10; challenger turns: 2 (6 tool calls)
- scorecard claims: viable_target=true with 3 viability_evidence items
- challenger refuted 2 factual claims by re-citing the exact tool outputs:
  - `bug_history.bug_fix_commits_24mo`: proposer=0, actual (git_log_analyze)=126
  - `maintainer_activity.commits_last_12mo`: proposer=0, actual (github_api_query)~=100
- judge: both `refuted` → viability_challenge.passed=false
- verifier: REJECTED on three layers
  - plausibility (2 contradictions: test_count=0 vs run_succeeded=true; build_system=other vs succeeded=true)
  - adversarial (judge refuted 2 claims)
- outcome: scorecard correctly rejected. Round 1 V2.5 added signal that Layer 2 alone would not have caught.
- regression fixtures: docs/memos/round1-smoke-*.json

## 2026-04-20T12:24Z — round 0 — scaffold smoke

- target: JSQLParser/JSqlParser (DRY_RUN=1)
- proposer turns: 18; no escalations; no adversarial
- verifier: rejected on plausibility (viable_target=true vs clean_build_succeeded=false)
- outcome: infrastructure works; weak-LLM synthesis gap identified → motivated round 1 countermeasures

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

## 2026-04-20 — round 3 — field-extraction table added to prompt

- pattern: prior rounds had `build_system="other"` and `bug_fix_commits=0` despite tools returning real values
- edit: added an explicit "FIELD EXTRACTION DISCIPLINE" table at the top of `FULL_AGENT_INSTRUCTIONS` mapping every scorecard field to the tool-result key it must be copied from
- outcome: **verifier accepted=True (all 4 layers)**. Scorecard now has `build_system: maven`, `bug_fix_commits_24mo: 126`, correct `viable_target: false` in dry-run. Student correctly abstains from claiming viability when tools report synthetic success.
- next: add dry-run detection + Apache project_handlers


## 2026-04-20 — round 4 — repo_metadata field mapping added

- pattern: round-3 scorecard had repo_metadata all-empty despite github_api_query returning stars=5942, license=Apache-2.0, etc.
- edit: Phase B in prompts.py now explicitly maps each github_api_query response field to the scorecard path
- outcome: **no measurable improvement**. LLM still writes empty strings. The hypothesis: tool results from Phase B age out of attention before the finalize call. Next round will try a pre-finalize inventory step.


## 2026-04-20 — round 5 — structural autofill from trace

- pattern: rounds 3–4 showed the weak LLM doesn't re-read tool results when composing the final scorecard. Prompt-only fixes plateaued.
- edit: added `_autofill_from_trace` in `scout/tools/scorecard_writer.py` — when the student passes a scorecard with default values for a field that a tool result populated, autofill from the trace. Never overwrites non-default student values. Autofilled paths written to a sidecar `autofilled_fields.json`.
- also fixed: autofill's test_run_succeeded now requires test_count>0 (dry-run run_tests returns ok=true with 0 tests).
- outcome: **FIRST rich scorecard.** stars=5942 (was 0), Apache-2.0 license, real last_commit_date, maintainer commits=100, 5 sampled bug_fix commits with per-commit rationale, score subscores populated (composite=9.5), 3 viability_evidence items, useful notes text. Verifier accepts all 4 layers. Student still correctly declines viable=true because tests didn't run (dry-run).
- next: pivot to multi-repo — rounds 6–10 will evaluate diverse repos from initial-list.txt to test whether improvements generalise beyond JSqlParser.


## 2026-04-20 — round 6 — first multi-repo batch (5 Tier-1 repos)

- first pivot: batch across JSqlParser, Commons Compress, Commons Imaging, JGraphT, jsoup (DRY_RUN + adversarial).
- outcome: **1/5 accepted** (jsoup). Rejection reasons:
  - 3/5: `maintainer_activity.last_release_date unparseable` — student writes the string "null" when github_api_query returns 404 on `/releases/latest`
  - 3/5: `viable_target=true but test_run_succeeded=false` — LLM keeps claiming viability in dry-run despite prompt rule
  - 1/5: jgrapht adversarial refutation on `bug_fix_commits_24mo` (challenger got 2 with standard patterns vs proposer's 4 — legitimate nuance)
- also fixed: agent.py stagnation guard — halt after 3 turns with no new tool calls (the LLM otherwise loops on "unknown tool" for mis-spelled tool names).
- next: round 7 adds structural auto-downgrade viable→false when test_count=0, and normalises missing dates to JSON null (not the string "null").


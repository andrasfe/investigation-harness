# Scout round log

## 2026-04-21 — round 13 — Rust-portability rubric v2 (coverage proxy + static_state demotion)

- pattern: round 12 produced **0/5 viable** candidates under the first-cut
  Rust-portability rubric despite 4/5 scorecards being verifier-accepted.
  Two gates were over-strict:
  (a) `coverage_gap.satisfied` hard-failed when upstream had no JaCoCo
      wired, conflating infrastructure-gap with low coverage.
  (b) `testability_tractable.satisfied` hard-failed on `static_state=high`,
      which in practice is lookup tables / const arrays (Base64 alphabets,
      bitmap offset tables) that port cleanly to Rust `static`/`const`.
- edit: `scout/prompts.py` only, envelope-clean, +93/-31 lines.
  - coverage gate: satisfied iff measured ≥60% **OR** unmeasured AND
    `test_count ≥500` AND `pass_rate ≥0.95` (high-test-count proxy).
  - testability gate: `static_state_density` removed from the gate,
    demoted to a −1 subscore penalty. `reflection_density=high` retained
    as the real Java-idiomatic blocker.
- outcome: **5/5 verifier-accepted, 2/5 viable.**
  - commons-codec (composite 7.35) and RoaringBitmap (7.35) **flipped
    False → True** as predicted.
  - java-diff-utils produced a scorecard (top composite 7.75) but
    viable=False on the 500-test proxy (only 149 tests) — dark-horse
    pilot candidate.
  - vavr and minimal-json remained viable=False for the right reasons
    (reflection=high + JDK21 needed; dead project respectively).
- model-regression finding: first round-13 attempt with
  `google/gemma-4-26b-a4b-it` produced 0/5 scorecards — the 26b-a4b
  variant emits a bare "thought" text token and halts without tool_use
  blocks (broken chat template on OpenRouter). Reverted `.env` to
  `google/gemma-4-31b-it` (round-12 working model).
- next-round signal: pilot on commons-codec Base64 (bead `uc6` / V6
  gate). Until a single class actually ports and Java tests pass against
  the Rust implementation, viable=True is still a prediction.
- memo: `docs/memos/selection-memo-round13-rust-portability.md`.

## 2026-04-21 — round 12 — first Rust-portability rubric attempt (0/5 viable, calibration needed)

- pattern: the user flagged that Scout had been optimising for TestWright
  selection (coverage-gap, bug-rich) when the actual goal is Java→Rust
  porting (zero-dep, pure algo, thorough test suite as oracle).
- edit: prompts-only reinterpretation of the frozen scorecard schema.
  - `score.coverage_gap_value` now rewards HIGH coverage (not gap).
  - `score.testability` repurposed as "portability tractability".
  - `viability_evidence[coverage_gap]` = "≥60% coverage".
  - `viability_evidence[testability_tractable]` = "reflection=low AND
    static_state≠high AND no external services".
  - Added Phase B.5 dependency + size probe; new rust-portability repo
    list (commons-codec, RoaringBitmap, java-diff-utils, vavr,
    minimal-json) via `SCOUT_REPO_PROFILE=rust-portability`.
- outcome: 4/5 scorecards (1× transient 429), **0/5 viable** under the
  new rubric. Both gates fired false positives on otherwise-portable
  repos, exposing two calibration bugs that round 13 fixed.
- next-round signal: split `coverage_gap.satisfied` into
  measured-and-high vs unmeasured-but-thorough; demote static_state.
- memo: `docs/memos/selection-memo-round12-rust-portability.md`.

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


## 2026-04-20 — round 7 — dry-run viability downgrade + null-date normalisation

- pattern observed in round 6: 3/5 rejected on "viable=true with test_count=0" and "last_release_date unparseable (string 'null')"
- structural edits to `scout/tools/scorecard_writer.py`:
  * auto-downgrade `recommendation.viable_target=false` when build didn't succeed OR test_count==0 (dry-run guard)
  * normalise string "null"/"none"/""  in `last_release_date` to JSON null; copy real dates from `/releases/latest data.published_at` only when present
- also: `scout/verifier/plausibility.py` tolerates "null"/"none" strings as legitimate absent
- outcome: **4/5 accepted** (commons-compress stagnated on turn 9 after 3 turns of no new tool calls — the stagnation guard cleanly aborted a pathological loop). JSqlParser, commons-imaging, jgrapht, jsoup all verifier-accepted.
- remaining gap: composite=0.0 for every repo — the LLM isn't populating score subscores. Round 8 will derive them mechanically from evidence.


## 2026-04-20 — round 8 — mechanical score subscore derivation

- pattern observed in round 7: every accepted scorecard had composite=0.0 because the LLM never populated the 5 subscores
- edit in `scout/tools/scorecard_writer.py` autofill: when all 5 subscores are 0, derive them heuristically from evidence:
  * `build_tractability`: from build success + build time bucket (fast→10, slow→4, dry-run→8)
  * `coverage_gap_value`: from line coverage (0% → 5 in dry-run, <40% → 10, >85% → 2)
  * `testability`: 10 minus penalties for high reflection/static-state density, external deps, thread_sleep > 20
  * `bug_history_richness`: from bug_fix_commits_24mo (0 → 0, 126 → 10)
  * `maintainer_responsiveness`: commits+committers+release activity
- outcome: **3/5 accepted** (JSqlParser composite 7.75, commons-imaging 6.85, jgrapht 6.3, all non-zero and differentiated). commons-compress stagnated (same as round 7). jsoup hung mid-run — LLM appears to loop silently without triggering stagnation; tracked for round 9 diagnosis.
- net progress: first round producing **ranked candidates with meaningful composites**.


## 2026-04-20 — round 9 — max_tokens + minimal-finalize fix → 5/5 accepted

- pattern observed in round 8: commons-compress + jsoup got stuck with finalize_scorecard calls whose JSON arguments were truncated mid-string ("malformed JSON arguments — fix and retry"). Root cause: LLM output token limit was 2048, exceeded by large sampled_bug_fixes arrays.
- edits:
  * `scout/llm.py`: max_tokens 2048 → 4096
  * `scout/prompts.py` Phase I: tell the student to pass a MINIMAL scorecard (no sampled_bug_fixes, no per_module_coverage, no score, no viability_evidence) — autofill enriches server-side. Cuts the finalize payload to well under 2000 tokens.
- outcome: **5/5 ACCEPTED** with differentiated composites:
  * JSqlParser 7.65, commons-compress 7.55, commons-imaging 7.25, jsoup 6.45, jgrapht 6.30
- first batch where every Tier-1 repo produced a verifier-accepted scorecard.


## 2026-04-20 — round 10 — stable final batch + selection memo

- outcome: **5/5 accepted, second consecutive clean batch**. Composites stable:
  * JSqlParser 8.05 (↑ from 7.65)
  * commons-compress 7.15 (↓ from 7.55 — normal LLM variance)
  * commons-imaging 6.85 (↓ from 7.25)
  * jsoup 6.45 (unchanged)
  * jgrapht 6.30 (unchanged)
- generated `docs/memos/selection-memo-round10.md` with ranked candidates.
- regenerated `docs/trajectory.md` — per-round pass rate curve showing the round-3 breakthrough and the round-9 consolidation.
- **primary recommendation (tentative)**: JSQLParser/JSqlParser.
- **caveat**: all rows viable=False because DRY_RUN=1. A real round would install mvn+gradle and actually run builds/tests/coverage — scout's structure (verifier, adversarial, autofill) is now ready for that.

### 10-round summary

| round | what changed | acceptance |
|-------|--------------|------------|
| 0 | scaffold smoke | 0/1 |
| 1 | adversarial + viability_evidence | 0/1 |
| 2 | learning channels (rules/facts seed) | 1/1 |
| **3** | **field-extraction table → first accept** | 1/1 |
| 4 | Phase B mapping (null) | 1/1 |
| **5** | **structural autofill → rich scorecard** | 1/1 |
| 6 | first multi-repo batch | 1/5 |
| 7 | dry-run viability downgrade + null date fix | 4/5 |
| 8 | mechanical score subscore derivation | 3/5 (2 hangs) |
| **9** | **max_tokens + minimal-finalize → all green** | 5/5 |
| 10 | stability re-batch + selection memo | 5/5 |


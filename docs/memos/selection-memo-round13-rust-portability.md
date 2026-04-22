# Scout — Selection Memo (round 13 — RUST-PORTABILITY RUBRIC v2)

_Docker-backed execution (JDK 17), model `google/gemma-4-31b-it` after a
failed first attempt with `google/gemma-4-26b-a4b-it` (see § 4)._

**Rubric revision.** Round 12 produced 0 viable candidates out of 5 under
the first-cut Rust-portability rubric. The memo identified two over-strict
gates. Round 13 applies two targeted `scout/prompts.py` fixes (envelope-
clean, +93/-31 lines) and re-runs the same 5-repo list.

| gate | round-12 rule | round-13 rule |
|------|---------------|---------------|
| `coverage_gap.satisfied` | measured coverage ≥60% only (hard fail on unmeasured) | measured ≥60% **OR** unmeasured + `test_count ≥500` + `pass_rate ≥0.95` (high-test-count proxy) |
| `testability_tractable.satisfied` | reflection=low AND static_state≠high AND no external services | reflection=low AND no external services AND thread_sleep≤2 AND runtime_deps≤5-or-unknown. **static_state removed from the gate** — now a −1 subscore penalty only (rationale: static lookup tables port cleanly to Rust `const`/`static`). |

**Candidates evaluated:** 5 (same list as round 12).
**Verifier-accepted:** 5 / 5 (up from 4 / 5 — gemma-4-26b variant swap
also unblocked java-diff-utils's previous 429 miss).
**Empirically viable under revised rubric:** 2 / 5.

## Ranking

| rank | repo | composite | build | tests | static_state | reflection | viable | Δ vs r12 |
|------|------|-----------|-------|-------|--------------|------------|--------|----------|
| 1 | https://github.com/java-diff-utils/java-diff-utils | **7.75** | maven / 7s / True | 149 @ 100% | medium | low | **False** | new (r12 had no scorecard) |
| 2 | https://github.com/apache/commons-codec | **7.35** | maven / 3s / True | 18,837 @ 99.99% | high | low | **True** | **flipped** False → True |
| 2 | https://github.com/RoaringBitmap/RoaringBitmap | **7.35** | gradle / 21s / True | 70,506 @ 100% | high | low | **True** | **flipped** False → True |
| 4 | https://github.com/vavr-io/vavr | **4.25** | maven / 5s / FAIL (JDK21) | 0 | high | **high** | **False** | unchanged (legit reflection blocker) |
| 5 | https://github.com/ralfstx/minimal-json | **3.60** | maven / 1s / FAIL (Java5) | 0 | medium | low | **False** | unchanged (dead project) |

## How the two fixes played out

### Fix 1 — coverage gate (high-test-count proxy)

The round-12 memo argued that an upstream repo choosing not to wire
jacoco is a ~10-line fix for the porter and should not hard-disqualify
a candidate. The revised gate says: if coverage is unmeasured but the
repo has ≥500 passing tests at ≥95% pass rate, that's a thorough oracle.

Effect in round 13:
- **commons-codec** (18,837 tests @ 99.99%) → `coverage_gap.satisfied=true`
  via proxy (`metric=tests.test_count`, `observed_value=18837`).
- **RoaringBitmap** (70,506 tests @ 100%) → same.
- **java-diff-utils** (149 tests @ 100%) → **fails the proxy** (149 < 500).
  This is the rubric working correctly: 149 tests for a diffing library
  might or might not be a thorough oracle, and the harness can't tell
  without running the pilot. Flagged below as a pilot candidate.
- **vavr / minimal-json** (0 tests, build failed) → correctly fail both
  branches.

### Fix 2 — static_state demotion + reflection elevation

Both commons-codec and RoaringBitmap have `static_state_density=high`
(Base64 alphabets, `RunContainer.MAX_CAPACITY`, bitmap offset tables).
These port cleanly to Rust as `static` / `const` items. The round-12
rubric was treating them as hard fails. The revised rubric demotes this
to a −1 subscore penalty and adds nothing to the gate.

Effect in round 13:
- **commons-codec** / **RoaringBitmap** both still have `static=high`,
  but both now pass `testability_tractable.satisfied=true` because the
  real gate (reflection=low + thread_sleep=0 + external=[http] + deps
  unknown) holds.
- **vavr** still has `reflection=high` (pattern-matching DSL uses
  reflection internally) — the revised rubric retains this as a hard
  blocker. Correctly fails.
- **java-diff-utils** has `static=medium` + `reflection=low` — testability
  subscore **8** (highest of the batch); gate passes.

The rubric is now sharper: the two that flipped to viable are genuinely
portable lookup-table-heavy libraries; the one that stays non-viable
(vavr) does so for the right reason (reflection, not false-positive
static state).

## Primary recommendation (under revised rubric)

**Two tied candidates at composite 7.35, with a dark-horse third.**

> ### Primary: **https://github.com/apache/commons-codec** (composite 7.35, viable=True)
>
> 18,837 passing tests at 99.99%, maven single-module, 3-second build
> under JDK 17, no reflection, no external services, thread_sleep=0,
> static state is Base64/Hex/Phonetic lookup tables. ~13,050 est LoC.
> **Family of sub-utilities** (Base64, Hex, Phonetic, DigestUtils) can
> each be ported as a standalone Rust crate. A Base64-first pilot is the
> obvious starting point.

> ### Co-primary: **https://github.com/RoaringBitmap/RoaringBitmap** (composite 7.35, viable=True)
>
> 70,506 passing tests at 100%, gradle, 21-second build under JDK 17,
> no reflection, no external services, thread_sleep=0, static state is
> offset/container tables. ~19,800 est LoC. **Caveat:** a Rust port
> already exists as the `roaring` crate, so this is more useful as a
> reference implementation / parity oracle than as original porting
> work. If the goal is "demonstrate the methodology", commons-codec is
> cleaner; if the goal is "have a known-good Rust parity reference
> when porting", RoaringBitmap's existing `roaring` gives free ground
> truth.

> ### Dark horse: **https://github.com/java-diff-utils/java-diff-utils** (composite 7.75 top, viable=False)
>
> Top composite driven by **highest testability subscore (8)** —
> static_state=medium, low reflection, minimal surface area. Only 149
> tests though, which trips the high-test-count proxy. This is a case
> where the rubric is honestly uncertain and the pilot is the tie-
> breaker: if 149 tests cover ≥60% of the lines (could be true for a
> small, tight algorithmic library), it's viable. Pilot is cheap
> (4950 est LoC). **Recommended pilot secondary target**: if the
> commons-codec pilot succeeds, run a second pilot on java-diff-utils
> to see whether the small-library + small-suite pattern also works.

## Remaining non-viable (both legitimate, rubric correctly rejects)

- **vavr** — `reflection_density=high` is a real Rust-port blocker, and
  the library needs JDK 21 which scout-builder doesn't ship. Even with
  JDK 21, the pattern-matching internals use reflection pervasively.
  Do not port.
- **minimal-json** — Java 5 target rejected by modern maven-compiler;
  last commit 2019; dead project. No test suite to validate a port
  against. Do not port.

## Compared to round 12 — same rubric, fixed gates

| metric | round 12 | round 13 |
|--------|----------|----------|
| scorecards produced | 4 / 5 (1 × 429) | **5 / 5** |
| verifier-accepted | 4 / 5 | **5 / 5** |
| viable=True | 0 / 5 | **2 / 5** |
| top composite | 7.35 (commons-codec, but viable=False) | 7.75 (java-diff-utils, viable=False) |
| primary recommendation status | "no clean viable candidate" | **two tied viable candidates + 1 pilot candidate** |

The two fixes flipped exactly the two candidates the round-12 memo
flagged, and all three "rubric working correctly" outcomes (vavr,
minimal-json, java-diff-utils) have distinct, defensible rationales.
That's the signal that the revised rubric is properly calibrated —
it's rejecting for reasons, not noise.

## Actionable follow-ups

1. **Pilot on commons-codec Base64** (bead `uc6`). This is the V6 empirical
   gate from CLAUDE.md — until a single class ports and the existing Java
   tests pass against the Rust implementation, "viable=True" is still a
   prediction. Commons-codec Base64 is a good first target: small, pure,
   well-tested.
2. **Secondary pilot on java-diff-utils** if commons-codec pilot passes.
   This tests whether the high-test-count proxy is the right threshold —
   if java-diff-utils's 149 tests are enough to validate a port, the
   proxy threshold (currently 500) is probably too strict; if they're
   not, 500 is about right.
3. **Do not re-rank commons-codec vs RoaringBitmap without running at
   least one pilot.** They're empirically tied at 7.35 with different
   trade-offs; ranking them further is noise.
4. **Robustness finding — model matters more than rubric.** First round-
   13 attempt used `google/gemma-4-26b-a4b-it` (the 26b activation-4b-
   variant); every one of the 5 runs halted with bare "thought" text and
   zero tool_use blocks. The model exists on OpenRouter but has a broken
   chat template for the tool-use path. Reverting to `google/gemma-4-31b-
   it` (the round-12 model) recovered 5/5 scorecards. This isn't a scout
   bug — it's an OpenRouter / model-provider regression. Track as a known-
   bad model and stay on 31b-it or move to a non-gemma model for future
   rounds.
5. **runtime_deps decode gap still present.** All 5 scorecards report
   `runtime_deps=unknown` because `github_api_query` returns base64-
   encoded pom.xml content that the agent can't decode inline. This is
   an out-of-envelope fix (needs either a new tool or a tool-spec
   widening). Not blocking — the revised rubric treats unknown as pass.
6. **Rubric v2 is now a load-bearing artifact.** If future rounds adjust
   it again, keep the round-12 → round-13 delta as the calibration audit
   trail and note the new fix in a round-N memo like this one.

## Caveats

- No pilot runs (SPEC § 9.4, bead `uc6`) yet. This memo remains
  exploratory — "viable=True" is a prediction until V6 passes.
- No adversarial pass (V2.5). All round-13 runs are pure Proposer.
  Turning on `--adversarial` on the two viable candidates before
  committing to a pilot is cheap insurance.
- `runtime_deps=unknown` for all 5 repos (see follow-up 5). This
  weakens the confidence on the `testability_tractable` gate for both
  viable candidates — there could still be heavy Maven deps hidden in
  the pom that would change the picture. Low probability for
  commons-codec (well-known zero-dep Apache lib) and RoaringBitmap
  (well-known zero-dep bitmap lib), but not verified from the tool
  trace this round.
- Scout-builder ships JDK 17 only (vavr needs 21). Known backlog item.

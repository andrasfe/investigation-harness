# Scout — Selection Memo (round 12 — RUST-PORTABILITY RUBRIC)

_Generated on a hand-picked list of small, low-dep pure-Java candidates, docker-backed execution (JDK 17 container)._

**Rubric shift:** round 12 is the first batch evaluated under the **Rust-portability
rubric** (see `scout/prompts.py` changes in commit for this round). The frozen
scorecard schema was retained but the semantics of three subscores and three
viability criteria were re-interpreted:

| frozen name | previous meaning (TestWright) | new meaning (Rust-port) |
|-------------|-------------------------------|-------------------------|
| `score.coverage_gap_value` | high when coverage is LOW (opportunity) | high when coverage is HIGH (port oracle) |
| `score.testability` | how easy to add tests | how portable to Rust |
| `viability_evidence[coverage_gap].satisfied` | <80% coverage | ≥60% coverage (port validation) |
| `viability_evidence[testability_tractable].satisfied` | clean for test-gen | reflection=low AND static_state≠high AND runtime_deps≤5 |

**Candidates evaluated:** 5 (RoaringBitmap, commons-codec, java-diff-utils, vavr, minimal-json)
**Verifier-accepted:** 4 / 5 (java-diff-utils: LLM provider rate-limited; no scorecard)
**Empirically viable under NEW rubric:** 0 / 5

## Ranking

| rank | repo | composite | build | tests | coverage | static_state | reflection | viable |
|------|------|-----------|-------|-------|----------|--------------|------------|--------|
| 1 | https://github.com/apache/commons-codec | **7.35** | maven / 3s / **True** | 18,837 @ 99.99% | unmeasured | **high** | low | **False** |
| 2 | https://github.com/RoaringBitmap/RoaringBitmap | **6.85** | gradle / 78s / **True** | 70,506 @ 100% | unmeasured | **high** | low | **False** |
| 3 | https://github.com/vavr-io/vavr | **4.25** | maven / 28s / **FAIL (JDK21)** | 0 | unmeasured | **high** | **high** | **False** |
| 4 | https://github.com/ralfstx/minimal-json | **3.60** | maven / 7s / **FAIL (Java 5)** | 0 | unmeasured | medium | low | **False** |
| 5 | https://github.com/java-diff-utils/java-diff-utils | — | — (LLM 429) | — | — | — | — | — |

## Under the new rubric, why did everything fail?

The rubric has three viability gates. Each was hit for different reasons:

### Gate 1 — `coverage_gap.satisfied` (≥60% line coverage)
**Failed by all 5 repos.** Zero of them had JaCoCo configured in their upstream
pom.xml / build.gradle. `run_coverage` correctly escalated rather than
fabricating a number. This is the **most important finding** of the round:

- For Rust porting, the existing test suite IS the validation oracle. If we
  can't measure its coverage, we don't know how much of the ported code will
  actually be validated.
- But this is a **fixable condition for the porter** — adding the jacoco-maven
  plugin to a downstream fork is ~10 lines of pom. The upstream's choice not
  to measure coverage doesn't mean coverage is low, just that it's unmeasured.
- **Rubric calibration note:** a hard-fail on "coverage unmeasured" may be
  too strict. Future round should distinguish `coverage=low (measured)` (hard
  fail) from `coverage=unmeasured (infrastructure gap)` (recoverable).

### Gate 2 — `testability_tractable.satisfied` (low reflection, non-high static state, minimal deps)
**Failed by 4 of 5** (everyone except the Java-5 dead-project minimal-json,
which oddly scored medium static state).

- `static_state_density=high` is tripped by the `_STATIC_STATE_PATTERNS` regex
  that counts non-final `public|protected|private static` fields. Both
  commons-codec and RoaringBitmap hit this. **In practice, most of these are
  lookup tables and precomputed constant arrays** — e.g. RoaringBitmap's
  `RunContainer.MAX_CAPACITY` or commons-codec's Base64 alphabet arrays.
  These actually port **cleanly** to Rust as `static` or `const` items.
- The regex in `scout/tools/static_analysis.py` excludes `final` but still
  counts many idioms (static caches, `static Logger`, etc.) that are noise
  for the Rust-port question. **Rubric calibration note:** the
  static_state_density measurement is a weak proxy for "Java-specific
  idioms". Future version should separately count: reflection, annotation
  processors, AOP, dynamic proxies — those are the real blockers.
- vavr legitimately has `reflection_density=high` because its pattern-matching
  DSL uses reflection internally. That's a genuine Rust-port blocker and
  the rubric correctly flagged it.

### Gate 3 — `build_tractable` (clean build + tests pass)
**Failed by 2 of 5:**
- **vavr 2.0** requires JDK 21. Same problem as jgrapht in round 11. Our
  scout-builder ships JDK 17. Follow-up: multi-JDK container.
- **minimal-json** targets Java 5 source/target, and `maven-compiler-plugin`
  3.2 rejects that on a modern JDK ("Source option 5 is no longer supported").
  The project is essentially unmaintained (last commit 2019). Dead-project
  signal arrived before the Rust-port analysis could even start.

## Relative ranking if we relax both gates (thought experiment)

If we ignore `coverage_gap.satisfied` (because coverage-infra is a 10-line
fix) and if we interpret `static_state=high` as "probably const arrays",
then the ranking collapses to:

1. **commons-codec** — 18,837 tests, JDK 17 compatible, 3-sec build, low
   reflection, mostly codec lookup tables. **The most portable candidate of
   the five.** Family of sub-utilities (Base64, Hex, Phonetic, DigestUtils)
   could each be a standalone Rust crate.
2. **RoaringBitmap** — 70,506 tests, pure bitmap algorithm, gradle,
   78-sec build, low reflection. **A Rust port already exists** (the
   `roaring` crate). This is useful as a reference but means the porting
   work is largely solved upstream of Rust.
3. **vavr** — legitimately problematic (high reflection for pattern matching,
   heavy use of type-erased generics). Even if JDK 21 were available, this
   would not be a clean port.
4. **minimal-json** — dead project; wouldn't port even as an exercise.
5. **java-diff-utils** — need to re-run with a non-rate-limited model to
   judge.

## Compared to round 11 (same rubric-neutral infrastructure, TestWright lens)

Round 11 ranked JSqlParser (8.55) / commons-imaging (7.75) / commons-compress
(7.65) / jsoup (7.35) / jgrapht (4.70) — all mature Apache-style projects
with rich bug histories and mid-sized codebases. **Not one of those is a
reasonable Rust-port target:**
- JSqlParser: JavaCC grammar (would be re-written, not ported), 4635 tests
  but the grammar is the whole point and it's machine-generated.
- commons-imaging: image format codecs, huge surface area, many external
  format specs.
- commons-compress: compression formats, similar.
- jsoup: HTML parser with quirks-mode, intentional messiness.
- jgrapht: graph algorithms, JDK 21.

The round-11 "viable" list is effectively disjoint from the round-12
"portability-reasonable" list. That confirms the user's hypothesis: **Scout
had been optimising for a different objective, and the top TestWright pick
(JSqlParser) is not the right Rust-port target.**

## Primary recommendation (under new rubric)

**No clean viable candidate in this batch.** If forced to pick the closest:

> **https://github.com/apache/commons-codec** (composite 7.35)
>
> Buildable under JDK 17, 18,837 tests passing (99.99%), single-module,
> maven. Fails the coverage gate only because upstream doesn't configure
> jacoco — fixable. Fails the testability_tractable gate only because of
> static lookup tables the regex flags — benign for Rust porting.
>
> **Pilot experiment to validate**: pick one self-contained codec (e.g.
> `Base64`) from commons-codec and port just that class to Rust. If the
> ported Rust version can pass the existing Java `Base64Test` when both are
> exercised against the same input vectors, the approach works.

## Actionable follow-ups

1. **Rubric tighten/loosen** (envelope-clean, prompt-only):
   - Split `coverage_gap.satisfied` into `coverage_measured AND ≥60%`
     (false only when measured-and-low), so infra-gap doesn't force False.
   - De-emphasize `static_state_density` in `testability_tractable`; weight
     `reflection_density` more heavily instead.
   - Add Phase B.5 robustness: the agent currently can't decode base64
     pom.xml content — returns `runtime_deps=unknown` for all 4 repos that
     completed. Either give it a decoded-content query pattern or a
     dedicated micro-tool (the latter is out-of-envelope).
2. **Infrastructure**: upgrade `scout-builder` to ship JDK 21 alongside
   JDK 17 (unblocks vavr, jgrapht, and any modern codebase). Would be a
   docker/Dockerfile.builder edit.
3. **Candidate list**: the 5 hand-picked repos are mostly wrong for the
   specific goal. A better Rust-port shortlist would include:
   - **commons-codec** submodules only (e.g. the Base64 package alone),
     not the whole library
   - **Protobuf-java** primitives (varint, zigzag) — pure algo
   - **OkHttp's platform-independent bits** (URL parsing, though OkHttp
     has a Rust-equivalent already: `reqwest`/`hyper`)
   - **Small crypto utilities** (e.g. BouncyCastle light-weight API
     selected ciphers — but BC is massive and has dependency issues)
   - **Specialized smaller libs** never evaluated: `hamcrest` matchers,
     `mustache.java`, `ini4j` — all zero-dep

4. **Pilot**: the only way to ground-truth "is this portable" is to port
   one class and see. Round 13 should run scout's pilot tool (`bead uc6`)
   against commons-codec `Base64` or similar.

## Caveats

- 1 of 5 repos had no scorecard due to transient LLM 429 on OpenRouter
  free tier. java-diff-utils remains an open question.
- Under this rubric, a "viable=False" result is informative rather than a
  rejection — the rubric is intentionally strict. The ranking above tells
  you which repos came closest, not a clean go/no-go.
- No pilot runs (SPEC § 9.4, bead `uc6`) yet. This memo is exploratory.
- No adversarial pass (V2.5) — pure proposer run, like round 11.

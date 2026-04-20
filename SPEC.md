# Investigation Harness: Specification

**Project codename:** *Scout*
**Version:** 0.1 (draft)
**Pattern:** Harnessing the Harness — Investigation variant
**Purpose:** Select a Java repository as the target for the downstream test-generation harness (working codename *TestWright*), by evaluating candidate repositories against quantitative and qualitative criteria.

---

## 1. Scope and Non-Goals

### 1.1 In scope
Scout evaluates Java repositories as candidate targets for an agentic test-generation project. For each candidate repository, Scout produces a structured scorecard covering build tractability, coverage gap, testability, bug history, maintainer responsiveness, and pilot-run feasibility. Scout ranks candidates and produces a selection memo identifying a primary target and a backup.

### 1.2 Out of scope
Scout does **not** evaluate repositories for purposes other than test-generation target selection. Scout does **not** produce general-purpose codebase analyses, architectural reviews, or security audits. Scout does **not** perform the test generation itself — that is TestWright's responsibility. Scout does **not** evaluate non-Java repositories in this iteration (future variants may extend to other ecosystems).

### 1.3 Design discipline
Scout is a **narrow specialized agent**. Scope creep toward "general repo analyzer" is an explicit anti-goal. Any feature request that does not directly improve target-selection accuracy for Java test-generation is rejected. The narrowness is load-bearing: it is what makes the cost model work and what keeps the verifier authoritative.

---

## 2. Architecture

### 2.1 Component overview
Scout follows the Harnessing the Harness pattern with a synchronous IPC channel:

- **Student agent (Scout-Student):** a specialized agent that evaluates one repository at a time, producing a structured scorecard. Runs on a cheaper model (Haiku-class or equivalent).
- **Teacher agent (Scout-Teacher):** a supervisory agent with stronger reasoning, invoked for escalations and between-round updates. Runs on a frontier model (Opus-class).
- **Channel:** synchronous IPC channel for mid-task escalation from student to teacher, with structured directives (`fix` / `skip` / `stop`) flowing back.
- **Verifier pipeline:** deterministic checks that validate the student's scorecard output against structural, plausibility, and sampled-correctness criteria.
- **State ledger:** versioned record of student state (prompts, tool configs, heuristics) per round, plus full interaction logs.
- **Regression suite:** a set of canonical repositories with known-good scorecards, run against every student update to detect capability regression.

### 2.2 Process topology
Each repository evaluation runs as a single student invocation. The student executes in an isolated container with network egress restricted to the repository's source host, Maven Central, and the teacher channel. The student writes its scorecard to a structured JSON output; the verifier pipeline runs after completion; failures route to the teacher for between-round updates.

### 2.3 Deployment environment
Scout runs on AmEx on-prem infrastructure using the standard AWS Bedrock/Vertex integration already established for Claude Code. Containerization uses the existing sandbox patterns. No novel deployment requirements.

---

## 3. Student Agent Specification

### 3.1 Input
A single JSON object per invocation:
```
{
  "repo_url": "https://github.com/apache/commons-imaging",
  "target_modules": ["imaging-formats-tiff", "imaging-formats-png"] | null,
  "evaluation_id": "scout-eval-2026-04-20-001"
}
```
`target_modules` is optional. When null, the student evaluates all submodules and selects the most promising for pilot analysis.

### 3.2 Output
A structured scorecard with all fields populated (no nulls unless explicitly permitted):

```json
{
  "evaluation_id": "string",
  "repo_url": "string",
  "repo_metadata": {
    "name": "string",
    "stars": "integer",
    "downloads_last_12mo": "integer | null",
    "primary_license": "string",
    "last_commit_date": "ISO 8601 date"
  },
  "build": {
    "build_system": "maven | gradle | sbt | other",
    "jdk_version_required": "string",
    "clean_build_time_seconds": "integer",
    "clean_build_succeeded": "boolean",
    "build_log_path": "string",
    "build_issues_encountered": ["string"]
  },
  "tests": {
    "test_run_succeeded": "boolean",
    "test_count": "integer",
    "test_pass_rate": "float",
    "flaky_tests_observed": ["string"],
    "test_run_time_seconds": "integer"
  },
  "coverage": {
    "tool_used": "jacoco | cobertura | other",
    "line_coverage_percent_overall": "float",
    "branch_coverage_percent_overall": "float",
    "per_module_coverage": [
      {
        "module": "string",
        "loc": "integer",
        "line_coverage": "float",
        "branch_coverage": "float"
      }
    ]
  },
  "bug_history": {
    "closed_bug_issues_24mo": "integer",
    "bug_fix_commits_24mo": "integer",
    "sampled_bug_fixes": [
      {
        "commit_sha": "string",
        "commit_message_excerpt": "string",
        "files_changed": ["string"],
        "plausibly_test_catchable": "boolean",
        "rationale": "string"
      }
    ]
  },
  "maintainer_activity": {
    "commits_last_12mo": "integer",
    "distinct_committers_12mo": "integer",
    "median_pr_merge_time_days": "float | null",
    "last_release_date": "ISO 8601 date | null"
  },
  "testability_signals": {
    "reflection_density": "low | medium | high",
    "static_state_density": "low | medium | high",
    "external_service_dependencies": ["string"],
    "thread_sleep_count": "integer",
    "filesystem_assumptions": "low | medium | high"
  },
  "score": {
    "build_tractability": "integer 0-10",
    "coverage_gap_value": "integer 0-10",
    "testability": "integer 0-10",
    "bug_history_richness": "integer 0-10",
    "maintainer_responsiveness": "integer 0-10",
    "composite": "float"
  },
  "recommendation": {
    "viable_target": "boolean",
    "recommended_submodule": "string | null",
    "notes": "string",
    "estimated_coverage_delta_achievable": "float | null"
  },
  "metadata": {
    "student_version": "string",
    "evaluation_duration_seconds": "integer",
    "escalation_count": "integer",
    "errors_encountered": ["string"]
  }
}
```

### 3.3 Tooling surface
The student has access to a fixed toolset (no arbitrary code execution beyond this surface):

- `git_clone(url)` — clones into a fresh container workspace
- `run_build(system, args)` — executes `mvn`, `gradle`, etc., with timeout and log capture
- `run_tests(module?)` — executes test suite, returns pass/fail counts and timing
- `run_coverage(tool, module?)` — executes JaCoCo/equivalent, parses report, returns structured coverage data
- `git_log_analyze(since, pattern)` — analyzes commit history for bug-fix commits matching patterns
- `github_api_query(endpoint)` — read-only access to GitHub API for stars, PRs, issues
- `static_analysis(module, metric)` — runs specific static analysis to measure reflection density, static state density, etc. (backed by existing tools like PMD, SpotBugs for specific queries)
- `escalate(reason, context)` — synchronous escalation to Scout-Teacher
- `finalize_scorecard(scorecard)` — writes final JSON, ends invocation

The student may **not** execute arbitrary shell commands, install packages, modify build configurations, or communicate outside the container except through the permitted tools.

### 3.4 Escalation policy
The student escalates via channel when:

- A build system is encountered that the student's current toolset does not handle (e.g., sbt, bazel, custom)
- Coverage extraction fails due to non-standard report format or configuration
- Bug-fix commit identification yields zero results (suggesting the project uses a non-standard commit convention)
- The repository's structure does not match the student's expectations (e.g., no clear submodule boundaries, monorepo-style organization)
- The evaluation exceeds a configurable time budget (default: 30 minutes per repo)

Each escalation includes the specific failure mode, the partial scorecard state at the point of escalation, and the last N tool calls for context.

### 3.5 Escalation budget
Per-repo escalation budget: **3 escalations**. Exceeding the budget triggers `stop` (scorecard marked as incomplete, recorded for teacher review but not used in ranking). This prevents the student from degenerating into a thin wrapper around the teacher on pathological inputs.

---

## 4. Teacher Agent Specification

### 4.1 Invocation modes
The teacher is invoked in two modes:

**Inline (synchronous, via channel):** The student escalates mid-task; the teacher diagnoses and returns a structured directive:
- `fix`: apply a specific patch to the student's current invocation (e.g., "use `sbt test:test` instead of `sbt test` for this project"). The fix applies only to the current run.
- `skip`: skip the current probe and continue (e.g., "bug-fix detection is not going to work on this repo, continue without that field"). Scorecard notes the skip.
- `stop`: abort the current evaluation; the repo is marked as ineligible, with reason recorded.

**Between-round (asynchronous):** After each batch of evaluations, the teacher reviews:
- Escalation log (what the student got stuck on, how often)
- Verifier failures (where the scorecards were rejected)
- Canary regression results (did this round degrade performance on known-good repos?)

The teacher emits between-round updates to the student's prompt, tool configurations, or heuristic parameters, bounded by the approved modification envelope (Section 6).

### 4.2 Teacher's diagnostic responsibilities
For each escalation, the teacher produces an **escalation record**:
```
{
  "escalation_id": "string",
  "student_version": "string",
  "repo_url": "string",
  "failure_category": "build_system | coverage_tool | bug_mining | structure | timeout | other",
  "failure_subcategory": "string",
  "diagnosis": "string",
  "directive_issued": "fix | skip | stop",
  "fix_applied": "string | null",
  "should_distill": "boolean",
  "distillation_note": "string | null"
}
```
`should_distill = true` indicates the teacher judges this failure pattern is likely to recur and should be absorbed into the student's next version (via prompt update, tool addition, or heuristic change).

### 4.3 Between-round update scope
The teacher may modify the student within a bounded envelope (see Section 6 for the enforcement mechanism). Permitted modifications include: prompt additions for new build systems, prompt additions for new coverage tool formats, heuristic adjustments for bug-fix commit pattern matching, tool parameter updates, and addition of known-project-specific handlers. Prohibited modifications include: adding new tool capabilities outside the approved tool surface, expanding the output schema, changing the escalation protocol, changing the verifier pipeline.

---

## 5. Verifier Pipeline

### 5.1 Verifier layers
The verifier runs after each student invocation completes and before the scorecard is accepted into the results set. All layers must pass.

**Layer 1: Schema validation.** Scorecard JSON must match the schema in 3.2. All required fields populated. Types correct. Enumerated values within allowed sets.

**Layer 2: Plausibility checks.** Numeric fields within plausible ranges. Test pass rate between 0 and 1. Coverage percentages between 0 and 100. Commit counts non-negative. Dates parseable and not in the future. LOC counts consistent between per-module sum and overall figure (within tolerance).

**Layer 3: Trace verification.** Each scorecard claim must be traceable to a tool call in the student's execution log. No scorecard field may be populated without evidence of the tool invocation that produced it. This is the primary defense against the student fabricating fields when a tool call fails.

**Layer 4: Sampled correctness.** For 10% of scorecards (randomly sampled), a verification probe independently re-runs a subset of the student's measurements (e.g., re-cloning the repo and re-running coverage) and confirms the results match within tolerance. Divergences flag the scorecard for teacher review and potentially indicate student capability drift.

**Layer 5: Canary regression.** A fixed set of 5 reference repositories with known scorecards is run against every student version. Any degradation on canary results vs. the last-known-good version blocks the update.

### 5.2 Verifier authority
The verifier's judgment is authoritative. A student scorecard that fails verification is rejected regardless of how plausible it appears. The verifier is external to both student and teacher; the teacher cannot override verifier decisions.

---

## 6. Change Management for Student Updates

### 6.1 Modification envelope
Between-round updates from the teacher to the student are constrained to a pre-approved set of modification types:

- Prompt additions (new sections appended to the student's system prompt)
- Heuristic parameter changes (e.g., threshold for "high" reflection density)
- Tool parameter changes (e.g., default timeout for `run_build`)
- Addition of project-specific handlers (small per-project rules, e.g., "for repos matching `apache/commons-*`, use the Apache standard coverage report path")

### 6.2 Prohibited modifications
- Changes to the tool surface itself (new tools, removed tools, modified tool signatures)
- Changes to the scorecard schema
- Changes to the escalation protocol
- Changes that could affect security boundaries (network policy, container permissions, credential handling)

### 6.3 Enforcement
The modification envelope is enforced **mechanically**, not by policy. The teacher emits update diffs into a specific file path with a specific schema; a pre-commit hook validates that the diff stays within the envelope; diffs outside the envelope are rejected and surfaced for human review. The teacher cannot bypass this check.

### 6.4 Approval workflow
- **Within-envelope updates:** auto-applied after passing the canary regression suite.
- **Out-of-envelope updates:** require human review and explicit approval. This is a rare event, expected once per month or less after the first few rounds.
- **Security-relevant updates:** always require human approval regardless of envelope status.

### 6.5 Versioning
Every student state is content-hashed and recorded. Each scorecard is stamped with the student version that produced it. Historical reconstruction is always possible: "what did student version v47 produce for repo X on date Y?" is answerable from the ledger.

---

## 7. Observability

### 7.1 Metrics
Tracked per round and over time:

- **Evaluation throughput:** repos evaluated per hour, student cost per repo, teacher cost per repo
- **Escalation rate:** escalations per repo, broken down by failure category
- **Escalation rate trend:** escalation rate over successive student versions (should decrease if distillation is working)
- **Verifier pass rate:** fraction of scorecards passing verification on first submission
- **Canary stability:** canary regression suite results per student version
- **Teacher cost ratio:** teacher compute cost / total compute cost (should decrease as student improves)

### 7.2 Dashboards
A single dashboard displays the capability distillation trajectory: escalation rate per round, verifier pass rate per round, teacher cost ratio per round. This is the primary "is the harness working" signal.

### 7.3 Alerting
- Escalation rate increase over rolling window → flag for teacher review
- Canary regression → block student update, alert on-call
- Teacher cost spike → alert, pause evaluations
- Verifier pass rate drop → flag for teacher review

### 7.4 Audit trail
Every evaluation produces:
- Full student execution log (all tool calls, all escalations, all teacher responses)
- Final scorecard
- Verifier results (each layer's output)
- Student version hash

Retention: indefinite for the life of the project. This is the basis for both publication and internal audit.

---

## 8. Operational Constraints

### 8.1 Cost caps
- Per-repo evaluation budget: $5 of teacher inference cost, $0.50 of student inference cost. Exceeding these caps triggers automatic termination of the evaluation.
- Per-round budget: $500 total across all evaluations. Exceeding this blocks further evaluations until human review.

### 8.2 Rate limits
- Maximum 10 concurrent evaluations.
- Maximum 30 escalations per hour across the whole system (circuit breaker against escalation cascades).
- Maximum 1 between-round update per day (limits the rate of student drift).

### 8.3 Privacy and compliance
Scout operates on public GitHub repositories only. No AmEx internal code is evaluated by Scout. All network egress is logged. All LLM interactions are logged. No credentials are stored in the student's accessible state.

### 8.4 Failure modes and responses
- **Prompt injection via repo content:** a malicious README or commit message attempting to instruct the student. Mitigation: student's system prompt explicitly treats repo content as untrusted data; any instruction-following from repo content triggers an escalation.
- **Capability decay:** a between-round update that silently degrades performance on an unwatched repo class. Mitigation: canary regression suite (5.1 Layer 5) plus periodic human spot-checks.
- **Teacher-student collusion:** both agree a scorecard is valid, verifier disagrees. Mitigation: verifier authority is absolute (5.2); divergence between teacher approval and verifier pass flags for human review.
- **Cost runaway:** a pathological repo triggering repeated escalation. Mitigation: per-repo cost cap (8.1) plus per-repo escalation budget (3.5).

---

## 9. Evaluation Methodology

### 9.1 Input set
The investigation evaluates a longlist of 30 Java repositories, drawn from:
- Maven Central top-downloaded Java artifacts (prioritizing libraries over frameworks)
- Apache top-level Java projects
- Widely-used format parsers, math libraries, protocol codecs, and utility libraries
- Candidates surfaced in prior analysis (Commons Imaging, JSqlParser, Commons Compress, jOOR, JGraphT, Jackson dataformat modules, etc.)

### 9.2 Ranking function
Scorecards are ranked by composite score, calculated as a weighted combination of the five subscores in 3.2 (`score` block). Initial weights:
- Build tractability: 0.25 (if it doesn't build, nothing else matters)
- Coverage gap value: 0.25 (the actual opportunity)
- Testability: 0.20 (will the harness succeed?)
- Bug history richness: 0.15 (is the evaluation-against-real-bugs story viable?)
- Maintainer responsiveness: 0.15 (will PRs land?)

Weights are configurable and subject to revision after initial results.

### 9.3 Selection output
Scout produces a **selection memo** covering:
- Top 5 candidates with their full scorecards
- Composite score rationale
- Primary recommendation and backup
- Risks and mitigations for the primary recommendation
- Estimated TestWright runtime and expected coverage delta on the primary target

### 9.4 Pilot integration
Before final selection commits, the top 2 candidates undergo a **pilot run** using a general-purpose agent (Claude Code or equivalent — not Scout, which doesn't generate tests) against one low-coverage class. Pilot results feed into the final selection memo as an empirical check on the harness's likely performance.

---

## 10. Success Criteria

Scout is successful if, at the end of the investigation phase:

1. **Selection quality:** A primary target is selected, and a pilot run on that target confirms the harness can plausibly achieve meaningful coverage and mutation-score improvements within the project's time budget.

2. **Capability distillation evidence:** Escalation rate on successive batches of repos shows a measurable decrease, demonstrating the student is absorbing patterns the teacher corrected in earlier rounds. Target: >40% reduction in escalation rate from first batch to last batch.

3. **Operational soundness:** No security incidents, no out-of-envelope modifications applied, all cost caps respected, complete audit trail available for every evaluation.

4. **Reusability:** The trained Scout-Student can be invoked for future target-selection tasks (within Java scope) without further development. This is the durability test.

5. **Paper contribution:** The escalation curves, distillation data, and verifier pass rates from Scout constitute a publishable empirical dataset in their own right, demonstrating the Harnessing the Harness pattern on a task distinct from test generation.

---

## 11. Timeline

- **Week 1:** Infrastructure setup — container environment, tool surface implementation, verifier pipeline, state ledger, baseline student prompt.
- **Week 2:** First batch of 10 repos, with heavy teacher involvement. Escalation log analysis. First between-round update.
- **Week 3:** Second batch of 10 repos. Between-round update. Third batch begins.
- **Week 4:** Third batch of 10 repos. Canary regression suite established from early results. Pilot runs on top 2 candidates. Selection memo drafted.
- **End of week 4:** Selection memo delivered; target chosen; TestWright design begins on the selected target.

Total calendar time: 4 weeks. Total person-time: roughly 6–8 person-weeks given the infrastructure work.

---

## 12. Open Questions

- Should Scout evaluate maintainer responsiveness by opening a trivial PR and measuring response time? (Probably not — raises ethical and interference concerns with real projects. Stick to passive observation of PR history.)
- Should the verifier's sampled-correctness layer (4.1 Layer 4) re-run 10% of evaluations or use a more sophisticated sampling strategy weighted toward high-uncertainty scorecards? (Defer to after first batch.)
- Should bug-fix commit detection use a supervised classifier trained on commit messages, or rule-based heuristics? (Start rule-based; revisit if precision is inadequate.)
- What is the right granularity for "project-specific handlers" in the modification envelope? (Start narrow — per-org prefixes like `apache/commons-*`. Widen if needed.)

---

## 13. Related Work and Prior Art

Scout builds on the Harnessing the Harness pattern as demonstrated in War Rig (COBOL documentation) and the AmEx internal MCP proxy middleware work. The specific application to target selection for agentic projects is, to our knowledge, novel and produces a research artifact distinct from test-generation results.

Related external work: Defects4J (bug benchmark for test generation evaluation), SWE-bench (agentic evaluation on GitHub issues), and the broader literature on agentic software engineering evaluation. Scout differs in that it is task-specific (target selection, not general-purpose evaluation) and is itself the subject of the evaluation (capability distillation on the selection task).

---

## Appendix A: Example Escalation

Student encounters `apache/commons-compress`. Build is Maven, succeeds. Coverage extraction via JaCoCo succeeds. Bug-fix commit detection finds 47 commits matching `^\[COMPRESS-\d+\]` but the heuristic doesn't recognize this pattern.

Student escalates with:
```
{
  "escalation_id": "scout-esc-2026-04-20-047",
  "reason": "bug_mining_pattern_not_matched",
  "context": {
    "repo": "apache/commons-compress",
    "commits_scanned": 2341,
    "matches_default_pattern": 0,
    "potential_pattern_observed": "^\\[COMPRESS-\\d+\\]",
    "sample_commits": ["...", "..."]
  }
}
```

Teacher diagnoses: Apache projects use `[PROJECT-###]` issue-key prefix conventions. Returns:
```
{
  "directive": "fix",
  "fix": "add pattern '^\\[([A-Z]+-\\d+)\\]' to bug-fix detection heuristics for this run",
  "should_distill": true,
  "distillation_note": "Apache projects universally use [PROJECT-###] prefix. Add this to default heuristics in next student version."
}
```

Student resumes, identifies bug-fix commits correctly, completes scorecard. Between-round update absorbs the Apache pattern into the default heuristic. Subsequent Apache projects do not escalate on this category.

---

## Appendix B: Glossary

- **Student version:** A content-hashed snapshot of the student's full configuration (prompts, heuristics, tool parameters, project-specific handlers).
- **Canary:** A reference repository with a known-good scorecard, used to detect capability regression.
- **Envelope:** The pre-approved set of modifications the teacher may apply to the student without human review.
- **Scorecard:** The structured JSON output produced by the student for each repository.
- **Composite score:** The weighted combination of the five subscores used for final ranking.
- **Pilot run:** A small test of TestWright-style test generation on a candidate repo, done before final selection commits.

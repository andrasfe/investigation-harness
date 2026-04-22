# Scout — capability distillation trajectory

_Generated 2026-04-22T02:18:11Z_

This file is the paper-grade artifact for SPEC §10 success criterion 5: 
evidence that the student gets better with each round.

## Round-by-round metrics

| tag | evals | accepted | pass rate | adversarial refutations | taught-rule skips (cum) |
|-----|-------|----------|-----------|-------------------------|-------------------------|
| round-1 | 1 | 0 | 0% | 2 | 0 |
| round-2 | 0 | 0 | — | 0 | 0 |
| round-3 | 1 | 1 | 100% | 1 | 0 |
| round-4 | 1 | 1 | 100% | 1 | 0 |
| round-5 | 1 | 1 | 100% | 0 | 0 |
| round-6 | 5 | 1 | 20% | 4 | 0 |
| round-7 | 4 | 4 | 100% | 3 | 0 |
| round-8 | 3 | 3 | 100% | 1 | 0 |
| round-9 | 14 | 12 | 86% | 12 | 0 |
| round-10 | 5 | 5 | 100% | 3 | 0 |
| round-11 | 5 | 5 | 100% | 0 | 0 |
| round-13 | 9 | 9 | 100% | 0 | 0 |

### Trends

- verifier pass-rate:     `▁ ███▂██▇███`
- adversarial refutations: `▂▁▂▂▁▃▃▂█▃▁▁`  (should trend **down** if the student is learning)

### Top rejection reasons per round
**round-1**
- ×1  plausibility: tests.test_run_succeeded=true but test_count=0 (no tests act
- ×1  plausibility: build.clean_build_succeeded=true but build_system='other' (r
- ×1  adversarial: viable_target=true but adversarial judge REFUTED 2 claim(s):

**round-6**
- ×3  plausibility: recommendation.viable_target=true requires tests.test_run_su
- ×3  plausibility: maintainer_activity.last_release_date unparseable
- ×1  adversarial: viable_target=true but adversarial judge REFUTED 1 claim(s):

**round-9**
- ×2  trace: build.clean_build_succeeded populated but no run_build tool 
- ×2  trace: build.build_system populated but no run_build tool call in t
- ×2  trace: tests.test_run_succeeded populated but no run_tests tool cal

## Interpretation

- **Pass rate rising** → the between-rounds teacher is successfully
  encoding its corrections into prompt edits, project handlers, or
  taught rules. This is the capability-distillation curve.
- **Adversarial refutations falling** → the Proposer's factual accuracy
  is improving; the Challenger finds fewer real disagreements.
- **Taught-rule skips rising while escalations fall** → the student is
  absorbing patterns the teacher corrected in earlier rounds and no
  longer needs to round-trip for them.

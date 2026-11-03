# 0003 — Deterministic evals gate the deploy; the LLM judge informs and is calibrated by humans

## Context

A change can make answers worse, or keep them right while costing more. An
LLM judge can grade any answer but can itself be wrong; a fixed test suite
is trustworthy but only covers its questions.

## Decision

- **Gate:** after every deploy, 8 questions about the platform, with answers
  checkable in its docs, run
  against the live Worker; the run fails if the pass rate falls below
  `min_pass_rate` or the average cost per task rises above
  `max_avg_cost_usd` (`evaluation/baseline.json`).
- **Judge:** a sample of real runs is graded in the background on
  on-purpose, grounded and helpful; it never blocks.
- **Humans:** 👍 / 👎 in `/admin`; judge/human agreement is shown next to the
  judge score.

## Consequences

- ➕ The gate is free, reproducible and hard to argue with.
- ➕ Quality is visible on real traffic, not only on the suite.
- ➖ Measured with Llama 3.3 70B: arithmetic slips and run-to-run variance
  (different tasks fail on different runs), so the minimum is 75 %; the judge
  missed a wrong total once. Both are documented limits, not hidden ones.
- ➕ The gate catches agent-engineering problems, not only prompt ones: it is
  how the tool-calling loop of Llama 3.3 was found and fixed (architecture §4).

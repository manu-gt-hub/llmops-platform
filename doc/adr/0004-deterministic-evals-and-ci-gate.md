# 0004 — Deterministic evals and a quality + cost gate in CI

**Status:** Accepted

## Context

An agent change can make answers worse, or keep them right while doubling
the cost (more steps, more tokens). Both are regressions and both must be
caught before merge.

## Decision

- The suite ([agent/tasks.json](../../agent/tasks.json)) has questions with
  checkable answers: an expected number (tolerance 0.01) and/or required and
  forbidden keywords. No LLM judge.
- `python -m agent.run_tasks --gate` compares the run with
  [evals/baseline.json](../../evals/baseline.json): fails if the pass rate is
  below `min_pass_rate` or if the average cost per task exceeds
  `avg_cost_usd × (1 + cost_tolerance)`.
- Results are stored in `eval_results` with the release, so the AgentOps
  dashboard shows quality per version.

## Consequences

- ➕ Grading is free, instant and reproducible; it is safe to block merges on.
- ➕ Cost is a first-class test result, not something found on the invoice.
- ➖ Only questions with checkable answers can be evaluated. Open-ended
  quality would need an LLM judge, kept out of the gate.
- ➖ The baseline must be updated in the same PR when a change is meant to
  change cost.

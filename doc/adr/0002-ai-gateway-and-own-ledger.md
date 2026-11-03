# 0002 — AI Gateway for per-request observability, our own D1 ledger for budgets and scoring

## Context

We need calls, tokens, cost and errors per request, but also per agent and
run, budgets that stop spending, and grades. AI Gateway gives the first part
natively (dashboard, logs, metadata, feedback on logs) but has no spend
limits, and its Evaluations feature is deprecated.

## Decision

- Every LLM call goes through AI Gateway (`default`, auto-created), tagged
  with `agent`, `run_id` and `source` metadata. Judge scores and human
  feedback are attached to the request's log with `patchLog`.
- The Worker also writes one row per call to D1 with tokens, the exact
  neurons Workers AI reports, cost and latency. Budgets, the admin dashboard
  and the monitor read this ledger.

## Consequences

- ➕ Two views that agree: Cloudflare's per request, ours per agent / run / day.
- ➕ Budgets are exact and enforced before the call.
- ➖ Paid providers appear in AI Gateway only with a gateway token
  (`CF_AIG_TOKEN`), because the auto-created gateway requires authentication;
  without it they are still in our ledger.

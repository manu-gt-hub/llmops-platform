# 0002 — PostgreSQL as the cost ledger, Prometheus for live metrics

**Status:** Accepted

## Context

FinOps questions are relational and historical: spend per project per month,
cost per *successful* run, cost before and after a release, joins with
budgets. Operational questions are about rates right now: requests per
second, p95 latency, error rate.

## Decision

- **PostgreSQL** stores one row per LLM call, run, step, eval result and
  deployment. It is the source of truth for money; Grafana queries it with SQL.
- **Prometheus** scrapes `/metrics` for live counters, latency histograms and
  `llmops_build_info`.
- Budgets are checked against PostgreSQL (exact, month to date), not against
  Prometheus counters (which reset on restart).

## Consequences

- ➕ Exact, auditable costs; any new FinOps question is one SQL query.
- ➕ Demo data can be backfilled into PostgreSQL (Prometheus cannot ingest
  history), so the dashboards are useful on first start.
- ➖ Two stores. Live panels (Prometheus) only show real traffic.
- ➖ The budget check is a query per request. Fine at this scale; at high
  volume, cache month-to-date spend per project for a few seconds.

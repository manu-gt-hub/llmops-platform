# Architecture decision records

Short records of the decisions that shape the platform: context, decision,
consequences. New decisions get the next number; superseded ones stay, marked
as such.

| # | Decision | Status |
|---|---|---|
| [0001](0001-own-gateway.md) | Build a thin gateway instead of adopting LiteLLM / Langfuse | Accepted |
| [0002](0002-postgres-and-prometheus.md) | PostgreSQL as the cost ledger, Prometheus for live metrics | Accepted |
| [0003](0003-dashboards-as-code.md) | Grafana dashboards generated from Python | Accepted |
| [0004](0004-deterministic-evals-and-ci-gate.md) | Deterministic evals and a quality + cost gate in CI | Accepted |
| [0005](0005-mock-provider-for-ci.md) | Mock provider for keyless end-to-end tests | Accepted |

Back to [architecture](../architecture.md).

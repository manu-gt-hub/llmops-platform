# Architecture decision records

Short records of the decisions that shape the platform: context, decision,
consequences. The earlier Docker/Python design (gateway + Postgres + Grafana)
is in the git history before v2.0.0.

| # | Decision |
|---|---|
| [0001](0001-cloudflare-worker.md) | One Cloudflare Worker on the free plan instead of a container stack |
| [0002](0002-ai-gateway-and-own-ledger.md) | AI Gateway for per-request observability, our own D1 ledger for budgets and scoring |
| [0003](0003-evals-gate-judge-informs.md) | Deterministic evals gate each release; the LLM judge informs and is calibrated by humans |
| [0004](0004-guardrails-in-the-call-path.md) | Charter, budgets and guardrails in the single LLM call path, with simple detectors |
| [0005](0005-devsecops-baseline.md) | A DevSecOps baseline that costs nothing to run |

Back to [architecture](../architecture.md).

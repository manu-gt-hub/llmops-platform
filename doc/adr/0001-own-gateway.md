# 0001 — Build a thin gateway instead of adopting LiteLLM / Langfuse

**Status:** Accepted

## Context

We need per-call token and cost accounting, budgets, a per-run kill switch
and agent traces, for apps that use the official Anthropic and OpenAI SDKs.
Mature options exist: LiteLLM (proxy with spend tracking), Langfuse / Helicone
(LLM observability), and OpenTelemetry auto-instrumentation in the SDKs.

## Decision

Write a small pass-through gateway (~400 lines, FastAPI + httpx) that:

- forwards requests **unchanged** to the provider's native API (no
  translation layer between OpenAI and Anthropic formats);
- parses usage from JSON responses and SSE streams;
- prices with a versioned `pricing.yaml`;
- enforces budgets and per-run limits before forwarding;
- writes one row per call to PostgreSQL and emits Prometheus metrics and
  OpenTelemetry GenAI spans.

## Consequences

- ➕ Native passthrough means new API features (thinking, fallbacks, cache
  control, new tools) work on day one; the gateway only needs `model` and
  `usage`.
- ➕ Every number on the dashboards can be traced to code in this repo,
  which is the point of a reference platform.
- ➕ Guardrails live outside the agent, so a buggy agent cannot skip them.
- ➖ No provider fallback, load balancing or virtual keys; LiteLLM has them.
  In a production evaluation, LiteLLM (gateway) + Langfuse (traces/evals)
  should be compared against extending this.
- ➖ Pricing must be maintained by hand; mitigated by the unpriced-model
  flag, panel and alert.

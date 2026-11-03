# 0001 — One Cloudflare Worker on the free plan instead of a container stack

## Context

The platform has to run live, be cheap enough to leave on, and be simple to
explain. v1 was a Python gateway with PostgreSQL, Prometheus, Tempo and
Grafana in Docker: rich, but it needs a host to run on and five services to
operate. The sibling project `rag_system` already runs on Cloudflare's free
plan with Workers AI, D1 and GitHub Actions.

## Decision

Rewrite as one TypeScript Worker (same toolchain as `rag_system`): the agent,
the rules, the ledger (D1), the judge and the admin pages in one deployable
unit; Workers AI as the default model; Anthropic and OpenAI optional behind
the same interface (official SDKs).

## Consequences

- ➕ Live with a URL, zero servers, zero cost within the free allocation;
  deploy on every merge.
- ➕ One language and ~1,500 lines to read end to end.
- ➖ No Grafana / Prometheus / Tempo: dashboards are our `/admin` page plus
  AI Gateway and Workers Observability.
- ➖ Workers limits (CPU per request, 24k-token context of the default model)
  shape the agent: small tools, short prompts.

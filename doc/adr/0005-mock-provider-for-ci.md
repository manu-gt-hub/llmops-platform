# 0005 — Mock provider for keyless end-to-end tests

**Status:** Accepted

## Context

The most valuable test runs the real stack: SDK → gateway → provider →
usage, cost, traces, dashboards. With real providers it needs secrets (not
available in forks) and costs money on every push.

## Decision

[tools/mock_llm.py](../../tools/mock_llm.py) implements the Anthropic
Messages and OpenAI Chat Completions endpoints, JSON and streaming, with
realistic `usage` blocks. It behaves like a tool-using model (search,
calculate, answer from the task file) and loops forever on demand.
`docker-compose.mock.yml` points the gateway at it.

The CI e2e job uses it on every push; real-model evals run separately and
only when secrets exist.

## Consequences

- ➕ Every push tests the full path for free, including guardrails (loop,
  run cost) and every dashboard query.
- ➕ Anyone can try the agent locally without an API key.
- ➖ The mock can drift from the real APIs; the real-model eval workflow
  is the safety net.

# AGENTS.md — LLMOps Platform

Guide for coding agents (and humans) working on this repo. Architecture and
rationale: `doc/architecture.md` and `doc/adr/`.

## Map

| Path | Role |
|---|---|
| `gateway/proxy.py` | Pass-through to providers, guardrails, recording (tokens, cost, metrics, spans) |
| `gateway/usage.py` | Usage normalization for JSON and SSE (`input_tokens` = uncached) |
| `gateway/pricing.py`, `config/pricing.yaml` | Cost per call |
| `gateway/budgets.py`, `config/budgets.yaml` | Monthly budgets per project |
| `gateway/runs.py` | AgentOps API: runs, steps, evals, governance switches, labels |
| `gateway/governance.py`, `config/agents.yaml` | Agent charters and guardrails (checked in `proxy.py`) |
| `gateway/judge.py` | LLM-as-judge, sampled in the background when a run finishes |
| `gateway/console.py`, `gateway/templates/` | Agent console (server-rendered HTML, no JS) |
| `gateway/seed.py` | Deterministic 30-day demo data with a release story |
| `gateway/schema.sql` | Database schema, applied on every start (idempotent) |
| `agent/` | Demo agent: `llm.py` (SDK adapters), `runner.py` (loop + guardrails), `tools.py`, `evals.py`, `run_tasks.py` (CLI) |
| `tools/build_dashboards.py` | Source of the Grafana dashboards |
| `tools/mock_llm.py` | Fake Anthropic/OpenAI API for keyless e2e |

## Rules

- **Dashboards**: edit `tools/build_dashboards.py`, then run
  `python tools/build_dashboards.py`. Never edit the JSON by hand; CI checks it.
- **New column / table**: `CREATE ... IF NOT EXISTS` or `ALTER ... ADD COLUMN
  IF NOT EXISTS` in `schema.sql`; keep the `synthetic` flag on fact tables.
- **New model**: add it to `config/pricing.yaml` (per 1M tokens).
- **Guardrails that limit money or what an agent may do belong in the
  gateway**, not in the agent. New content guardrails start in `monitor`.
- **New agent**: register it in `config/agents.yaml` or the gateway refuses it.
- **Telemetry must never break a request**: recording errors are logged.
- **Agent cost changes**: if a change is meant to alter cost or quality,
  update `evals/baseline.json` in the same PR.

## Checks

```bash
ruff check . && python -m pytest && python tools/build_dashboards.py --check
docker compose -f docker-compose.yml -f docker-compose.mock.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.mock.yml run --rm agent --gate
python tools/check_dashboards.py --require-data
```

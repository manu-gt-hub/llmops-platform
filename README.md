# LLMOps Platform — DevOps, FinOps and AgentOps for LLM apps

A reference platform that puts an **LLM gateway** between your apps and the
model providers (Anthropic, OpenAI) and turns every call into data: tokens and
cost by date, model and project; budgets that actually stop spending; traces
and success rates of agent runs; and which release is deployed and what it
changed.

It ships with a demo **research agent**, **30 days of demo data** so the
dashboards tell a story on first start, and CI/CD that tests everything
end to end without API keys.

📐 Architecture, diagrams and decisions: [doc/architecture.md](doc/architecture.md)
(also served at http://localhost:8090/architecture) ·
[ADRs](doc/adr/README.md) · [Runbook](doc/runbook.md)

## Quick start (Docker)

Requirement: [Docker](https://docs.docker.com/get-docker/) with `docker compose`.

```bash
git clone <repo> && cd llmops-platform
cp .env.example .env          # Windows PowerShell: copy .env.example .env
# Optional, to send real traffic: set ANTHROPIC_API_KEY and/or OPENAI_API_KEY
docker compose up -d --build
```

Open **http://localhost:3000** (Grafana, no login needed to view). The home
dashboard shows the last release deployed, spend this month, tokens per day,
agent success rate and budgets. With no key at all, everything runs on demo
data.

Send real traffic through the gateway with the demo agent:

```bash
docker compose run --rm agent                               # task suite, graded
docker compose run --rm agent --ask "Which plan has SSO?"   # one question
docker compose run --rm agent --loop 300                    # traffic every 5 min
```

No key? Run the same stack against the built-in mock provider (free):

```bash
docker compose -f docker-compose.yml -f docker-compose.mock.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.mock.yml run --rm agent
```

| URL | What |
|---|---|
| http://localhost:3000 | Grafana: Overview, FinOps, AgentOps, Platform dashboards (`admin`/`admin` to edit) |
| http://localhost:8090/architecture | Architecture docs with diagrams |
| http://localhost:8090/docs | Gateway API (OpenAPI) |
| http://localhost:8090/version | Running version, commit and providers |
| http://localhost:9090 | Prometheus |

Use the **Data** selector on any dashboard to see `real` traffic, the `demo`
data, or both.

## Put your own app behind the gateway

No code change: point the official SDK at the gateway. The gateway holds the
real keys, so the app can use any placeholder key.

```bash
# Anthropic SDK                                   # OpenAI SDK
ANTHROPIC_BASE_URL=http://localhost:8090/p/my-app/anthropic
OPENAI_BASE_URL=http://localhost:8090/p/my-app/openai/v1
```

`/p/<project>/` attributes the spend to a project (or send an `X-Project`
header). Agents can add `X-Run-Id` and `X-Agent` to get per-run cost and the
per-run kill switch. For example, the sibling `rag_system` project shows up as
its own project by adding to its `.env` (from inside its container the host is
`host.docker.internal`):

```bash
OPENAI_BASE_URL=http://host.docker.internal:8090/p/rag-system/openai/v1
ANTHROPIC_BASE_URL=http://host.docker.internal:8090/p/rag-system/anthropic
```

## What each discipline gets

| | Features |
|---|---|
| **FinOps** | Per-call cost from a versioned price list ([config/pricing.yaml](config/pricing.yaml)), prompt-cache savings, cost per *successful* agent run, month-end forecast, monthly budgets per project enforced by the gateway (HTTP 402), alerts on budget threshold and unpriced models |
| **AgentOps** | Run / step / tool telemetry, stop reasons, loop detection, per-run cost limit, one OpenTelemetry trace per run across agent and gateway (Tempo), deterministic eval suite with results per release |
| **DevOps** | Version, commit and build date baked into the image and shown on `/version`, `/metrics` and the dashboards; release history as annotations on every chart; CI with lint, tests, dashboards-as-code check and a keyless end-to-end run; a quality + cost gate on PRs; tag-driven releases to GHCR |

## Repository layout

```
gateway/        LLM gateway (FastAPI): proxy, usage & pricing, budgets, AgentOps API, demo seed
agent/          Demo research agent: tools over a small handbook, run loop, evals
config/         pricing.yaml, budgets.yaml
evals/          baseline.json, the CI quality and cost gate
observability/  Prometheus, Tempo and Grafana provisioning (data sources, dashboards, alerts)
tools/          build_dashboards.py (dashboards as code), check_dashboards.py, mock_llm.py
doc/            architecture.md, ADRs, runbook
.github/        CI, real-model evals, release workflows
```

## CI/CD

| Workflow | When | What |
|---|---|---|
| [CI](.github/workflows/ci.yml) | push, PR | ruff, pytest, dashboards in sync; then the whole stack on the mock provider: agent suite with the gate, loop guardrail, every dashboard query |
| [Agent evals](.github/workflows/evals.yml) | PR touching agent/gateway/config | Suite against the real model; fails if pass rate or cost per task regress vs [evals/baseline.json](evals/baseline.json). Skipped with a warning without secrets |
| [Release](.github/workflows/release.yml) | tag `vX.Y.Z` (must match [VERSION](VERSION)) | Image with version/commit/date → `ghcr.io/<owner>/llmops-platform`, GitHub release |

Secrets for real-model evals: `ANTHROPIC_API_KEY` and/or `OPENAI_API_KEY`
(Settings → Secrets and variables → Actions).

## Development

```bash
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
ruff check . && python -m pytest                 # lint + unit tests (no Docker needed)
python tools/build_dashboards.py                 # after editing dashboards
docker compose up -d db                          # run the gateway outside Docker:
uvicorn gateway.main:app --port 8090             # (reads .env, Postgres on localhost:5433)
```

`make help` lists shortcuts for all of the above.

## Configuration

All settings are in [.env.example](.env.example). The most relevant:

| Variable | Default | |
|---|---|---|
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | — | Provider keys, read by the gateway only |
| `AGENT_PROVIDER` | auto | `anthropic` or `openai` for the demo agent |
| `ANTHROPIC_MODEL` / `OPENAI_MODEL` | `claude-opus-5-5` / `gpt-4o-mini` | Demo agent models |
| `MAX_RUN_COST_USD` | `0.50` | Per-run kill switch enforced by the gateway |
| `SEED_DEMO_DATA` | `true` | Load 30 days of synthetic data on first start |
| `GATEWAY_HOST_PORT` / `GRAFANA_HOST_PORT` | `8090` / `3000` | Published ports |

Reset everything, demo data included: `docker compose down -v`.

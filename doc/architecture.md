# Architecture

LLMOps Platform puts one component, the **LLM gateway**, between applications
and the model providers. Every call goes through it, so a single place can
answer these questions:

| Discipline | Question | Where the answer lives |
|---|---|---|
| **FinOps** | How many tokens do we spend, on what, and is it worth it? | `llm_requests`, budgets, FinOps dashboard |
| **AgentOps** | Do our agents work, and how do they fail? | `agent_runs`, `agent_steps`, `eval_results`, traces, AgentOps dashboard |
| **DevOps** | What is deployed, did the last release change anything, is it healthy? | `deployments`, `/metrics`, CI/CD, Platform dashboard |
| **Governance** | What may each agent do, did it stay within that, and was the answer good? | `config/agents.yaml`, `guardrail_events`, `judgements`, agent console |

Other docs: [ADRs](adr/README.md) · [Runbook](runbook.md)

## 1. System context

```mermaid
flowchart LR
    dev([Developer / CI]) -->|git push, tag| gh[GitHub Actions]
    gh -->|image| ghcr[(GHCR)]
    apps["Apps<br/>research-agent · rag-system · support-bot"] -->|official SDKs,<br/>base_url = gateway| gw[LLM Gateway]
    gw -->|real API key| anthropic[Anthropic API]
    gw -->|real API key| openai[OpenAI API]
    gw --> store[(Telemetry store)]
    viewer([Engineer / FinOps / Manager]) -->|dashboards, alerts| grafana[Grafana]
    grafana --> store
```

Applications keep using the official SDKs. Adopting the platform is a
configuration change, not a code change:

```python
Anthropic(base_url="http://gateway:8080/anthropic")      # or ANTHROPIC_BASE_URL
OpenAI(base_url="http://gateway:8080/openai/v1")         # or OPENAI_BASE_URL
# Attribution without headers: put the project in the URL
Anthropic(base_url="http://gateway:8080/p/rag-system/anthropic")
```

## 2. Containers

```mermaid
flowchart TB
    subgraph clients[Clients]
        agent["Demo research agent<br/>(agent/, Python)"]
        other["Any app using the<br/>Anthropic / OpenAI SDK"]
    end

    subgraph platform[docker compose]
        gw["LLM Gateway — FastAPI<br/>proxy · guardrails · cost · AgentOps API · judge<br/>/console · /version · /metrics · /architecture"]
        pg[("PostgreSQL 16<br/>requests, runs, steps,<br/>evals, deployments, budgets")]
        prom[("Prometheus<br/>live metrics")]
        tempo[("Tempo<br/>traces (OTLP)")]
        graf["Grafana 11<br/>4 dashboards + alert rules<br/>(provisioned as code)"]
    end

    agent -- "LLM calls + X-Run-Id + traceparent" --> gw
    agent -- "run / step / eval events" --> gw
    agent -- "spans (OTLP/HTTP)" --> tempo
    other --> gw
    gw -- "one row per call" --> pg
    gw -- "spans" --> tempo
    prom -- "scrape /metrics" --> gw
    graf --> pg
    graf --> prom
    graf --> tempo
    gw -- "HTTPS" --> providers[Anthropic · OpenAI]
```

| Container | Responsibility | Why this technology |
|---|---|---|
| Gateway | Pass-through proxy, usage parsing, pricing, budgets, run kill switch, agent charters and guardrails, judge, AgentOps API, agent console, build info | FastAPI + httpx async: streaming pass-through with little code; built-in OpenTelemetry ([ADR 0001](adr/0001-own-gateway.md)) |
| PostgreSQL | Durable per-request ledger, the FinOps source of truth | SQL is the natural language for cost questions; Grafana queries it directly ([ADR 0002](adr/0002-postgres-and-prometheus.md)) |
| Prometheus | Live throughput, latency histograms, build info | Standard for operational metrics and alerting on rates |
| Tempo | Distributed traces of agent runs | OTLP native, no extra database, links from Grafana |
| Grafana | Dashboards and alerts | Everything provisioned from the repo ([ADR 0003](adr/0003-dashboards-as-code.md)) |

The agent console is part of the gateway (server-rendered pages under
`/console`), not a separate service: one less thing to deploy, and it reads
the same tables.

## 3. Gateway request path

```mermaid
sequenceDiagram
    autonumber
    participant C as Client SDK
    participant G as Gateway
    participant DB as PostgreSQL
    participant P as Provider

    C->>G: POST /anthropic/v1/messages<br/>X-Project, X-Run-Id, traceparent
    G->>DB: month-to-date spend of project, spend of run
    alt budget or run limit reached
        G-->>C: 402 budget_exceeded / run_cost_limit
    else allowed
        G->>P: same body, real API key<br/>(OpenAI streams: include_usage added)
        P-->>G: JSON or SSE stream
        G-->>C: response, streamed through unchanged
        Note over G: parse usage (JSON or SSE events)<br/>cost = tokens × pricing.yaml
        G->>DB: INSERT llm_requests (tokens, cost, latency, trace_id, release)
        G->>G: Prometheus counters + span attributes (gen_ai.*)
    end
```

Design points:

- **Transparent**: the body is forwarded byte for byte (except
  `stream_options.include_usage` for OpenAI streams), so new API features
  work without gateway changes. Unknown models are proxied but flagged
  `priced = false` and alerted on.
- **Usage normalization** ([gateway/usage.py](../gateway/usage.py)):
  `input_tokens` always means *uncached* input. Anthropic already reports it
  that way; OpenAI's `prompt_tokens` include cached tokens, which are
  subtracted.
- **402 instead of 429** for guardrails: the SDKs retry 429 automatically;
  a budget refusal must not be retried.
- **Recording never breaks a request**: failures to write telemetry are
  logged, the client still gets its response.

## 4. Agent run (AgentOps)

```mermaid
sequenceDiagram
    autonumber
    participant R as Runner (agent/runner.py)
    participant G as Gateway
    participant L as LLM
    participant T as Tools

    R->>G: POST /api/runs (run_id, task, model, trace_id)
    loop until answer, max steps, loop or guardrail
        R->>G: LLM call with X-Run-Id + traceparent
        G->>L: forward
        L-->>R: tool_use / end_turn / refusal
        R->>G: POST /api/runs/{id}/steps (llm)
        R->>T: search_docs / read_doc / calculator
        R->>G: POST /api/runs/{id}/steps (tool, ok, latency)
        Note over R: LoopDetector: same tool + same args ×3 → kill
    end
    R->>G: POST /api/runs/{id}/finish (status, stop_reason)
    Note over G: cost and tokens of the run<br/>= SUM(llm_requests WHERE run_id)
    R->>G: POST /api/evals (passed, score, cost)
```

One trace covers the whole run, across processes:

```
research-agent  invoke_agent research-agent
research-agent    anthropic.messages.create        (SDK span)
llm-gateway         POST /anthropic/{path}         (FastAPI server span)
llm-gateway           chat claude-opus-5-5         (tokens, cost)
research-agent    execute_tool search_docs
...
```

Guardrails, in order of who enforces them:

| Guardrail | Enforced by | Result |
|---|---|---|
| Max steps (`AGENT_MAX_STEPS`) | agent | `failed / max_steps` |
| Tool loop (`AGENT_LOOP_REPEAT_LIMIT`) | agent | `killed / loop_detected` |
| Cost per run (`MAX_RUN_COST_USD`) | **gateway** | `killed / run_cost_limit` |
| Monthly project budget (`config/budgets.yaml`) | **gateway** | `killed / budget_exceeded` |
| Model refusal | model; fallback model via `fallbacks: "default"` | `failed / refusal` |

Cost limits live in the gateway on purpose: a buggy agent can't bypass them.

**Quality** is measured by a deterministic eval suite
([agent/tasks.json](../agent/tasks.json), [agent/evals.py](../agent/evals.py)):
numeric and keyword checks, no LLM judge, so grading is free, reproducible and
safe to gate CI on ([ADR 0004](adr/0004-deterministic-evals-and-ci-gate.md)).

## 5. Governance: charters, guardrails and judges

```mermaid
flowchart LR
    req["LLM call<br/>X-Agent: research-agent"] --> charter{"Charter<br/>(config/agents.yaml)"}
    charter -- "unknown · paused ·<br/>model or tool not allowed" --> b1["403, always"]
    charter -- ok --> input{"Input guardrails<br/>PII · prompt injection"}
    input -- "hit + enforce" --> b2[403]
    input -- "hit + monitor" --> log[(guardrail_events)]
    input -- ok --> provider[Provider]
    provider --> output{"Output guardrails<br/>PII"}
    output -- "hit + enforce" --> b3[403]
    output -- ok --> client[Response]
    b1 --> log
    b2 --> log
    b3 --> log
    client -. "run finishes" .-> judge["Judge (sampled, async)<br/>on_purpose · grounded · helpful"]
    judge --> grades[(judgements)]
    grades --> console["Console review queue<br/>human 👍 / 👎"]
```

**Charter** ([config/agents.yaml](../config/agents.yaml)). Each agent declares
its purpose, the models and tools it may use, and an owner. The gateway checks
every request carrying `X-Agent` against it and always enforces it. Two
switches live in the database and are flipped from the console: `status`
(active / paused, a kill switch per agent) and `guardrail_mode`.

**Guardrails** ([gateway/governance.py](../gateway/governance.py)). Plain
regular expressions and phrase lists, applied to what is new in each turn
(the user's question or the tool results, so injected text inside a
document is caught too) and to the model's answer:

| Rule | Stage | Notes |
|---|---|---|
| `pii_email`, `pii_phone`, `pii_card` (Luhn-checked), `pii_iban` | input, output | Only the last 4 characters are stored |
| `prompt_injection` | input | Known phrases ("ignore previous instructions", ...) |

In `monitor` mode a hit is recorded and the call goes on; in `enforce` it is
refused with 403, which the agent reports as `killed / <rule>`. New guardrails
should start in `monitor`. Limits: output guardrails only see non-streaming
responses, and the detectors catch obvious cases, not a determined attacker
([ADR 0006](adr/0006-governance-in-the-gateway.md)).

**Judge** ([gateway/judge.py](../gateway/judge.py)). After a run finishes,
a sample of runs (`judge_sample_rate`) is graded in the background by a
model (Claude Sonnet 5.5, or GPT-4o mini with only an OpenAI key) on three
1-5 criteria, given the agent's purpose, the question, the tool outputs and
the answer. The judge is itself a registered agent and calls the model
through the gateway, so its cost is visible as the `judges` project.

**Human review** ([gateway/console.py](../gateway/console.py)). The console
lists judged runs with a low score or a guardrail hit; a person labels them
good or bad. *Judge / human agreement* (AgentOps dashboard and console) says
how far the judge can be trusted. The judge informs; the CI gate stays
deterministic.

## 6. Data model

```mermaid
erDiagram
    llm_requests {
        bigint id PK
        timestamptz ts
        text provider
        text model
        text project
        text run_id FK
        int input_tokens
        int output_tokens
        int cache_read_tokens
        int cache_write_tokens
        numeric cost_usd
        numeric cache_savings_usd
        bool priced
        int latency_ms
        int status_code
        text trace_id
        text release
        bool synthetic
    }
    agent_runs {
        text run_id PK
        text agent
        text project
        text task_id
        text status
        text stop_reason
        int steps
        int tool_calls
        bool loop_detected
        numeric cost_usd
        text trace_id
        text release
    }
    agent_steps {
        bigint id PK
        text run_id FK
        int step
        text kind
        text name
        bool ok
        int latency_ms
    }
    eval_results {
        bigint id PK
        text suite_id
        text task_id
        text run_id
        bool passed
        numeric score
        numeric cost_usd
        text release
    }
    deployments {
        bigint id PK
        timestamptz ts
        text version
        text git_commit
        text environment
        text notes
    }
    budgets {
        text project PK
        numeric monthly_usd
        numeric alert_at
    }
    agent_runs ||--o{ agent_steps : has
    agent_runs ||--o{ llm_requests : "spends via"
    agent_runs ||--o| eval_results : "graded as"
    budgets ||--o{ llm_requests : "limits (by project)"
    agents ||--o{ agent_runs : "governs"
    agent_runs ||--o| judgements : "graded by judge"
    agent_runs ||--o{ guardrail_events : "triggers"
```

Governance tables (`agents`, `guardrail_events`, `judgements`) are left out of
the diagram's columns for brevity; see [gateway/schema.sql](../gateway/schema.sql).

Every table carries `release`, or is joined to `deployments` by time, so any
metric can be split by version. Every table carries `synthetic`, so demo data
and real traffic never mix in budgets or alerts.

## 7. Release and deployment flow (DevOps)

```mermaid
flowchart LR
    pr[Pull request] --> q[CI: ruff · pytest ·<br/>dashboards in sync]
    q --> e2e["CI: e2e on mock provider<br/>stack up · agent suite --gate ·<br/>loop guardrail · all dashboard queries"]
    pr --> ev["Evals on the real model<br/>(if secrets): pass rate + cost gate"]
    e2e --> merge[merge to main]
    ev --> merge
    merge --> tag["git tag vX.Y.Z<br/>(= VERSION)"]
    tag --> rel["Release workflow:<br/>build with VERSION, commit, date<br/>→ ghcr.io · GitHub release"]
    rel --> deploy["docker compose up<br/>(gateway starts)"]
    deploy --> row["deployments row<br/>+ llmops_build_info metric"]
    row --> dash["Dashboards: Last release,<br/>release annotations on every chart"]
```

The gateway records itself in `deployments` when it starts with a
version/commit different from the last one. Every time-series panel shows
releases as annotations, which is what makes the FinOps and AgentOps charts
explainable: in the demo data, the cost per run drops at *0.3.0 prompt
caching* and runs get killed between *0.2.1* and its hotfix *0.2.2*.

CI never needs API keys: the e2e job runs the real gateway and the real agent
against [a mock provider](../tools/mock_llm.py) that speaks both APIs,
streaming included ([ADR 0005](adr/0005-mock-provider-for-ci.md)).

## 8. Dashboards

| Dashboard | Key panels |
|---|---|
| **Overview** (home) | Last release + when, spend month to date, tokens today, agent success rate, eval pass rate, spend and tokens per day, budget used, release history |
| **FinOps** | Spend, month-end forecast, prompt-cache savings, cost per successful run, unpriced requests; cost per day by model, spend by project / model, cache hit ratio, most expensive runs |
| **AgentOps** | Runs, success rate, killed runs, steps, tool error rate, eval pass rate; outcomes per day, stop reasons, tool reliability, recent runs with trace links; judge score, judge/human agreement, guardrail events; Tempo trace search |
| **Platform** | Running version and commit (from `/metrics`), uptime, deployment frequency, error rate, p95 latency, live RPS and latency histograms, guardrail rejections, release history |

All dashboards share two variables: **Data** (`all` / `real` / `demo`) and
**Project**.

## 9. Configuration as code

| File | Owns |
|---|---|
| `config/pricing.yaml` | Price per model (per 1M tokens: input, output, cache read, cache write) |
| `config/budgets.yaml` | Monthly budget and alert threshold per project |
| `config/agents.yaml` | Agent charters: purpose, allowed models and tools, judge sampling |
| `evals/baseline.json` | Minimum pass rate and reference cost per task, per model |
| `tools/build_dashboards.py` | The four Grafana dashboards (JSON is generated) |
| `observability/grafana/provisioning/` | Data sources, dashboard provider, alert rules |
| `VERSION` | Release version; the release tag must match it |

## 10. Production notes

Deliberately out of scope for a laptop setup, and what would change:

- **Collector**: put an OpenTelemetry Collector between services and Tempo
  (batching, sampling, fan-out); Tempo on object storage.
- **Write path**: the gateway writes one row per call synchronously. At
  high volume, batch inserts through a queue, and partition `llm_requests`
  by month.
- **Secrets**: provider keys from a secret manager, per-project virtual keys
  instead of the `/p/<project>` URL.
- **HA**: several gateway replicas behind a load balancer; budget checks are
  already stateless (they read Postgres).
- **Alert routing**: contact points (Slack, PagerDuty) for the provisioned rules.
- **Console access**: behind SSO, with the real user recorded on every switch
  and label; tool calls that need a human approval would go through it.

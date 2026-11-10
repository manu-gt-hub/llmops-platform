# Architecture

LLMOps Platform is one **Cloudflare Worker** on the free plan that runs an
LLM agent in production and answers, for that agent, the questions an LLMOps
team has to answer:

| Discipline | Question | Where the answer lives |
|---|---|---|
| **FinOps** | What does each call, run and day cost, and can it overspend? | `llm_calls` (D1), budgets in `config/agents.json`, AI Gateway analytics, `/admin` |
| **Scoring** | Are the answers right, and how do we know? | Deterministic eval suite (run on every release, red = rollback), LLM judge, human labels |
| **Guardrails** | What may the agent do, and what is refused? | Charter in `config/agents.json`, `src/governance.ts`, `guardrail_events` |
| **DevOps** | What is deployed, did it work, is it healthy? | CI, deploy workflow, `deployments`, `/version`, monitor workflow, Workers Observability |
| **DevSecOps** | Is the code, the supply chain and the live service safe? | npm audit, gitleaks, CodeQL, least-privilege token, admin auth, security headers |

Decisions and their trade-offs: [ADRs](adr/README.md) · Operating it: [Runbook](runbook.md)

## 1. Components

```mermaid
flowchart LR
    user([User]) -->|"/  chat UI"| assets[Static assets<br/>public/]
    user -->|"POST /api/ask"| worker
    visitor([Anyone]) -->|"/dashboard (read-only)"| worker
    admin([Operator]) -->|"/admin (password)"| worker
    subgraph cf[Cloudflare, free plan]
        assets
        worker["Worker (TypeScript)<br/>agent · guardrails · ledger · judge · admin"]
        worker <--> d1[("D1 (SQLite)<br/>calls, runs, steps, events,<br/>judgements, switches, releases")]
        worker -->|"every LLM call<br/>tagged agent + run"| gw[AI Gateway]
        gw --> wai[Workers AI<br/>Llama 3.3 70B]
        obs[Workers Observability<br/>logs, errors, latency]
        worker -.-> obs
    end
    gw -.->|optional, with secrets| paid[Anthropic · OpenAI]
    gh[GitHub Actions] -->|"deploy · live eval · monitor"| worker
```

| Component | Role | Why |
|---|---|---|
| Worker | Everything that is ours: the agent loop, the rules, the ledger, the judge, the admin pages | One deployable unit, no server to operate ([ADR 0001](adr/0001-cloudflare-worker.md)) |
| D1 | The ledger: one row per call, run, step, guardrail hit, grade and release | SQL for FinOps questions; budgets need exact sums the gateway does not enforce |
| AI Gateway | Per-request analytics, tokens, cost and logs in the Cloudflare dashboard, plus the place where judge scores and human feedback are attached to each request | Native, free, nothing to build ([ADR 0002](adr/0002-ai-gateway-and-own-ledger.md)) |
| Workers AI | The model (free daily allocation of 10,000 neurons) | No API key; Anthropic or OpenAI are a configuration change |
| Workers Observability | Worker logs, exceptions and latency | Enabled in `wrangler.jsonc`, no code |

## 2. A question, end to end

```mermaid
sequenceDiagram
    autonumber
    participant U as User
    participant W as Worker (agent.ts)
    participant DB as D1
    participant G as AI Gateway → Workers AI
    participant J as Judge (background)

    U->>W: POST /api/ask {question}
    Note over W: rate limit per IP · max 500 chars
    W->>DB: run row (question with PII redacted)
    loop until answer, max steps, or a rule fires
        Note over W: charter (paused? model? tools?)<br/>budgets (today, this run)<br/>input guardrails on what is new
        W->>G: LLM call (metadata: agent, run_id)
        G-->>W: tool calls or answer · usage incl. neurons
        W->>DB: llm_calls row (tokens, neurons, $, latency, gateway log id)
        W->>W: tools (search_docs, read_doc, calculator)
        W->>DB: steps (args, output)
    end
    Note over W: output guardrails on the answer
    W-->>U: answer, cost, run id
    W-)J: ctx.waitUntil(judge)
    J->>G: grade on_purpose · grounded · helpful (JSON)
    J->>DB: judgement
    J->>G: patchLog(score) on the run's last request
```

All the rules live in one function, `runAgent` in [src/agent.ts](../src/agent.ts):
there is no other code path to the model, so nothing can skip them.

## 3. FinOps

- **Exact cost per call.** Workers AI returns the neurons each call used.
  The ledger records their **list-price value**, neurons × $0.011 / 1,000;
  paid models are priced per token from [config/pricing.json](../config/pricing.json).
- **Two figures, never mixed:** *usage at list price* (what the platform
  would cost without the free tier; budgets are measured in it, so a paid
  provider behaves the same) and *billed* (only neurons beyond the 10,000
  free a day, plus paid providers). With the defaults, billed stays at $0.
- **Budgets that stop spending**, per agent, from
  [config/agents.json](../config/agents.json): `daily_budget_usd` and
  `max_run_cost_usd`. Checked before every call; a run that hits one ends
  as `killed / daily_budget` or `killed / run_cost_limit`. The defaults
  (copilot $0.08 + judge $0.02 a day ≈ 9,000 neurons) keep the whole
  platform inside the free allocation, so it is never billed.
- **Rate limit** of 10 questions per minute per IP protects the allocation
  from a single client.
- **Where to look:** `/dashboard` (public) and `/admin` (today's spend, share of the free allocation,
  cost per day, cost per run, budget used per agent) and the AI Gateway
  dashboard (requests, tokens, cost, logs per request).

## 4. Scoring

Three layers, from cheapest and most trusted to most informative:

| Layer | What | Blocks? |
|---|---|---|
| **Deterministic evals** | 8 questions about the platform, with answers checkable in its docs ([evaluation/tasks.json](../evaluation/tasks.json)): expected number or keywords. Run on the live Worker after every deploy; fails if the pass rate or the average cost per task breaks [evaluation/baseline.json](../evaluation/baseline.json) | Yes: the deploy run turns red |
| **LLM judge** | A sample of real runs graded 1-5 on *on purpose*, *grounded*, *helpful* ([src/judge.ts](../src/judge.ts)), in the background. Score also attached to the AI Gateway log of the request | No: informs and alerts (monitor) |
| **Human labels** | 👍 / 👎 on judged runs in `/admin`; *judge / human agreement* measures how far to trust the judge. Sent to AI Gateway as feedback | — |

What measuring on the real model (Llama 3.3 70B) taught:

- **With tools available it keeps calling them**, even when the results
  already hold the answer: the first copilot version passed 5 of 8, stopped
  by the loop guardrail. Reproduced in isolation: without tools it answered 3
  of 3. Fix: after a repeated call, and on the last allowed step, the next
  call is made **without tools** (`tool_choice: none` on paid providers), so
  a loop becomes an answer. Since then: 8 of 8, on two runs.
- **Retrieval quality is agent quality**: plain word overlap ranked long
  sections first; IDF weighting and a 5-letter stem fixed it.
- **Arithmetic and run-to-run variance remain**, so the gate asks for 75 %, not
  100 %. The judge (same model family) caught one wrong total and missed
  another: it is lenient on arithmetic, which is exactly why it informs and
  the deterministic suite gates.

## 5. Guardrails

| Rule | Kind | Mode |
|---|---|---|
| `agent_paused` | Operator pressed *pause* in `/admin` | always enforced |
| `model_not_allowed`, `tool_not_allowed`, `unknown_agent` | Charter in `config/agents.json` | always enforced |
| `daily_budget`, `run_cost_limit` | Budgets | always enforced |
| `loop_detected` | Same tool, same arguments, 3 times | always enforced |
| `pii_email`, `pii_phone`, `pii_card` (Luhn-checked), `pii_iban` | Personal data in the question, tool results or answer | `monitor` logs, `enforce` blocks |
| `prompt_injection` | Known phrases in the question or in tool results (indirect injection) | `monitor` / `enforce` |

Personal data is never stored: questions and answers are saved redacted and
events keep only the last four characters. The detectors are regular
expressions and a phrase list: predictable and readable, they catch accidents
rather than a determined attacker ([ADR 0004](adr/0004-guardrails-in-the-call-path.md)).
AI Gateway also offers Llama Guard based guardrails that can be switched on
in its dashboard as an extra layer.

## 6. DevOps

```mermaid
flowchart LR
    pr[Pull request] --> ci["CI: typecheck · tests (Worker end to end<br/>on SQLite + scripted AI) · build ·<br/>npm audit · gitleaks · CodeQL"]
    ci --> merge[merge to main]
    merge --> dep["Deploy: setup D1 · migrations ·<br/>wrangler deploy (version, commit) ·<br/>release row · admin secret"]
    dep --> smoke["Smoke test: health, version live,<br/>guardrail live, security headers"]
    smoke --> ev["Live evaluation:<br/>quality + cost gate"]
    cron["Every 6 h"] --> mon["Monitor: errors, free allocation,<br/>budgets, judge score → email on failure"]
```

- **Version and commit** are injected at deploy (`--var`), shown on
  `/version` and `/admin`, and every deploy adds a row to `deployments`.
- **Rollback:** `npx wrangler rollback` returns to the previous version.
- **Alerting:** the monitor workflow fails (and GitHub emails the owner)
  when the error rate passes 10 %, the free allocation or a budget passes
  80 %, or the judge score falls under 0.6.

## 7. DevSecOps

| Control | Where |
|---|---|
| Vulnerable dependencies fail CI (`npm audit --audit-level=high`) | `ci.yml` |
| Committed secrets fail CI (gitleaks, full history) | `ci.yml` |
| Static analysis (CodeQL, weekly and on PRs) | `codeql.yml` |
| Least-privilege deploy token; workflows with `contents: read` | `deploy.yml`, `.env.example` |
| Secrets only as Worker / GitHub secrets, never in the repo | `wrangler secret put` |
| Admin behind a password (constant-time check), off when unset; cross-site POSTs refused (CSRF) | `src/security.ts` |
| CSP without inline scripts, no framing, HSTS, nosniff; model output always escaped | `src/security.ts`, `public/_headers`, `public/app.js` |
| Input limits, rate limit, budgets (abuse = cost) | `src/index.ts`, `config/agents.json` |
| PII redacted at rest | `src/governance.ts` |
| Errors logged to Observability, never returned to the client | `src/index.ts` |

## 8. Configuration

| File | Owns |
|---|---|
| `wrangler.jsonc` | Bindings (AI, D1, assets, rate limit), provider and models |
| `config/agents.json` | Agent charters: purpose, models, tools, guardrail mode, judge sampling, budgets |
| `doc/` → `src/corpus.ts` | The copilot's knowledge base: these docs, bundled by `npm run corpus` (a test fails if stale) |
| `config/pricing.json` | Neuron price and free allocation; per-token prices of paid models |
| `evaluation/tasks.json`, `evaluation/baseline.json` | Eval suite and its gate |
| `migrations/` | D1 schema |
| `VERSION` | Release version shown live |

## 9. Dashboards

| Page | Who | Shows |
|---|---|---|
| `/dashboard` | anyone (EN / ES) | Aggregates only: cost, free allocation, calls, errors, success rate, judge score and agreement, guardrail hits per rule, agents and budgets, live evaluation per release (graded on the fly from `evaluation/tasks.json`), releases |
| `/admin` | operator (password) | The same, plus agent switches, recent runs with their (redacted) questions, run details and the review queue |
| AI Gateway, Workers Observability | account owner | Per-request logs, tokens, cost; Worker exceptions and latency |

The public page never shows text typed by users. Evaluation runs are the
exception that proves the rule: a request only counts as one when its
question is exactly one of the suite's, and the page reads the question from
the task file, not from the database.

## 10. What the free plan does not give us

- **No Grafana / Prometheus / Tempo.** Dashboards are `/dashboard` and `/admin` (ours) plus AI
  Gateway and Workers Observability (Cloudflare's); there are no custom
  distributed traces beyond what Workers Observability records.
- **AI Gateway Evaluations are deprecated**, so scoring is ours; the gateway
  keeps only the score and feedback we attach to each log.
- **No spend caps in AI Gateway**: budgets are enforced in the Worker.
- **The free allocation is per account.** The 10,000 neurons a day are shared by
  every Worker in the Cloudflare account (e.g. `rag_system`), and the platform
  only sees its own calls: the dashboard and the monitor show *this platform's*
  share. The account total is in the Cloudflare dashboard → Workers AI.
- **Workers AI cost is a valuation** (neurons × list price); within the free
  allocation nothing is billed, and there is no invoice to reconcile.
- **The judge's log annotation is best effort**: the binding exposes only the
  log id of its latest call, so under heavy concurrency it can attach to a
  neighbour's request.
- **No multi-user access control** on `/admin`: one shared password.
  Cloudflare Access (free up to 50 users) in front of `/admin/*` is the next step.

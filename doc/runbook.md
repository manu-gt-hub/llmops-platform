# Runbook

What to do when an alert fires. Alert rules:
[observability/grafana/provisioning/alerting/rules.yml](../observability/grafana/provisioning/alerting/rules.yml).
They evaluate real traffic only.

## Budget threshold

*Project budget above alert threshold* — month-to-date spend passed
`alert_at × monthly_usd` for a project.

1. FinOps dashboard, filter **Project**: is it one model, one agent, or a
   general increase? Check the *Most expensive agent runs* table.
2. Compare with the release annotations: did a deploy change prompts, model
   or caching?
3. If the spend is legitimate, raise `monthly_usd` in `config/budgets.yaml`
   (reviewed PR) and restart the gateway. If not, fix the cause; at 100 %
   the gateway answers **402 budget_exceeded** and the project stops.

## Unpriced model

*Requests for a model missing in pricing.yaml* — calls are proxied, but
their cost is recorded as $0, so the dashboards under-report spend.

1. Add the model to `config/pricing.yaml` with its price per 1M tokens.
2. Optionally recompute past rows:
   `UPDATE llm_requests SET cost_usd = …, priced = true WHERE model = '<id>'`.

## Gateway error rate

*Gateway error rate above 5%* for a provider over 15 minutes.

1. Platform dashboard → *Errors per day by status code* and live RPS.
   - `429` / `529`: provider rate limit or overload. Check provider status,
     reduce concurrency.
   - `401` / `403`: invalid or revoked key in `.env`.
   - `502`: the gateway cannot reach the provider (network, DNS).
   - `402`: our own guardrails, see budget threshold.
2. `docker compose logs gateway --tail 200`.

## Agent success rate

*Agent success rate below 70%* over 24 h.

1. AgentOps dashboard → *Stop reasons* and *Recent runs*.
   - `loop_detected`: the agent repeats a tool call; check the tool output
     it receives (trace link) and the prompt.
   - `run_cost_limit`: runs are hitting `MAX_RUN_COST_USD`; more steps or
     bigger context than before.
   - `refusal` / `max_tokens`: model-side; look at the trace.
2. Did it start at a release? Run the suite on the previous version:
   `docker compose run --rm agent --gate`.

## Agent misbehaving

Stop it first, investigate after: **console → Agents → ⏸ Pause**. Every call
of that agent gets 403 until it is resumed. Then:

1. Console → the agent → *Guardrail events* and *Recent runs*.
2. A burst of `prompt_injection`: look at the run's tool outputs; a document
   in the knowledge base may contain injected instructions.
3. Low judge scores: open a few runs, label them; if the labels disagree with
   the judge, the judge (or its rubric in `gateway/judge.py`) is the problem.

## Useful commands

```bash
docker compose ps                         # service health
curl localhost:8090/version               # what is running
curl localhost:8090/api/summary           # spend per project (real traffic)
docker compose exec db psql -U llmops     # SQL on the ledger
docker compose run --rm gateway python -m gateway.seed --clear   # remove demo data
```

# Runbook

## The monitor workflow failed

It runs every 6 hours and GitHub emails the owner when it fails. Its log says
which check failed:

| Problem | What to do |
|---|---|
| **Error rate > 10 %** | Cloudflare dashboard → Workers → llmops-platform → Observability for the exceptions; AI Gateway → Logs for failing model calls. `quota_exceeded` means the free allocation ran out |
| **Free allocation > 80 %** | Someone is using it a lot. `/admin` → Recent runs. Lower `daily_budget_usd` or the rate limit if it is abuse |
| **Agent budget > 80 %** | Same as above, for one agent. Raise the budget in `config/agents.json` (reviewed PR) only if the traffic is legitimate |
| **Judge score < 0.6** | `/admin` → Review queue: open the runs, label them. If humans disagree with the judge, the judge is wrong, not the agent |

## The live evaluation failed after a deploy

The deploy already happened. Look at the `evaluation-results` artifact of the
run: which tasks failed and why.

- Quality dropped because of the change → fix forward, or `npx wrangler rollback`.
- Cost rose on purpose (e.g. a paid provider) → raise `max_avg_cost_usd`
  in `evaluation/baseline.json` in the same PR.
- A single different task fails each time → model variance; re-run the job.

## The agent misbehaves

Stop it first, investigate after: `/admin` → Agents → **pause**. Every call
of that agent is refused (`agent_paused`) until it is resumed. Then open its
recent runs: steps, tool outputs and guardrail events are on each run page.

## Switch the model to Anthropic or OpenAI

```bash
npx wrangler secret put ANTHROPIC_API_KEY      # or OPENAI_API_KEY
npx wrangler secret put CF_AIG_TOKEN           # optional: show them in AI Gateway too
# wrangler.jsonc: "LLM_PROVIDER": "anthropic"
```

Then raise `daily_budget_usd` / `max_run_cost_usd` (config/agents.json) and
`max_avg_cost_usd` (evaluation/baseline.json): paid calls cost ~100× more.

## Useful commands

```bash
curl https://<worker>/version                                  # what is live
curl -u admin:$ADMIN_TOKEN https://<worker>/admin/api/summary  # today's numbers
npx wrangler tail                                              # live logs
npx wrangler rollback                                          # previous version
npx wrangler d1 execute llmops --remote --command "SELECT ..." # SQL on the ledger
```

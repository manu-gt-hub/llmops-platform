# AGENTS.md — LLMOps Platform

Guide for coding agents (and humans). Architecture and rationale:
`doc/architecture.md` and `doc/adr/`.

## Map

| Path | Role |
|---|---|
| `src/index.ts` | Routes: chat API, health, version, admin; rate limit, security headers |
| `src/agent.ts` | Agent loop; **the only path to the model**: charter, budgets, guardrails, ledger |
| `src/llm.ts` | Workers AI / Anthropic / OpenAI behind one interface, via AI Gateway |
| `src/governance.ts`, `config/agents.json` | Charters and content guardrails |
| `src/pricing.ts`, `config/pricing.json` | Cost per call |
| `src/ledger.ts`, `migrations/` | D1 reads and writes, schema |
| `src/judge.ts` | LLM-as-judge and human labels (also sent to AI Gateway) |
| `src/evals.ts`, `evaluation/` | Deterministic grading, eval suite, baseline, live evaluator |
| `src/admin.ts` | Public `/dashboard` (aggregates only) and `/admin` (server-rendered, no JS) |
| `src/security.ts` | Headers, admin auth, CSRF check |
| `src/tools.ts`, `src/corpus.ts` | The demo agent's tools and knowledge base |
| `public/` | Chat UI (static) |
| `test/` | vitest: units, and the Worker end to end on SQLite + scripted AI |

## Rules

- Every LLM call goes through `runAgent` (or the judge); never call the model elsewhere.
- Guardrails that limit money or what an agent may do are enforced in the
  Worker, before the call. New content guardrails start in `monitor`.
- New agent → `config/agents.json`. New paid model → `config/pricing.json`.
- Schema change → a new file in `migrations/` (never edit an applied one).
- Never store raw personal data: `redact()` before writing user or model text.
- `/dashboard` is public: aggregates and suite questions only, never user text or run links.
- Escape everything rendered in HTML (`esc()`); `public/app.js` uses `textContent` only.
- The entry module (`src/index.ts`) exports only the handler.
- A change meant to alter cost or quality updates `evaluation/baseline.json`.

## Checks

```bash
npm run typecheck && npm test && npx wrangler deploy --dry-run --outdir dist && npm audit --audit-level=high
```

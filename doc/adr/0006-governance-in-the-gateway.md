# 0006 — Agent governance in the gateway: charters, simple guardrails, sampled judge

**Status:** Accepted

## Context

Agents need a declared purpose and limits (which models and tools, a way to
stop them), checks on what goes in and out (personal data, prompt
injection), and a measure of answer quality on real traffic, not only on the
eval suite. Options range from dedicated products (guardrail frameworks,
LLM-observability platforms with evaluators) to code in each agent.

## Decision

- **Charters in `config/agents.yaml`, enforced by the gateway.** Same reason
  as the cost limits: a buggy or compromised agent cannot skip a rule that
  lives outside it. The operational switches (pause, guardrail mode) live in
  the database so the console can flip them without a deploy.
- **Guardrails are regular expressions and phrase lists** with a `monitor` /
  `enforce` mode per agent. They are cheap, deterministic and readable in a
  few minutes. They catch accidents (an e-mail pasted in a question, a
  document saying "ignore previous instructions"), not determined attackers.
- **The judge is an LLM, sampled and asynchronous.** It never adds latency
  to the agent and never blocks a merge (the CI gate stays deterministic,
  ADR 0004). It runs through the gateway as a registered agent, so it obeys
  the same rules and its cost is visible.
- **Humans label judged runs in the console**, and the judge/human agreement
  is a first-class metric: a judge is only trusted as far as it agrees with
  people.
- **The console is server-rendered HTML inside the gateway** (Jinja, plain
  forms, no JavaScript build), so the whole stack stays one language and
  readable end to end.

## Consequences

- ➕ One place to answer "what may this agent do, did it, and was it good?".
- ➕ Everything is inspectable: the rules are ~150 lines, the judge prompt is
  one string.
- ➖ Detection quality is basic. Swapping a detector for a model-based
  classifier (e.g. a small LLM call for injection) fits behind the same
  `scan()` function.
- ➖ Output guardrails do not inspect streamed responses.
- ➖ No tool-level human approval (the agent would have to wait on the
  console); a natural next step.
- ➖ The console has no authentication; it is a local tool.

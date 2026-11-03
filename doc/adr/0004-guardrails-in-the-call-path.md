# 0004 — Charter, budgets and guardrails in the single LLM call path, with simple detectors

## Context

The agent must stay within a declared purpose, use only its models and
tools, be stoppable by an operator, and not leak or store personal data or
follow injected instructions.

## Decision

- `config/agents.json` declares each agent's purpose, models, tools,
  budgets and guardrail mode. `/admin` can pause an agent and switch its mode.
- `runAgent` (src/agent.ts) is the only path to the model and checks, before
  every call: charter, budgets, and content guardrails on what is new (the
  question, then each tool result); after the last call, the answer.
- Detectors are regular expressions (e-mail, phone, Luhn-checked card, IBAN)
  and a phrase list for prompt injection. `monitor` records, `enforce` blocks.
- Questions and answers are stored redacted.

## Consequences

- ➕ Readable in minutes, deterministic, testable, no extra cost or latency.
- ➖ Catches accidents, not a determined attacker. A model-based classifier
  (e.g. AI Gateway's Llama Guard guardrails) can be added in front without
  changing this design.
- ➖ Output guardrails see the final answer only (no streaming, by design).

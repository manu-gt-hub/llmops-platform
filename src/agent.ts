/**
 * agent.ts — The research agent loop. Every LLM call goes through `guardedStep`,
 * the single place where the platform's rules are applied:
 *
 *   before the call   charter (paused, model, tools) · daily and per-run budget
 *                     · input guardrails on what is new (question, tool results)
 *   after the call    ledger row (tokens, neurons, cost, latency, AI Gateway log)
 *   final answer      output guardrails
 *
 * A rule that fires ends the run as `killed` with the rule as stop reason
 * (content guardrails only when the agent is in `enforce` mode).
 */

import type { Env } from "./env";
import { CHARTERS, charterViolations, type Finding, redact, scan, type Switches } from "./governance";
import { type Conversation, newConversation, QuotaExceededError, type ToolOutcome } from "./llm";
import { addStep, finishRun, getSwitches, recordCall, recordEvents, spent, startRun } from "./ledger";
import { costUsd } from "./pricing";
import { runTool, TOOL_SPECS, ToolError } from "./tools";

export const AGENT = "research-agent";

export const SYSTEM_PROMPT =
  "You are a research assistant for Acme Cloud employees. Answer questions using only the Acme Cloud " +
  "handbook, which you can query with your tools. Look up every fact you rely on. For any arithmetic, " +
  "check the units of each figure (per seat, per month, per night, per day...) and pass every factor in a " +
  "single calculator expression. When you have the answer, reply with one short paragraph that states the final " +
  "figure or name explicitly. If the question is not about Acme Cloud, or the handbook does not contain " +
  "the answer, say so.";

export interface RunResult {
  runId: string;
  status: "success" | "failed" | "killed" | "error";
  stopReason: string;
  answer: string | null;
  steps: number;
  toolCalls: number;
  costUsd: number;
}

/** Stops the run with a stop reason (a rule, a budget or an error). */
class Stop extends Error {
  constructor(public status: RunResult["status"], public reason: string) {
    super(reason);
  }
}

/** Same tool with the same arguments this many times = a loop. */
export class LoopDetector {
  private seen = new Map<string, number>();
  constructor(private limit = 3) {}
  check(name: string, args: Record<string, unknown>): boolean {
    const key = `${name}:${JSON.stringify(args, Object.keys(args).sort())}`;
    const count = (this.seen.get(key) ?? 0) + 1;
    this.seen.set(key, count);
    return count >= this.limit;
  }
}

/** Apply a content-guardrail result: always recorded, blocks in enforce mode. */
async function guard(env: Env, runId: string, sw: Switches, findings: Finding[]) {
  if (!findings.length) return;
  const action = sw.guardrail_mode === "enforce" ? "blocked" : "flagged";
  await recordEvents(env, AGENT, runId, findings, action);
  if (action === "blocked") throw new Stop("killed", findings[0].rule);
}

export async function runAgent(
  env: Env,
  question: string,
  opts: { source: "chat" | "eval"; taskId?: string | null; conversation?: Conversation },
): Promise<RunResult> {
  const runId = `run-${crypto.randomUUID().slice(0, 12)}`;
  const charter = CHARTERS[AGENT];
  const conversation = opts.conversation ?? newConversation(env, SYSTEM_PROMPT, question, TOOL_SPECS);
  const sw = await getSwitches(env, AGENT);
  await startRun(env, { runId, agent: AGENT, source: opts.source, taskId: opts.taskId ?? null,
    model: conversation.model, question: redact(question) });

  const loops = new LoopDetector();
  let steps = 0;
  let toolCalls = 0;
  let answer: string | null = null;
  let status: RunResult["status"] = "failed";
  let stopReason = "max_steps";
  let newInput = [question];

  try {
    for (let i = 0; i < charter.max_steps; i++) {
      // --- before the call: charter, budgets, input guardrails -------------
      const violations = charterViolations(AGENT, sw, conversation.model, TOOL_SPECS.map((t) => t.name));
      if (violations.length) {
        await recordEvents(env, AGENT, runId, violations, "blocked");
        throw new Stop("killed", violations[0].rule);
      }
      const money = await spent(env, AGENT, runId);
      const overBudget: Finding | null =
        money.today >= charter.daily_budget_usd
          ? { stage: "budget", rule: "daily_budget", detail: `$${money.today.toFixed(4)} spent today` }
          : money.run >= charter.max_run_cost_usd
            ? { stage: "budget", rule: "run_cost_limit", detail: `$${money.run.toFixed(4)} spent in this run` }
            : null;
      if (overBudget) {
        await recordEvents(env, AGENT, runId, [overBudget], "blocked");
        throw new Stop("killed", overBudget.rule);
      }
      await guard(env, runId, sw, newInput.flatMap((text) => scan("input", text)));

      // --- the call -----------------------------------------------------------
      steps++;
      const started = Date.now();
      let turn;
      try {
        turn = await conversation.step({ agent: AGENT, run_id: runId, source: opts.source });
      } catch (e) {
        const latencyMs = Date.now() - started;
        await recordCall(env, { agent: AGENT, run_id: runId, provider: conversation.provider, model: conversation.model,
          input_tokens: 0, output_tokens: 0, neurons: 0, cost_usd: 0, latency_ms: latencyMs, ok: 0,
          error: String(e).slice(0, 300), gateway_log_id: null });
        await addStep(env, { runId, step: steps, kind: "llm", name: conversation.model, ok: false, latencyMs, detail: String(e) });
        throw new Stop("error", e instanceof QuotaExceededError ? "quota_exceeded" : "llm_error");
      }
      const latencyMs = Date.now() - started;
      await recordCall(env, { agent: AGENT, run_id: runId, provider: conversation.provider, model: turn.model,
        input_tokens: turn.usage.inputTokens, output_tokens: turn.usage.outputTokens, neurons: turn.usage.neurons ?? 0,
        cost_usd: costUsd(conversation.provider, turn.model, turn.usage) ?? 0, latency_ms: latencyMs, ok: 1, error: null,
        gateway_log_id: turn.gatewayLogId });
      await addStep(env, { runId, step: steps, kind: "llm", name: turn.model, ok: true, latencyMs, detail: turn.stop });

      // --- final answer: output guardrails ------------------------------------
      if (!turn.toolCalls.length) {
        await guard(env, runId, sw, scan("output", turn.text));
        answer = turn.text.trim() || null;
        status = turn.stop === "end_turn" && answer ? "success" : "failed";
        stopReason = turn.stop || "no_answer";
        break;
      }

      // --- tools ----------------------------------------------------------------
      const outcomes: ToolOutcome[] = [];
      let loop = false;
      for (const call of turn.toolCalls) {
        toolCalls++;
        loop ||= loops.check(call.name, call.args);
        const t0 = Date.now();
        let outcome: ToolOutcome;
        try {
          outcome = { call, output: runTool(call.name, call.args), isError: false };
        } catch (e) {
          if (!(e instanceof ToolError)) throw e;
          outcome = { call, output: e.message, isError: true };
        }
        outcomes.push(outcome);
        await addStep(env, { runId, step: steps, kind: "tool", name: call.name, ok: !outcome.isError,
          latencyMs: Date.now() - t0, detail: JSON.stringify(call.args), output: outcome.output });
      }
      if (loop) {
        await recordEvents(env, AGENT, runId, [{ stage: "charter", rule: "loop_detected", detail: "same tool call repeated" }], "blocked");
        throw new Stop("killed", "loop_detected");
      }
      conversation.addResults(outcomes);
      newInput = outcomes.map((o) => o.output); // tool results are the next input to check
    }
  } catch (e) {
    if (!(e instanceof Stop)) throw e;
    status = e.status;
    stopReason = e.reason;
    answer = null;
  }

  const cost = await finishRun(env, { runId, status, stopReason, answer: answer && redact(answer), steps, toolCalls });
  return { runId, status, stopReason, answer, steps, toolCalls, costUsd: cost };
}

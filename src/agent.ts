/**
 * agent.ts — The platform copilot loop. Every LLM call goes through `runAgent`,
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

export const AGENT = "platform-copilot";

export const SYSTEM_PROMPT =
  "You are the copilot of an LLMOps platform: a live LLM agent on Cloudflare with FinOps, scoring, guardrails, " +
  "DevOps and security around it. Answer questions about how the platform works, why it was designed that way, " +
  "how to operate it, and how it is doing right now. Use search_docs or read_doc for how and why, and " +
  "get_live_metrics for current numbers; never guess a figure. For any arithmetic, check the units of each figure " +
  "and pass every factor in a single calculator expression. Reply with one short paragraph that states the answer " +
  "explicitly. Always reply in the language the user wrote in (for example, in Spanish if the question is in " +
  "Spanish), even though the documentation is in English. If the question is not about the platform, say so.";

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
  /** "ok", "warn" (one repeat left: the model is told) or "loop" (stop the run). */
  check(name: string, args: Record<string, unknown>): "ok" | "warn" | "loop" {
    const key = `${name}:${JSON.stringify(args, Object.keys(args).sort())}`;
    const count = (this.seen.get(key) ?? 0) + 1;
    this.seen.set(key, count);
    return count >= this.limit ? "loop" : count === this.limit - 1 ? "warn" : "ok";
  }
}

const REPEAT_WARNING = "\n\n[platform] You already made this exact call. Answer now with the results you have.";

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
  // Measured on Llama 3.3: with tools available it keeps calling them even when
  // the results already hold the answer. After a repeated call, and on the last
  // allowed step, the next call is made without tools so the model must answer.
  let finalAnswer = false;

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
        turn = await conversation.step({ agent: AGENT, run_id: runId, source: opts.source },
          finalAnswer || i === charter.max_steps - 1);
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
        const repeat = loops.check(call.name, call.args);
        loop ||= repeat === "loop";
        const t0 = Date.now();
        let outcome: ToolOutcome;
        try {
          outcome = { call, output: await runTool(env, call.name, call.args), isError: false };
        } catch (e) {
          if (!(e instanceof ToolError)) throw e;
          outcome = { call, output: e.message, isError: true };
        }
        if (repeat === "warn") {
          outcome.output += REPEAT_WARNING;
          finalAnswer = true;
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

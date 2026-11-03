/**
 * judge.ts — LLM-as-judge: grades finished runs against the agent's purpose.
 *
 * After a run answers, a sample of runs (judge_sample_rate in
 * config/agents.json) is graded in the background (ctx.waitUntil, no latency
 * for the user) on three 1-5 criteria:
 *
 *   on_purpose  the agent stayed within its stated purpose
 *   grounded    every claim in the answer is supported by the tool outputs
 *   helpful     the answer resolves the question
 *
 * The judge runs on Workers AI in JSON mode, is itself a registered agent with
 * its own daily budget, and its score is also attached to the run's last
 * AI Gateway log (patchLog), so it shows next to the request in Cloudflare.
 * Humans can label judged runs in /admin; judge/human agreement says how far
 * the judge can be trusted. The CI gate does not use the judge (evals.ts does).
 */

import type { Env } from "./env";
import { CHARTERS } from "./governance";
import { recordCall, spent } from "./ledger";
import { costUsd } from "./pricing";

export const PASS_SCORE = 0.7;
const CRITERIA = ["on_purpose", "grounded", "helpful"] as const;

const VERDICT_SCHEMA = {
  type: "object",
  properties: {
    on_purpose: { type: "integer" },
    grounded: { type: "integer" },
    helpful: { type: "integer" },
    reasoning: { type: "string" },
  },
  required: ["on_purpose", "grounded", "helpful", "reasoning"],
};

const INSTRUCTIONS = `You review the work of an AI agent. Grade the run below from 1 (very poor) to 5 (excellent) on:

- on_purpose: the agent stayed within its stated purpose. Answering an unrelated question, or giving advice the purpose forbids, is a 1. Politely declining an out-of-scope question is a 5.
- grounded: every fact and figure in the answer is supported by the tool outputs. Invented facts are a 1. If the answer makes no factual claims, give a 5.
- helpful: the answer resolves the question clearly and states the final figure or name.

Be strict and consistent. Explain your grades in two or three sentences in "reasoning". Reply with JSON only.`;

export function buildPrompt(purpose: string, question: string, toolOutputs: string[], answer: string): string {
  const tools = toolOutputs.map((o, i) => `[${i + 1}] ${o}`).join("\n\n") || "(the agent used no tools)";
  return `${INSTRUCTIONS}\n\n<agent_purpose>\n${purpose}\n</agent_purpose>\n\n<question>\n${question}\n</question>\n\n` +
    `<tool_outputs>\n${tools}\n</tool_outputs>\n\n<answer>\n${answer || "(no answer)"}\n</answer>`;
}

export interface Verdict {
  on_purpose: number;
  grounded: number;
  helpful: number;
  score: number; // 0-1
  reasoning: string;
}

/** Clamp the grades to 1-5 and scale their mean to 0-1. */
export function parseVerdict(raw: unknown): Verdict {
  const v = (typeof raw === "string" ? JSON.parse(raw.slice(raw.indexOf("{"), raw.lastIndexOf("}") + 1)) : raw) as Record<string, unknown>;
  const grade = (k: string) => Math.min(5, Math.max(1, Math.round(Number(v[k]) || 1)));
  const [on_purpose, grounded, helpful] = CRITERIA.map(grade);
  const score = Math.round((((on_purpose + grounded + helpful) / 3 - 1) / 4) * 10000) / 10000;
  return { on_purpose, grounded, helpful, score, reasoning: String(v.reasoning ?? "").slice(0, 2000) };
}

export async function judgeRun(env: Env, runId: string): Promise<Verdict | null> {
  const charter = CHARTERS.judge;
  try {
    const run = await env.DB.prepare("SELECT agent, question, answer FROM runs WHERE run_id = ?")
      .bind(runId).first<{ agent: string; question: string; answer: string | null }>();
    if (!run?.answer) return null;
    if ((await spent(env, "judge", runId)).today >= charter.daily_budget_usd) return null;
    const steps = await env.DB.prepare("SELECT output FROM steps WHERE run_id = ? AND kind = 'tool' AND output IS NOT NULL ORDER BY id")
      .bind(runId).all<{ output: string }>();
    const prompt = buildPrompt(CHARTERS[run.agent]?.purpose ?? "(unknown)", run.question,
      steps.results.map((s) => s.output), run.answer);

    const started = Date.now();
    const out = (await env.AI.run(
      env.JUDGE_MODEL as keyof AiModels,
      { messages: [{ role: "user", content: prompt }], response_format: { type: "json_schema", json_schema: VERDICT_SCHEMA },
        max_tokens: 400 } as never,
      { gateway: { id: env.AI_GATEWAY_ID, metadata: { agent: "judge", run_id: runId } } },
    )) as { response?: unknown; usage?: { prompt_tokens?: number; completion_tokens?: number; neurons?: number } };
    const usage = { inputTokens: out.usage?.prompt_tokens ?? 0, outputTokens: out.usage?.completion_tokens ?? 0,
      neurons: out.usage?.neurons ?? 0 };
    await recordCall(env, { agent: "judge", run_id: runId, provider: "workers-ai", model: env.JUDGE_MODEL,
      input_tokens: usage.inputTokens, output_tokens: usage.outputTokens, neurons: usage.neurons,
      cost_usd: costUsd("workers-ai", env.JUDGE_MODEL, usage) ?? 0, latency_ms: Date.now() - started, ok: 1, error: null,
      gateway_log_id: env.AI.aiGatewayLogId ?? null });

    const verdict = parseVerdict(out.response);
    await env.DB.prepare(
      `INSERT OR REPLACE INTO judgements (run_id, judge_model, on_purpose, grounded, helpful, score, reasoning)
       VALUES (?, ?, ?, ?, ?, ?, ?)`,
    ).bind(runId, env.JUDGE_MODEL, verdict.on_purpose, verdict.grounded, verdict.helpful, verdict.score, verdict.reasoning).run();
    await annotateGatewayLog(env, runId, { score: Math.round(verdict.score * 100) });
    return verdict;
  } catch (e) {
    console.error(`judge failed for ${runId}: ${e}`); // the run simply stays unjudged
    return null;
  }
}

/** A human's verdict on a judged run; also sent to AI Gateway as feedback. */
export async function setLabel(env: Env, runId: string, label: "good" | "bad"): Promise<void> {
  await env.DB.prepare("UPDATE judgements SET human_label = ? WHERE run_id = ?").bind(label, runId).run();
  await annotateGatewayLog(env, runId, { feedback: label === "good" ? 1 : -1 });
}

/** Best effort: attach score / feedback to the AI Gateway log of the run's
 * last agent call (only Workers AI calls carry a log id). */
async function annotateGatewayLog(env: Env, runId: string, data: { score?: number; feedback?: 1 | -1 }) {
  const row = await env.DB.prepare(
    "SELECT gateway_log_id FROM llm_calls WHERE run_id = ? AND agent <> 'judge' AND gateway_log_id IS NOT NULL ORDER BY id DESC LIMIT 1",
  ).bind(runId).first<{ gateway_log_id: string }>();
  if (!row) return;
  try {
    await env.AI.gateway(env.AI_GATEWAY_ID).patchLog(row.gateway_log_id, data);
  } catch (e) {
    console.warn(`could not annotate AI Gateway log ${row.gateway_log_id}: ${e}`);
  }
}

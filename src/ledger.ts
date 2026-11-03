/**
 * ledger.ts — Reads and writes of the D1 tables (migrations/0001_init.sql).
 */

import type { Env } from "./env";
import { grade, TASKS } from "./evals";
import { CHARTERS, type Finding, type Switches } from "./governance";
import { FREE_NEURONS_PER_DAY, neuronsToUsd } from "./pricing";

type Row = Record<string, unknown>;
const first = async (env: Env, sql: string, ...args: unknown[]): Promise<Row> =>
  (await env.DB.prepare(sql).bind(...args).first<Row>()) ?? {};

export async function getSwitches(env: Env, agent: string): Promise<Switches> {
  const row = await env.DB.prepare("SELECT status, guardrail_mode FROM agent_switches WHERE agent = ?")
    .bind(agent)
    .first<Switches>();
  return row ?? { status: "active", guardrail_mode: CHARTERS[agent]?.guardrail_mode ?? "monitor" };
}

export async function setSwitches(env: Env, agent: string, sw: Switches): Promise<void> {
  await env.DB.prepare(
    `INSERT INTO agent_switches (agent, status, guardrail_mode) VALUES (?, ?, ?)
     ON CONFLICT (agent) DO UPDATE SET status = excluded.status, guardrail_mode = excluded.guardrail_mode,
       updated_at = datetime('now')`,
  )
    .bind(agent, sw.status, sw.guardrail_mode)
    .run();
}

/** Spend of today (UTC) for an agent, and of one run. */
export async function spent(env: Env, agent: string, runId: string): Promise<{ today: number; run: number }> {
  const row = await env.DB.prepare(
    `SELECT COALESCE(SUM(CASE WHEN ts >= date('now') THEN cost_usd END), 0) AS today,
            COALESCE(SUM(CASE WHEN run_id = ? THEN cost_usd END), 0) AS run
     FROM llm_calls WHERE agent = ? AND (ts >= date('now') OR run_id = ?)`,
  )
    .bind(runId, agent, runId)
    .first<{ today: number; run: number }>();
  return row ?? { today: 0, run: 0 };
}

export interface CallRow {
  agent: string;
  run_id: string;
  provider: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  neurons: number;
  cost_usd: number;
  latency_ms: number;
  ok: number;
  error: string | null;
  gateway_log_id: string | null;
}

export async function recordCall(env: Env, c: CallRow): Promise<void> {
  await env.DB.prepare(
    `INSERT INTO llm_calls (agent, run_id, provider, model, input_tokens, output_tokens, neurons, cost_usd,
       latency_ms, ok, error, gateway_log_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
  )
    .bind(c.agent, c.run_id, c.provider, c.model, c.input_tokens, c.output_tokens, c.neurons, c.cost_usd,
      c.latency_ms, c.ok, c.error, c.gateway_log_id)
    .run();
}

export async function recordEvents(env: Env, agent: string, runId: string | null, findings: Finding[], action: string) {
  if (!findings.length) return;
  await env.DB.batch(
    findings.map((f) =>
      env.DB.prepare("INSERT INTO guardrail_events (agent, run_id, stage, rule, action, detail) VALUES (?, ?, ?, ?, ?, ?)")
        .bind(agent, runId, f.stage, f.rule, action, f.detail.slice(0, 300)),
    ),
  );
}

export async function startRun(env: Env, r: { runId: string; agent: string; source: string; taskId: string | null;
  model: string; question: string }) {
  await env.DB.prepare(
    "INSERT INTO runs (run_id, agent, source, task_id, model, question, version) VALUES (?, ?, ?, ?, ?, ?, ?)",
  )
    .bind(r.runId, r.agent, r.source, r.taskId, r.model, r.question, env.APP_VERSION)
    .run();
}

export async function addStep(env: Env, s: { runId: string; step: number; kind: string; name: string; ok: boolean;
  latencyMs: number; detail?: string | null; output?: string | null }) {
  await env.DB.prepare(
    "INSERT INTO steps (run_id, step, kind, name, ok, latency_ms, detail, output) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
  )
    .bind(s.runId, s.step, s.kind, s.name, s.ok ? 1 : 0, s.latencyMs, s.detail?.slice(0, 500) ?? null,
      s.output?.slice(0, 4000) ?? null)
    .run();
}

export async function finishRun(env: Env, r: { runId: string; status: string; stopReason: string; answer: string | null;
  steps: number; toolCalls: number }): Promise<number> {
  await env.DB.prepare(
    `UPDATE runs SET status = ?, stop_reason = ?, answer = ?, steps = ?, tool_calls = ?,
       cost_usd = (SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls WHERE run_id = ?)
     WHERE run_id = ?`,
  )
    .bind(r.status, r.stopReason, r.answer, r.steps, r.toolCalls, r.runId, r.runId)
    .run();
  const row = await env.DB.prepare("SELECT cost_usd FROM runs WHERE run_id = ?").bind(r.runId).first<{ cost_usd: number }>();
  return row?.cost_usd ?? 0;
}

// ---------------------------------------------------------------------------
// Read models shared by the dashboards, the monitor and the copilot's tool
// ---------------------------------------------------------------------------
/** Numbers the monitor workflow alerts on.
 *
 * Two different money figures:
 *   value_usd   usage at list price (Workers AI neurons × $0.011 / 1,000, plus
 *               paid providers): what budgets are measured in
 *   billed_usd  what is actually billed: only neurons beyond the free daily
 *               allocation, plus paid providers (billed by them) */
export async function summary(env: Env) {
  const today = await first(env, `SELECT COUNT(*) AS calls, COALESCE(SUM(cost_usd),0) AS value_usd,
    COALESCE(SUM(neurons),0) AS neurons, COALESCE(SUM(CASE WHEN provider <> 'workers-ai' THEN cost_usd END),0) AS paid_usd,
    COALESCE(AVG(1 - ok), 0) AS error_rate FROM llm_calls WHERE ts >= date('now')`);
  const neurons = Number(today.neurons);
  const billed = neuronsToUsd(Math.max(0, neurons - FREE_NEURONS_PER_DAY)) + Number(today.paid_usd);
  const judge = await first(env, "SELECT AVG(score) AS judge_score, COUNT(*) AS judged FROM judgements WHERE ts >= datetime('now', '-1 day')");
  const agents = await Promise.all(Object.entries(CHARTERS).map(async ([name, c]) => {
    const spentToday = await first(env, "SELECT COALESCE(SUM(cost_usd),0) AS v FROM llm_calls WHERE agent = ? AND ts >= date('now')", name);
    return { name, ...(await getSwitches(env, name)), budget_used: Number(spentToday.v) / c.daily_budget_usd };
  }));
  return {
    version: env.APP_VERSION, commit: env.GIT_COMMIT,
    today: { ...today, billed_usd: billed, free_neurons_used: neurons / FREE_NEURONS_PER_DAY } as Row,
    judge_24h: judge,
    agents,
  };
}


/** Evaluation runs (newest first), graded against evaluation/tasks.json. A run
 * counts only if it asked the task's current question: when the suite changes,
 * runs of an older suite are not graded against the new answers. */
export async function evaluationRuns(env: Env): Promise<(Row & { passed: boolean })[]> {
  const runs = (await env.DB.prepare(
    `SELECT r.version, r.task_id, r.question, r.answer, r.status, r.stop_reason, r.cost_usd, r.ts, j.score
     FROM runs r LEFT JOIN judgements j USING (run_id) WHERE r.source = 'eval' ORDER BY r.ts DESC LIMIT 200`,
  ).all<Row>()).results;
  return runs
    .filter((r) => TASKS[String(r.task_id)]?.question === r.question)
    .map((r) => ({ ...r, passed: grade(TASKS[String(r.task_id)], (r.answer as string | null) ?? null).passed }));
}
